use std::sync::{Arc, Mutex};

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::sync::PyOnceLock;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use tokio::sync::oneshot;

use crate::common::httpversion::HTTPVersion;
use crate::pyerrors;
use crate::shared::balancer::ClientSet;
use crate::shared::constants::Constants;
use crate::shared::otel::{BalancerMetrics, Instrumentation, Operation};
use crate::shared::runtime::get_runtime;
use crate::shared::transport::{
    get_default_reqwest_client, ClientConfig, ClientParams, DEFAULT_MAX_REDIRECTS,
};
use crate::sync::request::SyncRequest;
use crate::sync::response::{close_request_iter, RequestIterHandle, SyncResponse};

#[pyclass(
    module = "_pyqwest",
    name = "SyncHTTPTransport",
    frozen,
    from_py_object
)]
#[derive(Clone)]
pub struct SyncHttpTransport {
    clients: Arc<ArcSwapOption<ClientSet>>,
    http3: bool,
    close: bool,

    instrumentation: Instrumentation,
    /// Kept so the balancer's observable metrics stay registered.
    _balancer_metrics: Option<Arc<BalancerMetrics>>,
    constants: Constants,
}

#[pymethods]
impl SyncHttpTransport {
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

    fn __enter__(slf: Py<SyncHttpTransport>) -> Py<SyncHttpTransport> {
        slf
    }

    fn __exit__(&self, _exc_type: Py<PyAny>, _exc_value: Py<PyAny>, _traceback: Py<PyAny>) {
        self.close();
    }

    fn execute_sync<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, SyncRequest>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_stream(py, request.get())?.into_bound_py_any(py)
    }

    fn close(&self) {
        if self.close {
            self.clients.store(None);
        }
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

impl SyncHttpTransport {
    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &SyncRequest,
    ) -> PyResult<Bound<'py, PyAny>> {
        let operation = self.instrumentation.start(py, &request.head)?;
        match self.send(py, request, &operation) {
            Ok(res) => {
                let res = res.read_full(py);
                operation.end(py, res.as_ref().err())?;
                res
            }
            Err(e) => {
                operation.end(py, Some(&e))?;
                Err(e)
            }
        }
    }

    pub(super) fn do_stream(
        &self,
        py: Python<'_>,
        request: &SyncRequest,
    ) -> PyResult<SyncResponse> {
        let operation = self.instrumentation.start(py, &request.head)?;
        let res = self.send(py, request, &operation);
        operation.end(py, res.as_ref().err())?;
        res
    }

    fn send(
        &self,
        py: Python<'_>,
        request: &SyncRequest,
        operation: &Operation,
    ) -> PyResult<SyncResponse> {
        let clients = self.clients.load();
        let Some(clients) = clients.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let (mut request_rs, request_iter) = request.new_reqwest(py, self.http3)?;
        let request_iter: RequestIterHandle = Arc::new(Mutex::new(request_iter));
        let (tx, rx) = oneshot::channel::<PyResult<SyncResponse>>();
        let mut response = SyncResponse::pending(py, request_iter.clone(), self.constants.clone())?;
        operation.inject(py, &mut request_rs)?;
        let (client, in_flight) = clients.acquire()?;
        let operation = operation.clone();
        get_runtime().spawn(async move {
            match client.execute(request_rs).await {
                Ok(res) => {
                    operation.fill_response(&res);
                    response.fill(res, in_flight).await;
                    let _ = tx.send(Ok(response));
                }
                Err(e) => {
                    // Dropping `in_flight` releases the failed request's hold.
                    let _ = tx.send(Err(pyerrors::from_reqwest(&e, "Request failed")));
                }
            }
        });
        py.detach(|| {
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving response: {e}")))
                .flatten()
        })
        .inspect_err(|_| close_request_iter(py, &request_iter, &self.constants))
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

static DEFAULT_TRANSPORT: PyOnceLock<Py<SyncHttpTransport>> = PyOnceLock::new();

#[pyfunction]
pub(crate) fn get_default_sync_transport(py: Python<'_>) -> PyResult<Py<SyncHttpTransport>> {
    Ok(DEFAULT_TRANSPORT
        .get_or_try_init(py, || Py::new(py, SyncHttpTransport::py_default(py)?))?
        .clone_ref(py))
}
