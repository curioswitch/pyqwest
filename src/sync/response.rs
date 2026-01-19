use std::sync::{Arc, Mutex};

use arc_swap::ArcSwapOption;
use bytes::Bytes;
use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyBytes, PyInt, PyTuple},
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::{
    common::{httpversion::HTTPVersion, FullResponse},
    headers::Headers,
    shared::{
        buffer::BytesMemoryView,
        constants::Constants,
        response::{ResponseBody, ResponseHead},
    },
};

enum Content {
    Http(Py<SyncContentGenerator>),
    Custom(Py<PyAny>),
}

pub(super) type RequestIterHandle = Arc<Mutex<Option<Py<PyAny>>>>;

pub(super) fn close_request_iter(
    py: Python<'_>,
    request_iter: &RequestIterHandle,
    constants: &Constants,
) {
    let request_iter = request_iter.lock().unwrap().take();
    if let Some(iter) = request_iter {
        let _ = iter.bind(py).call_method0(&constants.close);
    }
}

#[pyclass(module = "_pyqwest", frozen)]
pub(crate) struct SyncResponse {
    head: ResponseHead,
    content: Content,
    trailers: Py<Headers>,
    request_iter: RequestIterHandle,

    constants: Constants,
}

impl SyncResponse {
    pub(super) fn pending(
        py: Python<'_>,
        request_iter: RequestIterHandle,
        constants: Constants,
    ) -> PyResult<SyncResponse> {
        let trailers = Py::new(py, Headers::empty())?;
        Ok(SyncResponse {
            head: ResponseHead::pending(py),
            content: Content::Http(Py::new(
                py,
                SyncContentGenerator {
                    body: ArcSwapOption::from_pointee(ResponseBody::pending(
                        trailers.clone_ref(py),
                    )),
                    request_iter: request_iter.clone(),
                    constants: constants.clone(),
                },
            )?),
            trailers,
            request_iter,
            constants,
        })
    }

    pub(super) async fn fill(&mut self, response: reqwest::Response) {
        let response: http::Response<_> = response.into();
        let (head, body) = response.into_parts();
        self.head.fill(head);
        if let Content::Http(content) = &self.content {
            let content_body = content.get().body.load();
            // SAFETY: We do not return the response to the user before calling fill so it
            // cannot be closed yet.
            content_body.as_ref().unwrap().fill(body).await;
        } else {
            unreachable!("fill is only called on HTTP responses");
        }
    }
}

#[pymethods]
impl SyncResponse {
    #[new]
    #[pyo3(signature = (*, status, http_version = None, headers = None, content = None, trailers = None))]
    fn py_new(
        py: Python<'_>,
        status: u16,
        http_version: Option<&Bound<'_, HTTPVersion>>,
        headers: Option<Bound<'_, Headers>>,
        content: Option<Bound<'_, PyAny>>,
        trailers: Option<Bound<'_, Headers>>,
    ) -> PyResult<Self> {
        let constants = Constants::get(py)?;
        let http_version = if let Some(http_version) = http_version {
            http_version.get()
        } else {
            constants.http_1.get()
        };
        let content = if let Some(content) = content {
            content
        } else {
            PyTuple::empty(py).into_any().try_iter()?.into_any()
        };
        let trailers: Py<Headers> = Headers::from_option(py, trailers)?;
        Ok(Self {
            head: ResponseHead::new(py, status, http_version, headers)?,
            content: Content::Custom(content.unbind()),
            trailers,
            request_iter: Arc::new(Mutex::new(None)),
            constants,
        })
    }

    pub(super) fn __enter__(slf: Py<SyncResponse>) -> Py<SyncResponse> {
        slf
    }

    pub(super) fn __exit__(
        &self,
        py: Python<'_>,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc_value: Option<&Bound<'_, PyAny>>,
        _traceback: Option<&Bound<'_, PyAny>>,
    ) {
        self.close(py);
    }

    #[getter]
    fn status(&self, py: Python<'_>) -> PyResult<Py<PyInt>> {
        self.head.status(py)
    }

    #[getter]
    fn http_version(&self, py: Python<'_>) -> PyResult<Py<HTTPVersion>> {
        self.head.http_version(py)
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.head.headers(py)
    }

