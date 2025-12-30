use bytes::Bytes;
use pyo3::{exceptions::PyRuntimeError, pyclass, pymethods, Py, PyResult, Python};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::{
    common::HTTPVersion,
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead},
};

enum Content {
    Http(Option<ResponseBody>),
    Py(Py<SyncContentGenerator>),
}

#[pyclass]
pub(crate) struct SyncResponse {
    head: ResponseHead,
    content: Content,
}

impl SyncResponse {
    pub(crate) fn new(response: reqwest::Response) -> SyncResponse {
        let response: http::Response<_> = response.into();
        let (head, body) = response.into_parts();

        SyncResponse {
            head: ResponseHead::new(head),
            content: Content::Http(Some(ResponseBody::new(body))),
        }
    }
}

#[pymethods]
impl SyncResponse {
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
    fn content<'py>(&mut self, py: Python<'py>) -> PyResult<Py<SyncContentGenerator>> {
        match &mut self.content {
            Content::Http(body) => {
                let generator = Py::new(
                    py,
                    SyncContentGenerator {
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
struct SyncContentGenerator {
    body: ResponseBody,
}

#[pymethods]
impl SyncContentGenerator {
    fn __iter__(slf: Py<SyncContentGenerator>) -> Py<SyncContentGenerator> {
        slf
    }

    fn __next__<'py>(&self, py: Python<'py>) -> PyResult<Option<Bytes>> {
        py.detach(|| {
            let (tx, rx) = oneshot::channel::<PyResult<Option<Bytes>>>();
            let mut body = self.body.clone();
            get_runtime().spawn(async move {
                let chunk = body.chunk().await;
                tx.send(chunk).unwrap();
            });
            rx.blocking_recv()
                .map_err(|e| PyRuntimeError::new_err(format!("Error receiving chunk: {}", e)))
        })?
    }
}
