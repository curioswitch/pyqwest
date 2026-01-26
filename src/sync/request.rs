use bytes::Bytes;
use pyo3::{
    pybacked::PyBackedBytes,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyIterator, PyString, PyTuple},
    Borrowed, Bound, FromPyObject, IntoPyObject as _, Py, PyAny, PyErr, PyResult, Python,
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
    head: RequestHead,
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
    pub(crate) fn new_reqwest_builder(
        &self,
        py: Python<'_>,
        client: &reqwest::Client,
        http3: bool,
    ) -> PyResult<(reqwest::RequestBuilder, Option<Py<PyAny>>)> {
        let mut req_builder = self.head.new_request_builder(py, client, http3)?;
        if let Some(timeout) = get_timeout(py)? {
            req_builder = req_builder.timeout(timeout);
        }
        let mut request_iter: Option<Py<PyAny>> = None;
        if let Some((body, iter)) = self.content_into_reqwest(py) {
            req_builder = req_builder.body(body);
            request_iter = iter;
        }
        Ok((req_builder, request_iter))
    }

    fn content_into_reqwest(&self, py: Python<'_>) -> Option<(reqwest::Body, Option<Py<PyAny>>)> {
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                // TODO: Replace this dance with clone_ref when released.
                // https://github.com/PyO3/pyo3/pull/5654
                // SAFETY: Implementation known never to error, we unwrap to easily
                // switch to clone_ref later.
                let bytes = bytes.into_pyobject(py).unwrap();
                let bytes = PyBackedBytes::from(bytes);
                Some((reqwest::Body::from(Bytes::from_owner(bytes)), None))
            }
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
