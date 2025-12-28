use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;

use crate::common::HTTPVersion;
use crate::request::Request;
use crate::response::Response;

#[pyclass(module = "pyqwest")]
pub struct Client {
    client: reqwest::Client,
    http3: bool,
}

#[pymethods]
impl Client {
    #[new]
    #[pyo3(signature = (*, tls_ca_cert = None, http_version = None))]
    fn new<'py>(
        tls_ca_cert: Option<&[u8]>,
        http_version: Option<Bound<'py, HTTPVersion>>,
    ) -> PyResult<Self> {
        let mut builder = reqwest::Client::builder();
        let mut http3 = false;
        if let Some(http_version) = http_version {
            let http_version = http_version.get();
            match http_version {
                HTTPVersion::HTTP1 => {
                    builder = builder.http1_only();
                }
                HTTPVersion::HTTP2 => {
                    builder = builder.http2_prior_knowledge();
                }
                HTTPVersion::HTTP3 => {
                    http3 = true;
                    builder = builder.http3_prior_knowledge();
                }
            };
        }
        if let Some(ca_cert) = tls_ca_cert {
            let cert = reqwest::Certificate::from_pem(ca_cert).map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to parse CA certificate: {}", e))
            })?;
            builder = builder.tls_certs_only([cert]);
        }

        let client = if http3 {
            pyo3_async_runtimes::tokio::get_runtime().block_on(async move {
                let client = builder.build().map_err(|e| {
                    PyRuntimeError::new_err(format!(
                        "Failed to create client: {:+}",
                        errors::fmt(&e)
                    ))
                })?;
                Ok::<_, PyErr>(client)
            })?
        } else {
            builder.build().map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to create client: {:+}", errors::fmt(&e)))
            })?
        };
        Ok(Self { client, http3 })
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
        if self.http3 {
            req_builder = req_builder.version(http::Version::HTTP_3);
        }
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
            let res = req_builder.send().await.map_err(|e| {
                PyRuntimeError::new_err(format!("Request failed: {:+}", errors::fmt(&e)))
            })?;
            Ok(Response::new(res))
        })
    }
}
