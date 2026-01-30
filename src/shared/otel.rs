use std::{
    str::FromStr as _,
    sync::{Arc, Mutex},
    time::Instant,
};

use http::{HeaderMap, HeaderName, HeaderValue};
use pyo3::{
    exceptions::PyValueError,
    pyclass, pymethods,
    sync::MutexExt as _,
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyString, PyTypeMethods as _},
    Bound, IntoPyObject as _, Py, PyAny, PyErr, PyResult, Python,
};

use crate::shared::{constants::Constants, request::RequestHead};

struct InstrumentationInner {
    tracer: Py<PyAny>,

    metric_http_client_active_requests: Py<PyAny>,
    metric_http_client_request_duration: Py<PyAny>,
}

#[derive(Clone)]
pub(crate) struct Instrumentation {
    inner: Arc<InstrumentationInner>,
    constants: Constants,
}

impl Instrumentation {
    pub(crate) fn new(
        py: Python<'_>,
        meter_provider: Option<Bound<'_, PyAny>>,
        tracer_provider: Option<Bound<'_, PyAny>>,
        constants: &Constants,
    ) -> PyResult<Self> {
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
            inner: Arc::new(InstrumentationInner {
                tracer: tracer.unbind(),
                metric_http_client_active_requests: metric_http_client_active_requests.unbind(),
                metric_http_client_request_duration: metric_http_client_request_duration.unbind(),
            }),
            constants: constants.clone(),
        })
    }

    pub(crate) fn start(&self, py: Python<'_>, request: &RequestHead) -> PyResult<Operation> {
        let http_method = request.method(py)?;
        let base_attrs = PyDict::new(py);
        // We require a host to send requests so any validated request will have a host.
        let host = request.parsed_url().host_str().unwrap_or_default();
        let port = request
            .parsed_url()
            .port_or_known_default()
            // We only support schemes with a known default port.
            .unwrap_or_default();

        base_attrs.set_item(&self.constants.http_request_method, &http_method)?;
        base_attrs.set_item(&self.constants.server_address, host)?;
        base_attrs.set_item(&self.constants.server_port, port)?;

        let span_attrs = base_attrs.copy()?;
        span_attrs.set_item(&self.constants.network_protocol_name, &self.constants.http)?;
        span_attrs.set_item(&self.constants.url_full, request.url())?;

        // Because we are wrapping Rust code, we have no use case for setting the current span,
        // which luckily simplifies the asyncio path substantially.
        let span = self.inner.tracer.bind(py).call_method1(
            &self.constants.start_span,
            (
                http_method,
                py.None(),
                &self.constants.span_kind_client,
                span_attrs,
            ),
        )?;
        let context = self.constants.set_span_in_context.call1(py, (&span,))?;

        self.inner.metric_http_client_active_requests.call_method1(
            py,
            &self.constants.add,
            (1, &base_attrs, &context),
        )?;

        Ok(Operation {
            inner: Arc::new(OperationInner {
                span: span.unbind(),
                context,
                start_time: Instant::now(),
                response_info: Mutex::new(None),
                base_attrs: base_attrs.unbind(),
            }),
            instrumentation: self.inner.clone(),
            constants: self.constants.clone(),
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
}

#[derive(Clone)]
pub(crate) struct Operation {
    inner: Arc<OperationInner>,

    instrumentation: Arc<InstrumentationInner>,
    constants: Constants,
}

impl Operation {
    pub(crate) fn inject(&self, py: Python<'_>, request: &mut reqwest::Request) -> PyResult<()> {
        // Avoid allocating a new map - we have an exclusive borrow on Request, so we can take the
        // headers out, pass to python, and take them back, which only copies the HeaderMap struct
        // to/from the Python wrapper and not its heap allocations.
        let headers = std::mem::take(request.headers_mut());
        let carrier = Headers(headers).into_pyobject(py)?;
        self.constants.inject_context.call1(
            py,
            (
                &carrier,
                &self.inner.context,
                &self.constants.headers_setter,
            ),
        )?;
        let hdrs = std::mem::take(&mut carrier.borrow_mut().0);
        *request.headers_mut() = hdrs;

        Ok(())
    }

    pub(crate) fn fill_response(&self, response: &reqwest::Response) {
        let mut response_info = self.inner.response_info.lock().unwrap();

        *response_info = Some(ResponseInfo {
            status_code: response.status(),
            http_version: response.version(),
        });
    }

    pub(crate) fn end(&self, py: Python<'_>, err: Option<&PyErr>) -> PyResult<()> {
        let inner = self.inner.as_ref();
        let span = inner.span.bind(py);
        let context = inner.context.bind(py);

        let base_attrs = inner.base_attrs.bind(py);
        let attrs = base_attrs.copy()?;
        self.instrumentation
            .metric_http_client_active_requests
            .call_method1(py, &self.constants.add, (-1, base_attrs, context))?;

        attrs.set_item(&self.constants.network_protocol_name, &self.constants.http)?;
        if let Some(response_info) = self
            .inner
            .response_info
            .lock_py_attached(py)
            .unwrap()
            .take()
        {
            let status = self.constants.status_code(py, response_info.status_code);
            span.call_method1(
                &self.constants.set_attribute,
                (&self.constants.http_response_status_code, &status),
            )?;
            attrs.set_item(&self.constants.http_response_status_code, status)?;
            let protocol =
                network_protocol_version(py, response_info.http_version, &self.constants);
            span.call_method1(
                &self.constants.set_attribute,
                (&self.constants.network_protocol_version, &protocol),
            )?;
            attrs.set_item(&self.constants.network_protocol_version, protocol)?;
        }

        if let Some(err) = err {
            if let Ok(qualname) = err.get_type(py).qualname() {
                span.call_method1(
                    &self.constants.set_attribute,
                    (&self.constants.error_type, &qualname),
                )?;
                attrs.set_item(&self.constants.error_type, qualname)?;
            }
        }

        let duration = self.inner.start_time.elapsed().as_secs_f64();
        self.instrumentation
            .metric_http_client_request_duration
            .call_method1(py, &self.constants.record, (duration, attrs, context))?;

        span.call_method0(&self.constants.end)?;

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
