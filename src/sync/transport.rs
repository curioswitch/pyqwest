use std::sync::Arc;

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::sync::PyOnceLock;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::HTTPVersion;
use crate::pyerrors;
use crate::shared::transport::{get_default_reqwest_client, new_reqwest_client, ClientParams};
use crate::sync::request::SyncRequest;
use crate::sync::response::SyncResponse;

#[pyclass(module = "pyqwest", name = "SyncHTTPTransport", frozen)]
#[derive(Clone)]
pub struct SyncHttpTransport {
    client: Arc<ArcSwapOption<reqwest::Client>>,
    http3: bool,
    close: bool,
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
        connect_timeout = None,
        read_timeout = None,
        pool_idle_timeout = None,
        pool_max_idle_per_host = None,
        tcp_keepalive_interval = None,
        enable_gzip = false,
        enable_brotli = false,
        enable_zstd = false,
        use_system_dns = false,
    ))]
    pub(crate) fn new(
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
        })
    }

    fn __enter__(slf: Py<SyncHttpTransport>) -> Py<SyncHttpTransport> {
        slf
    }

    fn __exit__(&self, _exc_type: Py<PyAny>, _exc_value: Py<PyAny>, _traceback: Py<PyAny>) {
        self.close();
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, SyncRequest>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_execute(py, request.get())
    }

    fn close(&self) {
        if self.close {
            self.client.store(None);
        }
    }
}

impl SyncHttpTransport {
    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &SyncRequest,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let req_builder = request.as_reqwest_builder(py, client, self.http3)?;
        let (tx, rx) = oneshot::channel::<PyResult<SyncResponse>>();
        let mut response = SyncResponse::pending(py)?;
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
        let res = py.detach(|| {
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving response: {e}")))
        })??;
        res.into_bound_py_any(py)
    }

    pub(super) fn py_default(py: Python<'_>) -> Self {
        SyncHttpTransport {
            client: Arc::new(ArcSwapOption::from_pointee(get_default_reqwest_client(py))),
            http3: false,
            close: false,
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
