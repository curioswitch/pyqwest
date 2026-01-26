use std::ffi::CStr;

use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    sync::MutexExt as _,
    types::{PyAnyMethods as _, PyBytes, PyInt, PyString},
    Bound, Py, PyAny, PyResult, Python,
};

use crate::{headers::Headers, shared::constants::Constants};

/// Decompressers to use in testing transports without additional dependencies.
pub(crate) mod decompress;
/// An enum type corresponding to HTTP header names.
pub(crate) mod headername;
/// An enum type corresponding to HTTP versions.
pub(crate) mod httpversion;

#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct FullResponse {
    #[pyo3(get)]
    status: Py<PyInt>,
    #[pyo3(get)]
    headers: Py<Headers>,
    #[pyo3(get)]
    content: Py<PyBytes>,
    #[pyo3(get)]
    trailers: Py<Headers>,
}

#[pymethods]
impl FullResponse {
    #[new]
    pub(crate) fn py_new(
        status: Py<PyInt>,
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
        Constants::get(py)?
            .json_loads
            .bind(py)
            .call1((&self.content,))
    }
}

impl FullResponse {
    pub(crate) fn new(
        py: Python<'_>,
        status: http::StatusCode,
        headers: Py<Headers>,
        content: Py<PyBytes>,
        trailers: Py<Headers>,
    ) -> PyResult<Self> {
        let constants = Constants::get(py)?;
        Ok(Self {
            status: constants.status_code(py, status),
            headers,
            content,
            trailers,
        })
    }
}
