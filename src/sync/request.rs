use bytes::Bytes;
use pyo3::{
    exceptions::PyTypeError,
    pybacked::PyBackedBytes,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyIterator, PyList, PyString},
    Borrowed, Bound, FromPyObject, IntoPyObjectExt as _, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::{
    common::multipart::Multipart,
    headers::Headers,
    shared::{
        constants::Constants,
        request::{
            maybe_encode_json_content, multipart_content_type, RequestHead, RequestStreamError,
            RequestStreamResult,
        },
    },
    sync::timeout::get_timeout,
};

#[pyclass(module = "_pyqwest", frozen)]
pub struct SyncRequest {
    pub(super) head: RequestHead,
    content: Option<Content>,
    constants: Constants,
}

#[pymethods]
impl SyncRequest {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None, *, params=None))]
    pub(crate) fn py_new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
        params: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        Self::new(
            py,
            method,
            url,
            headers,
            content,
            params,
            &Constants::get(py)?,
        )
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
            Some(Content::Bytes(bytes)) => bytes.into_bound_py_any(py),
            Some(Content::Iter(iter)) => Ok(iter.bind(py).clone().into_any()),
            Some(Content::Multipart(multipart)) => Ok(multipart.bind(py).clone().into_any()),
            None => Ok(self.constants.empty_bytes.bind(py).clone().into_any()),
        }
    }

    #[getter]
    fn _json(&self) -> bool {
        self.head.json()
    }
}

impl SyncRequest {
    pub(crate) fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
        params: Option<Bound<'py, PyAny>>,
        constants: &Constants,
    ) -> PyResult<Self> {
        let headers = Headers::from_option(py, headers)?;
        let (content, json) =
            if let Some(content) = maybe_encode_json_content(py, content.as_ref(), constants)? {
                (Some(content), true)
            } else {
                (content, false)
            };
        let content: Option<Content> = match content {
            Some(content) => Some(content.extract()?),
            None => None,
        };
        Ok(Self {
            head: RequestHead::new(method, url, headers, params, json)?,
            content,
            constants: constants.clone(),
        })
    }

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
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                *req.body_mut() = Some(reqwest::Body::from(Bytes::from_owner(bytes.clone_ref(py))));
            }
            Some(Content::Iter(iter)) => {
                *req.body_mut() = Some(iter_into_body(py, iter));
                request_iter = Some(iter.clone_ref(py).into_any());
            }
            Some(Content::Multipart(multipart)) => {
                let mut readers: Vec<IterReader> = Vec::new();
                let mut iters: Vec<Py<PyAny>> = Vec::new();
                let form = multipart.get().build_form(py, |py, stream| {
                    let iter = stream
                        .bind(py)
                        .try_iter()
                        .map_err(|_| {
                            PyTypeError::new_err(
                                "Part content must be bytes, str, or an iterator of bytes",
                            )
                        })?
                        .unbind();
                    let (tx, rx) = mpsc::channel::<RequestStreamResult<Bytes>>(1);
                    readers.push((iter.clone_ref(py), tx));
                    iters.push(iter.into_any());
                    Ok(reqwest::Body::wrap_stream(ReceiverStream::new(rx)))
                })?;
                // Spawned only after every part converted successfully, so
                // that a conversion error in a later part does not leave a
                // thread consuming the earlier parts' iterators.
                spawn_iter_reader(readers);
                req.headers_mut().insert(
                    http::header::CONTENT_TYPE,
                    multipart_content_type(form.boundary())?,
                );
                *req.body_mut() = Some(reqwest::Body::wrap_stream(form.into_stream()));
                request_iter = if iters.len() <= 1 {
                    iters.pop()
                } else {
                    Some(PyList::new(py, &iters)?.unbind().into_any())
                };
            }
            None => {}
        }
        Ok((req, request_iter))
    }
}

type IterReader = (Py<PyIterator>, mpsc::Sender<RequestStreamResult<Bytes>>);

/// Reads a Python iterator of bytes on a blocking thread, streaming the chunks
/// into a request body.
fn iter_into_body(py: Python<'_>, iter: &Py<PyIterator>) -> reqwest::Body {
    let (tx, rx) = mpsc::channel::<RequestStreamResult<Bytes>>(1);
    spawn_iter_reader(vec![(iter.clone_ref(py), tx)]);
    reqwest::Body::wrap_stream(ReceiverStream::new(rx))
}

/// Reads Python iterators of bytes on a single blocking thread, streaming the
/// chunks into their channels in order. Multipart parts are consumed
/// sequentially by reqwest, so all of a form's parts can share one thread.
fn spawn_iter_reader(readers: Vec<IterReader>) {
    if readers.is_empty() {
        return;
    }
    get_runtime().spawn_blocking(move || {
        Python::attach(|py| {
            'readers: for (iter, tx) in readers {
                let mut read_iter = iter.into_bound(py);
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
                        // The remaining parts are moot once the request body
                        // errors or is dropped.
                        break 'readers;
                    }
                }
            }
        });
    });
}

enum Content {
    Bytes(PyBackedBytes),
    Iter(Py<PyIterator>),
    Multipart(Py<Multipart>),
}

impl FromPyObject<'_, '_> for Content {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(multipart) = obj.cast::<Multipart>() {
            return Ok(Self::Multipart(multipart.to_owned().unbind()));
        }
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }

        Ok(Self::Iter(obj.try_iter()?.unbind()))
    }
}
