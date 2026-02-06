use bytes::Bytes;
use pyo3::{
    pybacked::PyBackedBytes,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyIterator, PyString, PyTuple},
    Borrowed, Bound, FromPyObject, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::{
    headers::Headers,
    shared::request::{RequestHead, RequestStreamError, RequestStreamResult},
    sync::timeout::get_timeout,
};

#[pyclass(module = "_pyqwest", frozen)]
pub struct SyncRequest {
    pub(super) head: RequestHead,
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
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        let headers = Headers::from_option(py, headers)?;
        let content: Option<Content> = match content {
            Some(content) => Some(content.extract()?),
            None => None,
        };
        Ok(Self {
            head: RequestHead::new(method, url, headers)?,
            content,
        })
    }

    #[getter]
    fn method(&self, py: Python<'_>) -> PyResult<Py<PyString>> {
        self.head.method(py)
    }

    #[getter]
    fn url(&self) -> &str {
        self.head.url()
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.head.headers(py)
    }

    #[getter]
    fn content<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                Ok(PyTuple::new(py, [bytes])?.into_any().try_iter()?.into_any())
            }
            Some(Content::Iter(iter)) => Ok(iter.bind(py).clone().into_any()),
            None => Ok(PyTuple::empty(py).into_any().try_iter()?.into_any()),
        }
    }
}

impl SyncRequest {
    pub(crate) fn new_reqwest(
        &self,
        py: Python<'_>,
        http3: bool,
    ) -> PyResult<(reqwest::Request, Option<Py<PyAny>>)> {
        let mut req = self.head.new_reqwest(py, http3)?;
        if let Some(timeout) = get_timeout(py)? {
            *req.timeout_mut() = Some(timeout);
        }
        let mut request_iter: Option<Py<PyAny>> = None;
        if let Some((body, iter)) = self.content_into_reqwest(py) {
            *req.body_mut() = Some(body);
            request_iter = iter;
        }
        Ok((req, request_iter))
    }

    fn content_into_reqwest(&self, py: Python<'_>) -> Option<(reqwest::Body, Option<Py<PyAny>>)> {
        match &self.content {
            Some(Content::Bytes(bytes)) => Some((
                reqwest::Body::from(Bytes::from_owner(bytes.clone_ref(py))),
                None,
            )),
            Some(Content::Iter(iter)) => {
                let (tx, rx) = mpsc::channel::<RequestStreamResult<Bytes>>(1);
                let read_iter = iter.clone_ref(py);
                get_runtime().spawn_blocking(move || {
                    Python::attach(|py| {
                        let mut read_iter = read_iter.into_bound(py);
                        loop {
                            let res = match read_iter.next() {
                                Some(Ok(item)) => item.extract::<Bytes>().map_err(|e| {
                                    RequestStreamError::new(format!("Invalid bytes item: {e}"))
                                }),
                                Some(Err(e)) => {
                                    let e_py = e.into_value(py);
                                    Err(RequestStreamError::from_py(e_py.bind(py).as_any()))
                                }
                                None => break,
                            };
                            let errored = res.is_err();
                            if py.detach(|| tx.blocking_send(res)).is_err() || errored {
                                break;
                            }
                        }
                    });
                });
                Some((
                    reqwest::Body::wrap_stream(ReceiverStream::new(rx)),
                    Some(iter.clone_ref(py).into_any()),
                ))
            }
            None => None,
        }
    }
}

enum Content {
    Bytes(PyBackedBytes),
    Iter(Py<PyIterator>),
}

impl FromPyObject<'_, '_> for Content {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }

        Ok(Self::Iter(obj.try_iter()?.unbind()))
    }
}
