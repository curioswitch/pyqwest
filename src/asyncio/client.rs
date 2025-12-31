use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use crate::asyncio::request::Request;
use crate::asyncio::response::Response;
use crate::common::HTTPVersion;
use crate::shared::transport::{new_reqwest_client, ClientParams};

#[pyclass(module = "pyqwest")]
pub struct Client {
    client: reqwest::Client,
    http3: bool,
}

#[pymethods]
impl Client {
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
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = Request::new(py, method, url, headers, content)?;
        let req_builder = request.as_reqwest_builder(py, &self.client, self.http3)?;
        future_into_py(py, async move {
            let res = req_builder.send().await.map_err(|e| {
                PyRuntimeError::new_err(format!("Request failed: {:+}", errors::fmt(&e)))
            })?;
            Ok(Response::new(res))
        })
    }
}
