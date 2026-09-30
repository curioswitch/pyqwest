use std::sync::{Arc, Mutex, PoisonError};

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::sync::PyOnceLock;
use pyo3::{prelude::*, IntoPyObjectExt as _};

use crate::asyncio::awaitable::{EmptyAwaitable, ValueAwaitable};
use crate::asyncio::request::Request;
use crate::asyncio::response::Response;
use crate::asyncio::runtime::{into_awaitable, AsyncLibrary};
use crate::common::httpversion::HTTPVersion;
use crate::pyerrors;
use crate::shared::balancer::ClientSet;
use crate::shared::constants::Constants;
use crate::shared::exception::without_pending_exception;
use crate::shared::otel::{BalancerMetrics, Instrumentation, Operation};
use crate::shared::transport::{
    get_default_reqwest_client, ClientConfig, ClientParams, DEFAULT_MAX_REDIRECTS,
};

#[pyclass(module = "_pyqwest", name = "HTTPTransport", frozen, from_py_object)]
#[derive(Clone)]
pub struct HttpTransport {
    clients: Arc<ArcSwapOption<ClientSet>>,
    http3: bool,
    close: bool,

    instrumentation: Instrumentation,
    /// Kept so the balancer's observable metrics stay registered.
    _balancer_metrics: Option<Arc<BalancerMetrics>>,
    constants: Constants,
}

#[pymethods]
impl HttpTransport {
    #[new]
    #[pyo3(signature = (
        *,
        tls_ca_cert = None,
        tls_include_system_certs = false,
        tls_key = None,
        tls_cert = None,
        http_version = None,
        proxy = None,
        timeout = None,
        connect_timeout = 30.0,
        read_timeout = None,
        pool_idle_timeout = 90.0,
        pool_max_idle_per_host = None,
        tcp_keepalive_interval = 30.0,
        enable_gzip = true,
        enable_brotli = true,
        enable_zstd = true,
        use_system_dns = false,
        enable_cookie_store = false,
        follow_redirects = true,
        max_redirects = DEFAULT_MAX_REDIRECTS,
        max_streams_per_connection = None,
        max_connections = None,
        enable_otel = true,
        meter_provider = None,
        tracer_provider = None,
    ))]
    pub(crate) fn new(
        py: Python<'_>,
        tls_ca_cert: Option<&[u8]>,
        tls_include_system_certs: bool,
        tls_key: Option<&[u8]>,
        tls_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
        proxy: Option<Bound<'_, PyAny>>,
        timeout: Option<f64>,
        connect_timeout: Option<f64>,
        read_timeout: Option<f64>,
        pool_idle_timeout: Option<f64>,
        pool_max_idle_per_host: Option<usize>,
        tcp_keepalive_interval: Option<f64>,
        enable_gzip: bool,
        enable_brotli: bool,
        enable_zstd: bool,
        use_system_dns: bool,
        enable_cookie_store: bool,
        follow_redirects: bool,
        max_redirects: usize,
        max_streams_per_connection: Option<usize>,
        max_connections: Option<usize>,
        enable_otel: bool,
        meter_provider: Option<Bound<'_, PyAny>>,
        tracer_provider: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let config = ClientConfig::new(ClientParams {
            tls_ca_cert,
            tls_include_system_certs,
            tls_key,
            tls_cert,
            http_version,
            proxy,
            timeout,
            connect_timeout,
            read_timeout,
            pool_idle_timeout,
            pool_max_idle_per_host,
            tcp_keepalive_interval,
            enable_gzip,
            enable_brotli,
            enable_zstd,
            use_system_dns,
            enable_cookie_store,
            follow_redirects,
            max_redirects,
        })?;
        let http3 = config.http3();
        let clients = Arc::new(ArcSwapOption::from_pointee(ClientSet::new(
            config,
            max_streams_per_connection,
            max_connections,
        )?));
        let constants = Constants::get(py)?;
        let instrumentation =
            Instrumentation::new(py, enable_otel, meter_provider, tracer_provider, &constants)?;
        let balancer_metrics = instrumentation.observe_balancer(py, &clients)?;
        Ok(Self {
            clients,
            http3,
            close: true,
            instrumentation,
            _balancer_metrics: balancer_metrics,
            constants,
        })
    }

    fn __aenter__(slf: Py<HttpTransport>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        ValueAwaitable {
            value: Some(slf.into_any()),
        }
        .into_py_any(py)
    }

    fn __aexit__(
        &self,
        py: Python<'_>,
        _exc_type: Py<PyAny>,
        _exc_value: Py<PyAny>,
        _traceback: Py<PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.aclose(py)
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, Request>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_stream(py, request.get())
    }

    fn aclose(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.close {
            self.clients.store(None);
        }
        EmptyAwaitable.into_py_any(py)
    }

    /// The requests in flight on each of the transport's connections, in
    /// creation order, or `None` when `max_streams_per_connection` is unset.
    #[getter]
    fn _connection_loads(&self) -> Option<Vec<usize>> {
        self.clients
            .load()
            .as_ref()
            .and_then(|clients| clients.loads())
    }
}

