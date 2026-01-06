use std::sync::Arc;

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::HTTPVersion;
use crate::shared::pyerrors;
use crate::shared::transport::{new_reqwest_client, ClientParams};
use crate::sync::request::SyncRequest;
use crate::sync::response::SyncResponse;

#[pyclass(module = "pyqwest", name = "SyncHTTPTransport", frozen)]
#[derive(Clone)]
pub struct SyncHttpTransport {
    client: Arc<ArcSwapOption<reqwest::Client>>,
    http3: bool,
}

#[pymethods]
impl SyncHttpTransport {
    #[new]
    #[pyo3(signature = (*, tls_ca_cert = None, http_version = None))]
    pub(crate) fn new(
        tls_ca_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
    ) -> PyResult<Self> {
        let (client, http3) = new_reqwest_client(ClientParams {
            tls_ca_cert,
            http_version,
        })?;
        Ok(Self {
            client: Arc::new(ArcSwapOption::from_pointee(client)),
            http3,
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
        self.client.store(None);
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
                    let _ = tx.send(Err(pyerrors::from_reqwest(e, "Request failed")));
                }
            }
        });
        let res = py.detach(|| {
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving response: {e}")))
        })??;
        res.into_bound_py_any(py)
    }
}
