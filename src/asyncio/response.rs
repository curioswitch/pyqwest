use std::sync::Mutex;

use pyo3::{
    exceptions::PyStopAsyncIteration,
    intern, pyclass, pymethods,
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyBytes},
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::{
    asyncio::awaitable::{EmptyAsyncIterator, EmptyAwaitable, ValueAsyncIterator, ValueAwaitable},
    common::{FullResponse, HTTPVersion},
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead, RustFullResponse},
};

enum Content {
    Http(Py<ContentGenerator>),
    Custom {
        content: Py<PyAny>,
        trailers: Py<Headers>,
    },
}

#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct Response {
    pub(super) head: ResponseHead,
    content: Content,
    request_iter_task: Mutex<Option<Py<PyAny>>>,
}

impl Response {
    pub(super) fn pending(
        py: Python<'_>,
        request_iter_task: Option<Py<PyAny>>,
    ) -> PyResult<Response> {
        Ok(Response {
            head: ResponseHead::pending(py),
            content: Content::Http(Py::new(
                py,
                ContentGenerator {
                    body: ResponseBody::pending(py),
                },
            )?),
            request_iter_task: Mutex::new(request_iter_task),
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

    pub(super) async fn into_full_response(self) -> RustFullResponse {
        let status = self.head.status();
        let headers = self.head.headers;
        let Content::Http(content) = self.content else {
            unreachable!("into_full_response is only called on HTTP responses")
        };
        let body = content.get().body.clone();
        let (bytes, trailers) = body.read_full().await.unwrap();
        RustFullResponse {
            status,
            headers,
            body: bytes,
            trailers,
        }
    }
}

#[pymethods]
impl Response {
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
            EmptyAsyncIterator.into_bound_py_any(py)?
        };
        let trailers = Headers::from_option(py, trailers)?;
        Ok(Self {
            head: ResponseHead::new(py, status, http_version, headers)?,
            content: Content::Custom {
                content: content.unbind(),
                trailers,
            },
            request_iter_task: Mutex::new(None),
        })
    }

    fn __aenter__(slf: Py<Response>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        ValueAwaitable {
            value: Some(slf.into_any()),
        }
        .into_py_any(py)
    }

    fn __aexit__<'py>(
        &self,
        py: Python<'py>,
        _exc_type: Py<PyAny>,
        _exc_value: Py<PyAny>,
        _traceback: Py<PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.close(py)
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
                    ValueAsyncIterator {
                        value: Some(bytes.into_py_any(py)?),
                    }
                    .into_py_any(py)
                } else {
                    Ok(content.clone().into_any().unbind())
                }
            }
        }
    }

    fn read_full<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let status = self.head.status();
        let headers = self.head.headers(py);
        match &self.content {
            Content::Http(content) => {
                let body = content.get().body.clone();
                future_into_py(py, async move {
                    let (bytes, trailers) = body.read_full().await?;
                    Ok(RustFullResponse {
                        status,
                        headers,
                        body: bytes,
                        trailers,
                    })
                })
            }
            Content::Custom { content, trailers } => {
                if let Ok(bytes) = content.bind(py).cast::<PyBytes>() {
                    FullResponse::py_new(
                        status,
                        headers,
                        bytes.clone().unbind(),
                        trailers.clone_ref(py),
                    )
                    .into_bound_py_any(py)
                } else {
                    new_full_response(py, status, &headers, content, trailers)
                }
            }
        }
    }

    fn close<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let request_iter_task = self.request_iter_task.lock().unwrap().take();
        if let Some(task) = request_iter_task {
            task.call_method0(py, intern!(py, "cancel"))?;
        }
        if let Content::Http(content) = &self.content {
            if content.get().body.try_close() {
                return EmptyAwaitable.into_bound_py_any(py);
            }
            let body = content.get().body.clone();
            future_into_py(py, async move {
                body.close().await;
                Ok(())
            })
        } else {
            EmptyAwaitable.into_bound_py_any(py)
        }
    }

    #[getter]
    fn _read_pending(&self) -> bool {
        match &self.content {
            Content::Http(content) => content.get().body.read_pending(),
            Content::Custom { .. } => false,
        }
    }
}

#[pyclass(module = "pyqwest._async", frozen)]
struct ContentGenerator {
    body: ResponseBody,
}

#[pymethods]
impl ContentGenerator {
    fn __aiter__(slf: Py<ContentGenerator>) -> Py<ContentGenerator> {
        slf
    }

    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let body = self.body.clone();
        future_into_py(py, async move {
            let chunk = body.chunk().await?;
            if let Some(bytes) = chunk {
                Ok(bytes)
            } else {
                Err(PyStopAsyncIteration::new_err(()))
            }
        })
    }
}

fn new_full_response<'py>(
    py: Python<'py>,
    status: u16,
    headers: &Py<Headers>,
    content: &Py<PyAny>,
    trailers: &Py<Headers>,
) -> PyResult<Bound<'py, PyAny>> {
    static NEW_FULL_RESPONSE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

    let new_full_response = NEW_FULL_RESPONSE.get_or_try_init(py, || {
        let module = py.import("pyqwest._glue")?;
        module.getattr("new_full_response").map(Bound::unbind)
    })?;
    new_full_response
        .bind(py)
        .call1((status, headers, content, trailers))
}
