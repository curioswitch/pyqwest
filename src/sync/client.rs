use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::HTTPVersion;
use crate::shared::transport::{new_reqwest_client, ClientParams};
use crate::sync::request::SyncRequest;
use crate::sync::response::SyncResponse;

#[pyclass(module = "pyqwest")]
pub struct SyncClient {
    client: reqwest::Client,
    http3: bool,
}

#[pymethods]
impl SyncClient {
    #[new]
    #[pyo3(signature = (*, tls_ca_cert = None, http_version = None))]
    fn new(
        tls_ca_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
    ) -> PyResult<Self> {
        let (client, http3) = new_reqwest_client(ClientParams {
            tls_ca_cert,
            http_version,
        })?;
        Ok(Self { client, http3 })
    }

    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn execute<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<SyncResponse> {
        let request = SyncRequest::new(py, method, url, headers, content)?;
        let req_builder = request.as_reqwest_builder(py, &self.client, self.http3)?;
        let (tx, rx) = oneshot::channel::<PyResult<reqwest::Response>>();
        get_runtime().spawn(async move {
            let res = req_builder.send().await.map_err(|e| {
                PyRuntimeError::new_err(format!("Request failed: {:+}", errors::fmt(&e)))
            });
            tx.send(res).unwrap();
        });
        let res = py.detach(|| {
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving response: {e}")))
        })??;
        Ok(SyncResponse::new(res))
    }
}