impl HttpTransport {
    pub(super) fn do_stream<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let clients = self.clients.load();
        let Some(clients) = clients.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let library = AsyncLibrary::current(py, &self.constants)?;
        let (mut request_rs, request_iter_task) = request.new_reqwest(py, self.http3, library)?;
        let mut response = Response::pending(py, self.constants.clone(), library)?;
        let operation = self.instrumentation.start(py, &request.head)?;
        operation.inject(py, &mut request_rs)?;
        let on_done =
            EndOperationCallback::new(operation.clone(), self.constants.clone(), request_iter_task)
                .into_bound_py_any(py)?;
        let (client, in_flight) = clients.acquire()?;
        into_awaitable(
            py,
            library,
            &self.constants,
            async move {
                // A failed request drops `in_flight` here, releasing its hold.
                let res = client
                    .execute(request_rs)
                    .await
                    .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
                operation.fill_response(&res);
                response.fill(res, in_flight).await;
                Ok(response)
            },
            Some(on_done),
        )
    }

    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let clients = self.clients.load();
        let Some(clients) = clients.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let library = AsyncLibrary::current(py, &self.constants)?;
        let (mut request_rs, request_iter_task) = request.new_reqwest(py, self.http3, library)?;
        let mut response = Response::pending(py, self.constants.clone(), library)?;
        let operation = self.instrumentation.start(py, &request.head)?;
        operation.inject(py, &mut request_rs)?;
        let on_done =
            EndOperationCallback::new(operation.clone(), self.constants.clone(), request_iter_task)
                .into_bound_py_any(py)?;
        let (client, in_flight) = clients.acquire()?;
        into_awaitable(
            py,
            library,
            &self.constants,
            async move {
                let res = client
                    .execute(request_rs)
                    .await
                    .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
                operation.fill_response(&res);
                response.fill(res, in_flight).await;
                let full_response = response.into_full_response().await?;
                Ok(full_response)
            },
            Some(on_done),
        )
    }

    pub(super) fn py_default(py: Python<'_>) -> PyResult<Self> {
        let constants = Constants::get(py)?;
        Ok(Self {
            clients: Arc::new(ArcSwapOption::from_pointee(ClientSet::single(
                get_default_reqwest_client(py),
            ))),
            http3: false,
            close: false,
            instrumentation: Instrumentation::new(py, true, None, None, &constants)?,
            _balancer_metrics: None,
            constants,
        })
    }
}

static DEFAULT_TRANSPORT: PyOnceLock<Py<HttpTransport>> = PyOnceLock::new();

#[pyfunction]
pub(crate) fn get_default_transport(py: Python<'_>) -> PyResult<Py<HttpTransport>> {
    Ok(DEFAULT_TRANSPORT
        .get_or_try_init(py, || Py::new(py, HttpTransport::py_default(py)?))?
        .clone_ref(py))
}

#[pyclass(module = "_pyqwest.async", frozen)]
struct EndOperationCallback {
    /// Taken by whichever ends it: the call, or `Drop` if no call came.
    operation: Mutex<Option<Operation>>,
    constants: Constants,
    request_iter_task: Arc<ArcSwapOption<Py<PyAny>>>,
}

impl EndOperationCallback {
    const fn new(
        operation: Operation,
        constants: Constants,
        request_iter_task: Arc<ArcSwapOption<Py<PyAny>>>,
    ) -> Self {
        Self {
            operation: Mutex::new(Some(operation)),
            constants,
            request_iter_task,
        }
    }
}

#[pymethods]
impl EndOperationCallback {
    fn __call__(&self, py: Python<'_>, fut: &Bound<'_, PyAny>) -> PyResult<()> {
        let operation = self
            .operation
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        let res = fut.call_method0(&self.constants.result);
        let mut cancelled = Ok(());
        if let Some(task) = self.request_iter_task.swap(None) {
            let response = res
                .as_ref()
                .ok()
                .and_then(|res| res.cast::<Response>().ok());
            match response {
                // Move the request iterator task to the response being returned in this future
                // so it can be canceled when the response is closed.
                Some(response) => response.get().set_request_iter_task(task),
                None => {
                    // Response already failed, cancel the request iterator here.
                    cancelled = task.call_method0(py, &self.constants.cancel).map(drop);
                }
            }
        }
        // End the operation even if the cancel failed, since Drop no longer can.
        let ended = operation.map_or(Ok(()), |operation| operation.end(py, res.as_ref().err()));
        match (ended, cancelled) {
            (Err(end_err), Err(cancel_err)) => {
                cancel_err.write_unraisable(py, Some(fut));
                Err(end_err)
            }
            (ended, cancelled) => ended.and(cancelled),
        }
    }
}

impl Drop for EndOperationCallback {
    fn drop(&mut self) {
        let operation = self
            .operation
            .get_mut()
            .unwrap_or_else(PoisonError::into_inner);
        let Some(operation) = operation.take() else {
            return;
        };
        // A done callback freed without being called still ends its operation,
        // so the span closes and the active request count comes back down. That
        // happens when the event loop or trio run ends before the request
        // completes, leaving its outcome nowhere to go.
        Python::attach(|py| {
            without_pending_exception(py, || {
                if let Some(task) = self.request_iter_task.swap(None) {
                    let task = task.bind(py);
                    if let Err(e) = task.call_method0(&self.constants.cancel_soon) {
                        e.write_unraisable(py, Some(task));
                    }
                }
                let err = PyRuntimeError::new_err("request ended without completing");
                if let Err(e) = operation.end(py, Some(&err)) {
                    e.write_unraisable(py, None);
                }
            });
        });
    }
}
