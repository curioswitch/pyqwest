use bytes::Bytes;
use pyo3::{exceptions::PyRuntimeError, pyclass, pymethods, Py, PyResult, Python};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::{
    common::HTTPVersion,
    headers::Headers,
    shared::response::{ResponseBody, ResponseHead},
};

#[pyclass]
pub(crate) struct SyncResponse {
    head: ResponseHead,
    content: Py<SyncContentGenerator>,
}

impl SyncResponse {
    pub(super) fn pending(py: Python<'_>) -> PyResult<SyncResponse> {
        Ok(SyncResponse {
            head: ResponseHead::pending(py),
            content: Py::new(
                py,
                SyncContentGenerator {
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
    fn headers(&mut self, py: Python<'_>) -> Py<Headers> {
        self.head.headers(py)
    }

    #[getter]
    fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        self.content.get().body.trailers(py)
    }

    #[getter]
    fn content(&mut self, py: Python<'_>) -> Py<SyncContentGenerator> {
        self.content.clone_ref(py)
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
