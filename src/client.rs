use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use crate::request::Request;
use crate::response::Response;

#[pyclass(module = "pyqwest")]
pub struct Client {
    client: reqwest::Client,
}

#[pymethods]
impl Client {
    #[new]
    #[pyo3(signature = (*, http_version = None))]
    fn new<'py>(http_version: Option<Bound<'py, HTTPVersion>>) -> PyResult<Self> {
        let mut builder = reqwest::Client::builder();
        if let Some(http_version) = http_version {
            let http_version = http_version.get();
            match http_version {
                HTTPVersion::HTTP1 => {
                    builder = builder.http1_only();
                }
                HTTPVersion::HTTP2 => {
                    builder = builder.http2_prior_knowledge();
                }
                HTTPVersion::HTTP3 => {}
            };
        }
        let client = builder
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create client: {}", e)))?;
        Ok(Self { client })
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, Request>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let mut request = request.clone().borrow_mut();
        let mut req_builder = self
            .client
            .request(request.method.clone(), request.url.clone());
        if let Some(hdrs) = &request.headers {
            let hdrs = hdrs.bind(py).borrow();
            for (key, value) in hdrs.store.iter() {
                let value_str = value.extract::<&str>(py)?;
                req_builder = req_builder.header(key, value_str);
            }
        }
        if let Some(body) = request.body.take() {
            req_builder = req_builder.body(body.into_reqwest_body(py)?);
        }
        future_into_py(py, async move {
            let res = req_builder
                .send()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Request failed: {}", e)))?;
            Ok(Response::new(res))
        })
    }
}

#[pyclass(frozen, eq, eq_int)]
#[derive(PartialEq)]
pub(crate) enum HTTPVersion {
    HTTP1,
    HTTP2,
    HTTP3,
}
