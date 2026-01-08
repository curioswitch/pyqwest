use pyo3::{pyclass, pymethods, types::PyBytes, Py, Python};

use crate::headers::Headers;

#[pyclass(module = "pyqwest", frozen, eq, eq_int)]
#[derive(Clone, PartialEq)]
pub(crate) enum HTTPVersion {
    HTTP1,
    HTTP2,
    HTTP3,
}

#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct FullResponse {
    pub(crate) status: u16,
    pub(crate) headers: Py<Headers>,
    pub(crate) content: Py<PyBytes>,
    pub(crate) trailers: Py<Headers>,
}

#[pymethods]
impl FullResponse {
    #[new]
    fn py_new(
        status: u16,
        headers: Py<Headers>,
        content: Py<PyBytes>,
        trailers: Py<Headers>,
    ) -> Self {
        Self {
            status,
            headers,
            content,
            trailers,
        }
    }

    #[getter]
    fn status(&self) -> u16 {
        self.status
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.headers.clone_ref(py)
    }

    #[getter]
    fn content(&self, py: Python<'_>) -> Py<PyBytes> {
        self.content.clone_ref(py)
    }

    #[getter]
    fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        self.trailers.clone_ref(py)
    }
}
