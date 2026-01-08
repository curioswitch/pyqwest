use std::sync::Arc;

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::asyncio::awaitable::{EmptyAwaitable, ValueAwaitable};
use crate::asyncio::request::Request;
use crate::asyncio::response::Response;
use crate::common::HTTPVersion;
use crate::shared::pyerrors;
use crate::shared::transport::{new_reqwest_client, ClientParams};

#[pyclass(module = "pyqwest", name = "HTTPTransport", frozen)]
#[derive(Clone)]
pub struct HttpTransport {
    client: Arc<ArcSwapOption<reqwest::Client>>,
    http3: bool,
}

#[pymethods]
impl HttpTransport {
    #[new]
    #[pyo3(signature = (*, tls_ca_cert = None, tls_key = None, tls_cert = None, http_version = None))]
    pub(crate) fn new(
        tls_ca_cert: Option<&[u8]>,
        tls_key: Option<&[u8]>,
        tls_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
    ) -> PyResult<Self> {
        let (client, http3) = new_reqwest_client(ClientParams {
            tls_ca_cert,
            tls_key,
            tls_cert,
            http_version,
        })?;
        Ok(Self {
            client: Arc::new(ArcSwapOption::from_pointee(client)),
            http3,
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
        self.close();
        EmptyAwaitable.into_py_any(py)
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, Request>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_execute(py, request.get())
    }

    fn close(&self) {
        self.client.store(None);
    }
}

impl HttpTransport {
    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let req_builder = request.new_reqwest_builder(py, client, self.http3)?;
        let mut response = Response::pending(py)?;
        future_into_py(py, async move {
            let res = req_builder
                .send()
                .await
                .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
            response.fill(res).await;
            Ok(response)
        })
    }

    pub(super) fn do_execute_and_read_full<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let req_builder = request.new_reqwest_builder(py, client, self.http3)?;
        let mut response = Response::pending(py)?;
        future_into_py(py, async move {
            let res = req_builder
                .send()
                .await
                .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
            response.fill(res).await;
            let full_response = response.into_full_response().await;
            Ok(full_response)
        })
    }
}
