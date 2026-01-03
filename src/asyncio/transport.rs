use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use crate::asyncio::request::Request;
use crate::asyncio::response::Response;
use crate::common::HTTPVersion;
use crate::shared::transport::{new_reqwest_client, ClientParams};

#[pyclass(module = "pyqwest", name = "HTTPTransport", frozen)]
#[derive(Clone)]
pub struct HttpTransport {
    client: reqwest::Client,
    http3: bool,
}

#[pymethods]
impl HttpTransport {
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
        request: &Bound<'py, Request>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_execute(py, request.get())
    }
}

impl HttpTransport {
    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = request.as_reqwest(py, self.http3)?;
        let client = self.client.clone();
        future_into_py(py, async move {
            let response = client.execute(request).await.map_err(|e| {
                PyRuntimeError::new_err(format!("Request failed: {:+}", errors::fmt(&e)))
            })?;
            Ok(Response::new(response))
        })
    }
}
