use pyo3::exceptions::PyRuntimeError;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::HTTPVersion;
use crate::shared::transport::{new_reqwest_client, ClientParams};
use crate::sync::request::SyncRequest;
use crate::sync::response::SyncResponse;

#[pyclass(module = "pyqwest", name = "SyncHTTPTransport", frozen)]
#[derive(Clone)]
pub struct SyncHttpTransport {
    client: reqwest::Client,
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
        Ok(Self { client, http3 })
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, SyncRequest>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_execute(py, request.get())
    }
}

impl SyncHttpTransport {
    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &SyncRequest,
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = request.as_reqwest(py, self.http3)?;
        let (tx, rx) = oneshot::channel::<PyResult<reqwest::Response>>();
        let client = self.client.clone();
        get_runtime().spawn(async move {
            let response = client.execute(request).await.map_err(|e| {
                PyRuntimeError::new_err(format!("Request failed: {:+}", errors::fmt(&e)))
            });
            tx.send(response).unwrap();
        });
        let response = py.detach(|| {
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving response: {e}")))
        })??;
        SyncResponse::new(response).into_bound_py_any(py)
    }
}
