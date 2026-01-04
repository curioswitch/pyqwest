use bytes::Bytes;
use pyo3::{
    exceptions::PyRuntimeError, pyclass, pymethods, Bound, IntoPyObjectExt as _, Py, PyAny,
    PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;
use tokio::sync::oneshot;

use crate::{
    common::HTTPVersion,
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

#[pyclass(frozen)]
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
            SyncEmptyContentGenerator.into_bound_py_any(py)?
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
    fn content(&self, py: Python<'_>) -> Py<PyAny> {
        match &self.content {
            Content::Http(content) => content.clone_ref(py).into_any(),
            Content::Custom { content, .. } => content.clone_ref(py),
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

#[pyclass(module = "pyqwest._sync", frozen)]
struct SyncEmptyContentGenerator;

#[pymethods]
impl SyncEmptyContentGenerator {
    fn __iter__(slf: Py<SyncEmptyContentGenerator>) -> Py<SyncEmptyContentGenerator> {
        slf
    }

    fn __next__<'py>(&self) -> Option<Bytes> {
        None
    }
}
