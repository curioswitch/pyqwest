use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::common::HTTPVersion;
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
            }
        }
        if let Some(ca_cert) = tls_ca_cert {
            let cert = reqwest::Certificate::from_pem(ca_cert).map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to parse CA certificate: {e}"))
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

    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn execute<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<SyncResponse> {
        let mut request = SyncRequest::new(py, method, url, headers, content)?;
        let mut req_builder = self
            .client
            .request(request.method.clone(), request.url.clone());
        if self.http3 {
            req_builder = req_builder.version(http::Version::HTTP_3);
        }
        if let Some(hdrs) = &request.headers {
            let hdrs = hdrs.bind(py).borrow();
            for (key, value) in &hdrs.store {
                let value_str = value.extract::<&str>(py)?;
                req_builder = req_builder.header(key, value_str);
            }
        }
        if let Some(content) = request.content_into_reqwest(py) {
            req_builder = req_builder.body(content);
        }
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
