use std::{
    str::FromStr as _,
    sync::{Arc, Mutex},
    time::Instant,
};

use http::{HeaderMap, HeaderName, HeaderValue};
use pyo3::{
    exceptions::PyValueError,
    pyclass, pymethods,
    sync::{MutexExt as _, PyOnceLock},
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyFloat, PyInt, PyString, PyTuple,
        PyTypeMethods as _,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::runtime::RuntimeMetrics;

use crate::shared::{constants::Constants, request::RequestHead};

struct InstrumentationInner {
    tracer: Py<PyAny>,

    metric_http_client_active_requests: Py<PyAny>,
    metric_http_client_request_duration: Py<PyAny>,

    constants: Constants,
}

#[derive(Clone)]
pub(crate) struct Instrumentation {
    inner: Option<Arc<InstrumentationInner>>,
}

impl Instrumentation {
    pub(crate) fn new(
        py: Python<'_>,
        enable_otel: bool,
        meter_provider: Option<Bound<'_, PyAny>>,
        tracer_provider: Option<Bound<'_, PyAny>>,
        constants: &Constants,
    ) -> PyResult<Self> {
        if !enable_otel {
            return Ok(Self { inner: None });
        }
        let meter_provider = match meter_provider {
            Some(mp) => mp,
            None => constants.get_meter_provider.bind(py).call0()?,
        };
        let tracer_provider = match tracer_provider {
            Some(tp) => tp,
            None => constants.get_tracer_provider.bind(py).call0()?,
        };
        let meter = meter_provider.call_method1(&constants.get_meter, (&constants.pyqwest,))?;
        let tracer = tracer_provider.call_method1(&constants.get_tracer, (&constants.pyqwest,))?;

        start_runtime_metrics(py, &meter, constants)?;

        let kwargs = PyDict::new(py);
        kwargs.set_item(
            &constants.explicit_bucket_boundaries_advisory,
            &constants.http_client_request_duration_buckets,
        )?;
        let metric_http_client_request_duration = meter.call_method(
            &constants.create_histogram,
            (
                &constants.http_client_request_duration,
                &constants.otel_s,
                &constants.http_client_request_duration_description,
            ),
            Some(&kwargs),
        )?;

        let metric_http_client_active_requests = meter.call_method1(
            &constants.create_up_down_counter,
            (
                &constants.http_client_active_requests,
                &constants.otel_request,
                &constants.http_client_active_requests_description,
            ),
        )?;

        Ok(Self {
            inner: Some(Arc::new(InstrumentationInner {
                tracer: tracer.unbind(),
                metric_http_client_active_requests: metric_http_client_active_requests.unbind(),
                metric_http_client_request_duration: metric_http_client_request_duration.unbind(),
                constants: constants.clone(),
            })),
        })
    }

    pub(crate) fn start(&self, py: Python<'_>, request: &RequestHead) -> PyResult<Operation> {
        let Some(inner) = self.inner.as_ref() else {
            return Ok(Operation { inner: None });
        };
        let http_method = request.method(py)?;
        let base_attrs = PyDict::new(py);
        // We require a host to send requests so any validated request will have a host.
        let host = request.parsed_url().host_str().unwrap_or_default();
        let port = request
            .parsed_url()
            .port_or_known_default()
            // We only support schemes with a known default port.
            .unwrap_or_default();

        base_attrs.set_item(&inner.constants.http_request_method, &http_method)?;
        base_attrs.set_item(&inner.constants.server_address, host)?;
        base_attrs.set_item(&inner.constants.server_port, port)?;

        let span_attrs = base_attrs.copy()?;
        span_attrs.set_item(
            &inner.constants.network_protocol_name,
            &inner.constants.http,
        )?;
        span_attrs.set_item(&inner.constants.url_full, request.url())?;

        // Because we are wrapping Rust code, we have no use case for setting the current span,
        // which luckily simplifies the asyncio path substantially.
        let span = inner.tracer.bind(py).call_method1(
            &inner.constants.start_span,
            (
                http_method,
                py.None(),
                &inner.constants.span_kind_client,
                span_attrs,
            ),
        )?;
        let context = inner.constants.set_span_in_context.call1(py, (&span,))?;

        inner.metric_http_client_active_requests.call_method1(
            py,
            &inner.constants.add,
            (1, &base_attrs, &context),
        )?;

        Ok(Operation {
            inner: Some(Arc::new(OperationInner {
                span: span.unbind(),
                context,
                start_time: Instant::now(),
                response_info: Mutex::new(None),
                base_attrs: base_attrs.unbind(),
                instrumentation: inner.clone(),
                constants: inner.constants.clone(),
            })),
        })
    }
}

struct ResponseInfo {
    status_code: http::StatusCode,
    http_version: http::Version,
}

struct OperationInner {
    span: Py<PyAny>,
    context: Py<PyAny>,
    start_time: Instant,
    response_info: Mutex<Option<ResponseInfo>>,
    base_attrs: Py<PyDict>,
    instrumentation: Arc<InstrumentationInner>,

    constants: Constants,
}

#[derive(Clone)]
pub(crate) struct Operation {
    inner: Option<Arc<OperationInner>>,
}

impl Operation {
    pub(crate) fn inject(&self, py: Python<'_>, request: &mut reqwest::Request) -> PyResult<()> {
        let Some(inner) = self.inner.as_ref() else {
            return Ok(());
        };
        // Avoid allocating a new map - we have an exclusive borrow on Request, so we can take the
        // headers out, pass to python, and take them back, which only copies the HeaderMap struct
        // to/from the Python wrapper and not its heap allocations.
        let headers = std::mem::take(request.headers_mut());
        let carrier = Headers(headers).into_pyobject(py)?;
        inner.constants.inject_context.call1(
            py,
            (&carrier, &inner.context, &inner.constants.headers_setter),
        )?;
        let hdrs = std::mem::take(&mut carrier.borrow_mut().0);
        *request.headers_mut() = hdrs;

        Ok(())
    }

    pub(crate) fn fill_response(&self, response: &reqwest::Response) {
        let Some(inner) = self.inner.as_ref() else {
            return;
        };
        let mut response_info = inner.response_info.lock().unwrap();

        *response_info = Some(ResponseInfo {
            status_code: response.status(),
            http_version: response.version(),
        });
    }

    pub(crate) fn end(&self, py: Python<'_>, err: Option<&PyErr>) -> PyResult<()> {
        let Some(inner) = self.inner.as_ref() else {
            return Ok(());
        };
        let span = inner.span.bind(py);
        let context = inner.context.bind(py);

        let base_attrs = inner.base_attrs.bind(py);
        let attrs = base_attrs.copy()?;
        inner
            .instrumentation
            .metric_http_client_active_requests
            .call_method1(py, &inner.constants.add, (-1, base_attrs, context))?;

        attrs.set_item(
            &inner.constants.network_protocol_name,
            &inner.constants.http,
        )?;
        if let Some(response_info) = inner.response_info.lock_py_attached(py).unwrap().take() {
            let status = inner.constants.status_code(py, response_info.status_code);
            span.call_method1(
                &inner.constants.set_attribute,
                (&inner.constants.http_response_status_code, &status),
            )?;
            attrs.set_item(&inner.constants.http_response_status_code, status)?;
            let protocol =
                network_protocol_version(py, response_info.http_version, &inner.constants);
            span.call_method1(
                &inner.constants.set_attribute,
                (&inner.constants.network_protocol_version, &protocol),
            )?;
            attrs.set_item(&inner.constants.network_protocol_version, protocol)?;
        }

        if let Some(err) = err {
            if let Ok(qualname) = err.get_type(py).qualname() {
                span.call_method1(
                    &inner.constants.set_attribute,
                    (&inner.constants.error_type, &qualname),
                )?;
                attrs.set_item(&inner.constants.error_type, qualname)?;
            }
        }

        let duration = inner.start_time.elapsed().as_secs_f64();
        inner
            .instrumentation
            .metric_http_client_request_duration
            .call_method1(py, &inner.constants.record, (duration, attrs, context))?;

        span.call_method0(&inner.constants.end)?;

        Ok(())
    }
}

fn network_protocol_version(
    py: Python<'_>,
    ver: http::Version,
    constants: &Constants,
) -> Py<PyString> {
    match ver {
        http::Version::HTTP_10 => constants.otel_1_0.clone_ref(py),
        http::Version::HTTP_2 => constants.otel_2.clone_ref(py),
        http::Version::HTTP_3 => constants.otel_3.clone_ref(py),
        _ => constants.otel_1_1.clone_ref(py),
    }
}

#[pyclass(module = "_pyqwest.otel", name = "_Headers")]
struct Headers(HeaderMap);

#[pyclass(module = "_pyqwest.otel", name = "_HeadersSetter", frozen)]
pub(super) struct HeadersSetter;

const HEADER_NAME_BAGGAGE: HeaderName = HeaderName::from_static("baggage");
const HEADER_NAME_TRACEPARENT: HeaderName = HeaderName::from_static("traceparent");
const HEADER_NAME_TRACESTATE: HeaderName = HeaderName::from_static("tracestate");

fn header_name(name: &str) -> PyResult<HeaderName> {
    match name {
        "baggage" => Ok(HEADER_NAME_BAGGAGE),
        "traceparent" => Ok(HEADER_NAME_TRACEPARENT),
        "tracestate" => Ok(HEADER_NAME_TRACESTATE),
        _ => HeaderName::from_str(name)
            .map_err(|_| PyValueError::new_err(format!("Invalid header name '{name}'"))),
    }
}

#[pymethods]
impl HeadersSetter {
    #[allow(clippy::unused_self)]
    fn set(&self, carrier: &mut Headers, key: &str, value: &str) -> PyResult<()> {
        carrier.0.append(
            header_name(key)?,
            HeaderValue::from_str(value)
                .map_err(|_| PyValueError::new_err(format!("Invalid header value '{value}'")))?,
        );

        Ok(())
    }
}

// Not clear if metrics can be GC'd and disappear, we keep a reference in case.
struct TokioRuntimeMetrics {
    _workers: Py<PyAny>,
    _blocking_threads: Py<PyAny>,
    _num_alive_tasks: Py<PyAny>,
    _queue_depth: Py<PyAny>,
    _worker_busy_duration: Py<PyAny>,
}

static RUNTIME_METRICS: PyOnceLock<TokioRuntimeMetrics> = PyOnceLock::new();

#[allow(clippy::too_many_lines)]
fn start_runtime_metrics(
    py: Python<'_>,
    meter: &Bound<'_, PyAny>,
    constants: &Constants,
) -> PyResult<()> {
    RUNTIME_METRICS.get_or_try_init(py, || {
        let runtime_metrics = get_runtime().metrics();

        // We go ahead and use inline strings here for values not used outside, since we are inside a PyOnceLock.

        let base_attrs = PyDict::new(py);
        base_attrs.set_item("rust.runtime", "tokio")?;
        let workers = meter.call_method1(
            &constants.create_up_down_counter,
            (
                "rust.async_runtime.workers.count",
                "{thread}",
                "Number of runtime worker threads",
            ),
        )?;
        workers.call_method1(&constants.add, (runtime_metrics.num_workers(), base_attrs))?;

        let blocking_threads = meter.call_method1(
            &constants.create_observable_up_down_counter,
            (
                "rust.async_runtime.blocking_threads.count",
                (
                    TokioRuntimeMetricsCallback {
                        metrics: runtime_metrics.clone(),
                        metric_type: RuntimeMetricType::BlockingThreadsActive,
                        attrs: PyDict::from_sequence(
                            PyTuple::new(
                                py,
                                [("rust.runtime", "tokio"), ("rust.thread.state", "active")],
                            )?
                            .as_any(),
                        )?
                        .unbind(),
                        constants: constants.clone(),
                    },
                    TokioRuntimeMetricsCallback {
                        metrics: runtime_metrics.clone(),
                        metric_type: RuntimeMetricType::BlockingThreadsIdle,
                        attrs: PyDict::from_sequence(
                            PyTuple::new(
                                py,
                                [("rust.runtime", "tokio"), ("rust.thread.state", "idle")],
                            )?
                            .as_any(),
                        )?
                        .unbind(),
                        constants: constants.clone(),
                    },
                ),
                "{thread}",
                "Number of runtime blocking threads",
            ),
        )?;

        let num_alive_tasks = meter.call_method1(
            &constants.create_observable_up_down_counter,
            (
                "rust.async_runtime.alive_tasks.count",
                (TokioRuntimeMetricsCallback {
                    metrics: runtime_metrics.clone(),
                    metric_type: RuntimeMetricType::NumAliveTasks,
                    attrs: PyDict::from_sequence(
                        PyTuple::new(py, [("rust.runtime", "tokio")])?.as_any(),
                    )?
                    .unbind(),
                    constants: constants.clone(),
                },),
                "{task}",
                "Number of live tasks",
            ),
        )?;

        let queue_depth = meter.call_method1(
            &constants.create_observable_up_down_counter,
            (
                "rust.async_runtime.task_queue.size",
                (
                    TokioRuntimeMetricsCallback {
                        metrics: runtime_metrics.clone(),
                        metric_type: RuntimeMetricType::QueueDepthBlocking,
                        attrs: PyDict::from_sequence(
                            PyTuple::new(
                                py,
                                [("rust.runtime", "tokio"), ("rust.task.type", "blocking")],
                            )?
                            .as_any(),
                        )?
                        .unbind(),
                        constants: constants.clone(),
                    },
                    TokioRuntimeMetricsCallback {
                        metrics: runtime_metrics.clone(),
                        metric_type: RuntimeMetricType::QueueDepthGlobal,
                        attrs: PyDict::from_sequence(
                            PyTuple::new(
                                py,
                                [("rust.runtime", "tokio"), ("rust.task.type", "global")],
                            )?
                            .as_any(),
                        )?
                        .unbind(),
                        constants: constants.clone(),
                    },
                ),
                "{task}",
                "Number of pending runtime tasks in queue",
            ),
        )?;

        let worker_busy_duration = meter.call_method1(
            "create_observable_counter",
            (
                "rust.async_runtime.worker_busy_duration",
                (TokioRuntimeMetricsCallback {
                    metrics: runtime_metrics.clone(),
                    metric_type: RuntimeMetricType::WorkerBusyDuration,
                    attrs: PyDict::from_sequence(
                        PyTuple::new(py, [("rust.runtime", "tokio")])?.as_any(),
                    )?
                    .unbind(),
                    constants: constants.clone(),
                },),
                "s",
                "Time worker is busy processing tasks",
            ),
        )?;

        Ok::<_, PyErr>(TokioRuntimeMetrics {
            _workers: workers.unbind(),
            _blocking_threads: blocking_threads.unbind(),
            _num_alive_tasks: num_alive_tasks.unbind(),
            _queue_depth: queue_depth.unbind(),
            _worker_busy_duration: worker_busy_duration.unbind(),
        })
    })?;
    Ok(())
}

enum RuntimeMetricType {
    BlockingThreadsActive,
    BlockingThreadsIdle,
    NumAliveTasks,
    QueueDepthBlocking,
    QueueDepthGlobal,
    WorkerBusyDuration,
}

#[pyclass(module = "_pyqwest.otel", name = "TokioRuntimeMetricsCallback", frozen)]
struct TokioRuntimeMetricsCallback {
    metrics: RuntimeMetrics,
    metric_type: RuntimeMetricType,
    attrs: Py<PyDict>,
    constants: Constants,
}

#[pymethods]
impl TokioRuntimeMetricsCallback {
    fn __call__<'py>(
        &self,
        py: Python<'py>,
        _options: &Bound<'py, PyAny>,
    ) -> PyResult<(Bound<'py, PyAny>,)> {
        let observation_class = self.constants.observation_class.bind(py);
        let value = match self.metric_type {
            RuntimeMetricType::BlockingThreadsActive => {
                let total = self.metrics.num_blocking_threads();
                let idle = self.metrics.num_idle_blocking_threads();
                PyInt::new(py, total - idle).into_any()
            }
            RuntimeMetricType::BlockingThreadsIdle => {
                PyInt::new(py, self.metrics.num_idle_blocking_threads()).into_any()
            }
            RuntimeMetricType::NumAliveTasks => {
                PyInt::new(py, self.metrics.num_alive_tasks()).into_any()
            }
            RuntimeMetricType::QueueDepthBlocking => {
                PyInt::new(py, self.metrics.blocking_queue_depth()).into_any()
            }
            RuntimeMetricType::QueueDepthGlobal => {
                PyInt::new(py, self.metrics.global_queue_depth()).into_any()
            }
            RuntimeMetricType::WorkerBusyDuration => {
                let mut busy_secs = 0.0;
                for i in 0..self.metrics.num_workers() {
                    busy_secs += self.metrics.worker_total_busy_duration(i).as_secs_f64();
                }
                PyFloat::new(py, busy_secs).into_any()
            }
        };
        let observation = observation_class.call1((value, &self.attrs))?;
        Ok((observation,))
    }
}
