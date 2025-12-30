use pyo3::{
    exceptions::PyStopAsyncIteration, pyclass, pymethods, Bound, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::{
    common::HTTPVersion,
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead},
};

enum Content {
    Http(Option<ResponseBody>),
    Py(Py<ContentGenerator>),
}

#[pyclass]
pub(crate) struct Response {
    head: ResponseHead,
    content: Content,
}

impl Response {
    pub(crate) fn new(response: reqwest::Response) -> Response {
        let response: http::Response<_> = response.into();
        let (head, body) = response.into_parts();

        Response {
            head: ResponseHead::new(head),
            content: Content::Http(Some(ResponseBody::new(body))),
        }
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
    fn headers<'py>(&mut self, py: Python<'py>) -> PyResult<Py<Headers>> {
        self.head.headers(py)
    }

    #[getter]
    fn trailers<'py>(&self, py: Python<'py>) -> PyResult<Option<Py<Headers>>> {
        match &self.content {
            Content::Py(generator) => {
                let content = generator.get();
                content.body.clone().trailers(py)
            }
            _ => Ok(None),
        }
    }

    #[getter]
    fn content<'py>(&mut self, py: Python<'py>) -> PyResult<Py<ContentGenerator>> {
        match &mut self.content {
            Content::Http(body) => {
                let generator = Py::new(
                    py,
                    ContentGenerator {
                        body: body.take().unwrap(),
                    },
                )?;
                self.content = Content::Py(generator.clone_ref(py));
                Ok(generator)
            }
            Content::Py(generator) => Ok(generator.clone_ref(py)),
        }
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
        let mut body = self.body.clone();
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
