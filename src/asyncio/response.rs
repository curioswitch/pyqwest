use std::sync::Mutex;

use arc_swap::ArcSwapOption;
use pyo3::{
    exceptions::PyStopAsyncIteration,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyBytes, PyInt},
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::{
    asyncio::awaitable::{
        EmptyAsyncIterator, EmptyAwaitable, ErrorAwaitable, ValueAsyncIterator, ValueAwaitable,
    },
    common::httpversion::HTTPVersion,
    headers::Headers,
    shared::{
        buffer::BytesMemoryView,
        constants::Constants,
        response::{ResponseBody, ResponseHead, RustFullResponse},
    },
};

enum Content {
    Http(Py<ContentGenerator>),
    Custom(Py<PyAny>),
}

#[pyclass(module = "_pyqwest.async", frozen)]
pub(crate) struct Response {
    pub(super) head: ResponseHead,
    content: Content,
    trailers: Py<Headers>,
    request_iter_task: Mutex<Option<Py<PyAny>>>,

    constants: Constants,
}

impl Response {
    pub(super) fn pending(
        py: Python<'_>,
        request_iter_task: Option<Py<PyAny>>,
        constants: Constants,
    ) -> PyResult<Response> {
        let trailers = Py::new(py, Headers::empty())?;
        Ok(Response {
            head: ResponseHead::pending(py),
            content: Content::Http(Py::new(
                py,
                ContentGenerator::new(ResponseBody::pending(trailers.clone_ref(py))),
            )?),
            trailers,
            request_iter_task: Mutex::new(request_iter_task),
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

    pub(super) async fn into_full_response(self) -> PyResult<RustFullResponse> {
        let Content::Http(content) = self.content else {
            unreachable!("into_full_response is only called on HTTP responses")
        };
        let body = content.get().body.load();
        // SAFETY - we only call into_full_response without allowing the user to close this response.
        let bytes = body.as_ref().unwrap().read_full().await?;
        Ok(RustFullResponse {
            status: self.head.http_status(),
            headers: self.head.headers,
            body: bytes,
            trailers: self.trailers,
        })
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
        let constants = Constants::get(py)?;
        let http_version = if let Some(http_version) = http_version {
            http_version.get()
        } else {
            constants.http_1.get()
        };
        let content = if let Some(content) = content {
            content
        } else {
            EmptyAsyncIterator.into_bound_py_any(py)?
        };
        let trailers = Headers::from_option(py, trailers)?;
        Ok(Self {
            head: ResponseHead::new(py, status, http_version, headers)?,
            content: Content::Custom(content.unbind()),
            trailers,
            request_iter_task: Mutex::new(None),
            constants,
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
        self.aclose(py)
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

    fn aclose<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let request_iter_task = self.request_iter_task.lock().unwrap().take();
        if let Some(task) = request_iter_task {
            task.call_method0(py, &self.constants.cancel)?;
        }
        match &self.content {
            Content::Http(content) => content.get().aclose(py),
            Content::Custom(content) => {
                if let Ok(close_res) = content.bind(py).call_method0(&self.constants.aclose) {
                    close_res.into_bound_py_any(py)
                } else {
                    EmptyAwaitable.into_bound_py_any(py)
                }
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

#[pyclass(module = "_pyqwest.async", frozen)]
struct ContentGenerator {
    body: ArcSwapOption<ResponseBody>,
}

impl ContentGenerator {
    fn new(body: ResponseBody) -> Self {
        ContentGenerator {
            body: ArcSwapOption::from_pointee(body),
        }
    }
}

#[pymethods]
impl ContentGenerator {
    fn __aiter__(slf: Py<ContentGenerator>) -> Py<ContentGenerator> {
        slf
    }

    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let body = self.body.load();
        let Some(body) = body.as_ref() else {
            return ErrorAwaitable {
                error: Some(PyStopAsyncIteration::new_err(())),
            }
            .into_bound_py_any(py);
        };
        let body = body.clone();
        future_into_py(py, async move {
            let chunk = body.chunk().await?;
            if let Some(bytes) = chunk {
                Ok(BytesMemoryView::new(bytes))
            } else {
                Err(PyStopAsyncIteration::new_err(()))
            }
        })
    }

    fn aclose<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let Some(body) = self.body.swap(None) else {
            return EmptyAwaitable.into_bound_py_any(py);
        };
        if body.try_close() {
            return EmptyAwaitable.into_bound_py_any(py);
        }
        future_into_py(py, async move {
            body.close().await;
            Ok(())
        })
    }
}
