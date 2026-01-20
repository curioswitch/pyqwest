use std::sync::{Arc, Mutex};

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::sync::PyOnceLock;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::httpversion::HTTPVersion;
use crate::pyerrors;
use crate::shared::constants::Constants;
use crate::shared::transport::{get_default_reqwest_client, new_reqwest_client, ClientParams};
use crate::sync::request::SyncRequest;
use crate::sync::response::{close_request_iter, RequestIterHandle, SyncResponse};

#[pyclass(module = "_pyqwest", name = "SyncHTTPTransport", frozen)]
#[derive(Clone)]
pub struct SyncHttpTransport {
    client: Arc<ArcSwapOption<reqwest::Client>>,
    http3: bool,
    close: bool,

    constants: Constants,
}

#[pymethods]
impl SyncHttpTransport {
    #[new]
    #[pyo3(signature = (
        *,
        tls_ca_cert = None,
        tls_key = None,
        tls_cert = None,
        http_version = None,
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
    ))]
    pub(crate) fn new(
        py: Python<'_>,
        tls_ca_cert: Option<&[u8]>,
        tls_key: Option<&[u8]>,
        tls_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
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
    ) -> PyResult<Self> {
        let (client, http3) = new_reqwest_client(ClientParams {
            tls_ca_cert,
            tls_key,
            tls_cert,
            http_version,
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
        })?;
        Ok(Self {
            client: Arc::new(ArcSwapOption::from_pointee(client)),
            http3,
            close: true,
            constants: Constants::get(py)?,
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
        self.do_execute(py, request.get())?.into_bound_py_any(py)
    }

    fn close(&self) {
        if self.close {
            self.client.store(None);
        }
    }
}

impl SyncHttpTransport {
    pub(super) fn do_execute(
        &self,
        py: Python<'_>,
        request: &SyncRequest,
    ) -> PyResult<SyncResponse> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let (req_builder, request_iter) = request.new_reqwest_builder(py, client, self.http3)?;
        let request_iter: RequestIterHandle = Arc::new(Mutex::new(request_iter));
        let (tx, rx) = oneshot::channel::<PyResult<SyncResponse>>();
        let mut response = SyncResponse::pending(py, request_iter.clone(), self.constants.clone())?;
        get_runtime().spawn(async move {
            match req_builder.send().await {
                Ok(res) => {
                    response.fill(res).await;
                    let _ = tx.send(Ok(response));
                }
                Err(e) => {
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

    pub(super) fn py_default(py: Python<'_>) -> Self {
        SyncHttpTransport {
            client: Arc::new(ArcSwapOption::from_pointee(get_default_reqwest_client(py))),
            http3: false,
            close: false,
            constants: Constants::get(py).unwrap(),
        }
    }
}

static DEFAULT_TRANSPORT: PyOnceLock<Py<SyncHttpTransport>> = PyOnceLock::new();

#[pyfunction]
pub(crate) fn get_default_sync_transport(py: Python<'_>) -> PyResult<Py<SyncHttpTransport>> {
    Ok(DEFAULT_TRANSPORT
        .get_or_try_init(py, || Py::new(py, SyncHttpTransport::py_default(py)))?
        .clone_ref(py))
}
