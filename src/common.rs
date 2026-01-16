use std::ffi::CStr;

use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    sync::MutexExt as _,
    types::{PyAnyMethods as _, PyBytes, PyString},
    Bound, Py, PyAny, PyResult, Python,
};

use crate::{headers::Headers, shared::constants::Constants};

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

    constants: Constants,
}

#[pymethods]
impl FullResponse {
    #[new]
    pub(crate) fn py_new(
        py: Python<'_>,
        status: u16,
        headers: Py<Headers>,
        content: Py<PyBytes>,
        trailers: Py<Headers>,
    ) -> Self {
        FullResponse::new(
            status,
            headers,
            content,
            trailers,
            Constants::get(py).unwrap(),
        )
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

    fn text<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyString>> {
        let headers: std::sync::MutexGuard<'_, http::HeaderMap<crate::headers::PyHeaderValue>> =
            self.headers.get().store.lock_py_attached(py).unwrap();
        let mut charset_vec: Vec<u8> = Vec::new();
        if let Some(content_type) = headers.get("content-type") {
            if let Some(m) = content_type.as_mime(py) {
                if let Some(charset) = m.get_param("charset") {
                    let charset_bytes = charset.as_str().as_bytes();
                    charset_vec.reserve_exact(charset_bytes.len() + 1);
                    charset_vec.extend_from_slice(charset_bytes);
                    charset_vec.push(0);
                }
            }
        }
        let encoding: Option<&CStr> = if charset_vec.is_empty() {
            None
        } else {
            Some(
                CStr::from_bytes_with_nul(&charset_vec)
                    .map_err(|_| PyRuntimeError::new_err("could not read charset string"))?,
            )
        };
        PyString::from_encoded_object(self.content.bind(py), encoding, None)
    }

    fn json<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.constants.json_loads.bind(py).call1((&self.content,))
    }
}

impl FullResponse {
    pub(crate) fn new(
        status: u16,
        headers: Py<Headers>,
        content: Py<PyBytes>,
        trailers: Py<Headers>,
        constants: Constants,
    ) -> Self {
        Self {
            status,
            headers,
            content,
            trailers,
            constants,
        }
    }
}
