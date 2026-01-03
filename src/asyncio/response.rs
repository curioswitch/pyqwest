use pyo3::{
    exceptions::PyStopAsyncIteration, pyclass, pymethods, Bound, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::{
    common::HTTPVersion,
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead},
};

#[pyclass(frozen)]
pub(crate) struct Response {
    head: ResponseHead,
    content: Py<ContentGenerator>,
}

impl Response {
    pub(super) fn pending(py: Python<'_>) -> PyResult<Response> {
        Ok(Response {
            head: ResponseHead::pending(py),
            content: Py::new(
                py,
                ContentGenerator {
                    body: ResponseBody::pending(py),
                },
            )?,
        })
    }

    pub(super) async fn fill(&mut self, response: reqwest::Response) {
        let response: http::Response<_> = response.into();
        let (head, body) = response.into_parts();
        self.head.fill(head);
        self.content.get().body.fill(body).await;
    }
}

#[pymethods]
impl Response {
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
        self.content.get().body.trailers(py)
    }

    #[getter]
    fn content(&self, py: Python<'_>) -> Py<ContentGenerator> {
        self.content.clone_ref(py)
    }
}

#[pyclass(frozen)]
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
