use bytes::Bytes;
use pyo3::{
    exceptions::PyValueError,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyIterator},
    Borrowed, Bound, FromPyObject, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::headers::Headers;

#[pyclass]
pub struct SyncRequest {
    pub(crate) method: http::Method,
    pub(crate) url: reqwest::Url,
    pub(crate) headers: Option<Py<Headers>>,
    content: Option<Content>,
}

#[pymethods]
impl SyncRequest {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None))]
    pub(crate) fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        let method = http::Method::try_from(method)
            .map_err(|e| PyValueError::new_err(format!("Invalid HTTP method: {}", e)))?;
        let url = reqwest::Url::parse(url)
            .map_err(|e| PyValueError::new_err(format!("Invalid URL: {}", e)))?;
        let headers = if let Some(headers) = headers {
            if let Ok(hdrs) = headers.cast::<Headers>() {
                Some(hdrs.clone().unbind())
            } else {
                Some(Py::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let content: Option<Content> = match content {
            Some(content) => Some(content.extract()?),
            None => None,
        };
        Ok(Self {
            method,
            url,
            headers,
            content,
        })
    }
}

impl SyncRequest {
    pub(crate) fn content_into_reqwest(&mut self) -> Option<reqwest::Body> {
        match self.content.take() {
            Some(Content::Bytes(bytes)) => Some(reqwest::Body::from(bytes)),
            Some(Content::Iter(iter)) => {
                let (tx, rx) = mpsc::channel::<PyResult<Bytes>>(1);
                get_runtime().spawn_blocking(move || {
                    Python::attach(|py| {
                        let mut iter = iter.into_bound(py);
                        loop {
                            let res = match iter.next() {
                                Some(Ok(item)) => item.extract::<Bytes>().map_err(|e| {
                                    PyValueError::new_err(format!("Invalid bytes item: {}", e))
                                }),
                                Some(Err(e)) => Err(e),
                                None => break,
                            };
                            if py.detach(|| tx.blocking_send(res)).is_err() {
                                break;
                            }
                        }
                    })
                });
                Some(reqwest::Body::wrap_stream(ReceiverStream::new(rx)))
            }
            None => None,
        }
    }
}

enum Content {
    Bytes(Bytes),
    Iter(Py<PyIterator>),
}

impl FromPyObject<'_, '_> for Content {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<Bytes>() {
            return Ok(Self::Bytes(bytes));
        }

        let iter = PyIterator::from_object(&obj)?;
        Ok(Self::Iter(iter.unbind()))
    }
}
