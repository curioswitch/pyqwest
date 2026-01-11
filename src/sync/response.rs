use bytes::Bytes;
use http::HeaderMap;
use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyBytes, PyBytesMethods as _, PyTuple},
    Bound, IntoPyObject as _, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::{
    common::{FullResponse, HTTPVersion},
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead},
};

enum Content {
    Http(Py<SyncContentGenerator>),
    Custom {
        content: Py<PyAny>,
        trailers: Py<Headers>,
    },
}

#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct SyncResponse {
    head: ResponseHead,
    content: Content,
}

impl SyncResponse {
    pub(super) fn pending(py: Python<'_>) -> PyResult<SyncResponse> {
        Ok(SyncResponse {
            head: ResponseHead::pending(py),
            content: Content::Http(Py::new(
                py,
                SyncContentGenerator {
                    body: ResponseBody::pending(py),
                },
            )?),
        })
    }

    pub(super) async fn fill(&mut self, response: reqwest::Response) {
        let response: http::Response<_> = response.into();
        let (head, body) = response.into_parts();
        self.head.fill(head);
        if let Content::Http(content) = &self.content {
            content.get().body.fill(body).await;
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
        let http_version = if let Some(http_version) = http_version {
            http_version.get()
        } else {
            &HTTPVersion::HTTP1
        };
        let content = if let Some(content) = content {
            content
        } else {
            PyTuple::empty(py).into_any().try_iter()?.into_any()
        };
        let trailers: Py<Headers> = Headers::from_option(py, trailers)?;
        Ok(Self {
            head: ResponseHead::new(py, status, http_version, headers)?,
            content: Content::Custom {
                content: content.unbind(),
                trailers,
            },
        })
    }

    fn __enter__(slf: Py<SyncResponse>) -> Py<SyncResponse> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _exc_type: Py<PyAny>,
        _exc_value: Py<PyAny>,
        _traceback: Py<PyAny>,
    ) {
        self.close(py);
    }

    #[getter]
    fn status(&self) -> u16 {
        self.head.status()
    }

    #[getter]
    fn http_version(&self) -> HTTPVersion {
        self.head.http_version()
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.head.headers(py)
    }

    #[getter]
    fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        match &self.content {
            Content::Http(content) => content.get().body.trailers(py),
            Content::Custom { trailers, .. } => trailers.clone_ref(py),
        }
    }

    #[getter]
    fn content(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.content {
            Content::Http(content) => Ok(content.clone_ref(py).into_any()),
            Content::Custom { content, .. } => {
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

    pub(super) fn read_full<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let status = self.head.status();
        let headers = self.head.headers(py);
        match &self.content {
            Content::Http(content) => {
                let body = content.get().body.clone();
                let (tx, rx) = oneshot::channel::<PyResult<(Bytes, HeaderMap)>>();
                get_runtime().spawn(async move { tx.send(body.read_full().await) });
                let (body, trailers) = py
                    .detach(|| rx.blocking_recv())
                    .map_err(|_| PyRuntimeError::new_err("Failed to receive full response"))??;
                FullResponse {
                    status,
                    headers,
                    content: PyBytes::new(py, &body).unbind(),
                    trailers: {
                        let py_trailers = Headers::empty();
                        py_trailers.fill(trailers);
                        py_trailers.into_pyobject(py)?.unbind()
                    },
                }
                .into_bound_py_any(py)
            }
            Content::Custom { content, trailers } => {
                let mut body = Vec::new();
                if let Ok(bytes) = content.bind(py).cast::<PyBytes>() {
                    body.extend_from_slice(bytes.as_bytes());
                } else {
                    for chunk in content.bind(py).try_iter()? {
                        let chunk_py = chunk?;
                        let bytes = chunk_py.cast::<PyBytes>()?;
                        body.extend_from_slice(bytes.as_bytes());
                    }
                }
                FullResponse {
                    status,
                    headers,
                    content: PyBytes::new(py, &body).unbind(),
                    trailers: trailers.clone_ref(py),
                }
                .into_bound_py_any(py)
            }
        }
    }

    #[getter]
    fn _read_pending(&self) -> bool {
        match &self.content {
            Content::Http(content) => content.get().body.read_pending(),
            Content::Custom { .. } => false,
        }
    }

    fn close(&self, py: Python<'_>) {
        if let Content::Http(content) = &self.content {
            if content.get().body.try_close() {
                return;
            }
            let (tx, rx) = oneshot::channel::<()>();
            let body = content.get().body.clone();
            get_runtime().spawn(async move {
                body.close().await;
                tx.send(()).unwrap();
            });
            py.detach(|| {
                let _ = rx.blocking_recv();
            });
        }
    }
}

#[pyclass(module = "pyqwest._sync", frozen)]
struct SyncContentGenerator {
    body: ResponseBody,
}

#[pymethods]
impl SyncContentGenerator {
    fn __iter__(slf: Py<SyncContentGenerator>) -> Py<SyncContentGenerator> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<Option<Bytes>> {
        py.detach(|| {
            let (tx, rx) = oneshot::channel::<PyResult<Option<Bytes>>>();
            let body = self.body.clone();
            get_runtime().spawn(async move {
                let chunk = body.chunk().await;
                tx.send(chunk).unwrap();
            });
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving chunk: {e}")))
        })?
    }
}