    #[getter]
    fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        self.trailers.clone_ref(py)
    }

    #[getter]
    fn content(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.content {
            Content::Http(content) => Ok(content.clone_ref(py).into_any()),
            Content::Custom(content) => {
                let content = content.bind(py);
                if let Ok(bytes) = content.cast::<PyBytes>() {
                    Ok(PyTuple::new(py, [bytes])?
                        .into_any()
                        .try_iter()?
                        .into_any()
                        .unbind())
                } else {
                    Ok(content.clone().into_any().unbind())
                }
            }
        }
    }

    fn close(&self, py: Python<'_>) {
        close_request_iter(py, &self.request_iter, &self.constants);
        match &self.content {
            Content::Http(content) => content.get().close(py),
            Content::Custom(content) => {
                let _ = content.bind(py).call_method0("close");
            }
        }
    }

    #[getter]
    fn _read_pending(&self, py: Python<'_>) -> bool {
        match &self.content {
            Content::Http(content) => {
                let body = content.get().body.load();
                if let Some(body) = body.as_ref() {
                    body.read_pending()
                } else {
                    false
                }
            }
            Content::Custom(content) => {
                if let Ok(attr) = content.bind(py).getattr("_read_pending") {
                    attr.extract::<bool>().unwrap_or(false)
                } else {
                    false
                }
            }
        }
    }
}

impl SyncResponse {
    pub(super) fn read_full<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let status = self.head.status(py)?;
        let headers = self.head.headers(py);
        let trailers = self.trailers.clone_ref(py);
        let content = match &self.content {
            Content::Http(content) => {
                let body = content.get().body.load();
                if let Some(body) = body.as_ref() {
                    let body = body.clone();
                    let (tx, rx) = oneshot::channel::<PyResult<Bytes>>();
                    get_runtime().spawn(async move { tx.send(body.read_full().await) });
                    let res = py
                        .detach(|| rx.blocking_recv())
                        .map_err(|_| PyRuntimeError::new_err("Failed to receive full response"))
                        .flatten();
                    close_request_iter(py, &self.request_iter, &self.constants);
                    let body = res?;
                    PyBytes::new(py, &body).unbind()
                } else {
                    self.constants.empty_bytes.clone_ref(py)
                }
            }
            Content::Custom(content) => {
                if let Ok(bytes) = content.bind(py).cast::<PyBytes>() {
                    bytes.clone().unbind()
                } else {
                    self.constants
                        .read_content_sync
                        .bind(py)
                        .call1((content.bind(py).try_iter()?,))?
                        .cast::<PyBytes>()?
                        .clone()
                        .unbind()
                }
            }
        };
        FullResponse::py_new(status, headers, content, trailers).into_bound_py_any(py)
    }
}

#[pyclass(module = "_pyqwest.sync", frozen)]
struct SyncContentGenerator {
    body: ArcSwapOption<ResponseBody>,
    request_iter: RequestIterHandle,
    constants: Constants,
}

#[pymethods]
impl SyncContentGenerator {
    fn __iter__(slf: Py<SyncContentGenerator>) -> Py<SyncContentGenerator> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<Option<BytesMemoryView>> {
        let body = self.body.load();
        let Some(body) = body.as_ref() else {
            return Ok(None);
        };
        let body = body.clone();
        let res = py
            .detach(|| {
                let (tx, rx) = oneshot::channel::<PyResult<Option<Bytes>>>();
                get_runtime().spawn(async move {
                    let chunk = body.chunk().await;
                    tx.send(chunk).unwrap();
                });
                rx.blocking_recv()
                    .map_err(|e| PyRuntimeError::new_err(format!("Error receiving chunk: {e}")))
            })
            .flatten()
            .inspect_err(|_| close_request_iter(py, &self.request_iter, &self.constants))?;
        if res.is_none() {
            close_request_iter(py, &self.request_iter, &self.constants);
        }
        Ok(res.map(BytesMemoryView::new))
    }

    fn close(&self, py: Python<'_>) {
        let Some(body) = self.body.swap(None) else {
            return;
        };
        if body.try_close() {
            return;
        }
        let (tx, rx) = oneshot::channel::<()>();
        get_runtime().spawn(async move {
            body.close().await;
            tx.send(()).unwrap();
        });
        py.detach(|| {
            let _ = rx.blocking_recv();
        });
    }
}
