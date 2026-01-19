use pyo3::{pyclass, pymethods, types::PyString, Py, Python};

/// An enumeration of HTTP versions.
#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct HTTPVersion {
    py: Py<PyString>,
    rs: http::Version,
}

#[pymethods]
impl HTTPVersion {
    /// HTTP/1.1.
    #[pyo3(name = "HTTP1")]
    #[classattr]
    fn http1(py: Python<'_>) -> Self {
        Self {
            py: PyString::new(py, "HTTP/1.1").unbind(),
            rs: http::Version::HTTP_11,
        }
    }

    /// HTTP/2.
    #[pyo3(name = "HTTP2")]
    #[classattr]
    fn http2(py: Python<'_>) -> Self {
        Self {
            py: PyString::new(py, "HTTP/2").unbind(),
            rs: http::Version::HTTP_2,
        }
    }

    /// HTTP/3.
    #[pyo3(name = "HTTP3")]
    #[classattr]
    fn http3(py: Python<'_>) -> Self {
        Self {
            py: PyString::new(py, "HTTP/3").unbind(),
            rs: http::Version::HTTP_3,
        }
    }

    fn __str__(&self, py: Python<'_>) -> Py<PyString> {
        self.py.clone_ref(py)
    }

    fn __repr__(&self, py: Python<'_>) -> Py<PyString> {
        let repr = format!(
            "HTTPVersion.{}",
            match self.rs {
                http::Version::HTTP_2 => "HTTP2",
                http::Version::HTTP_3 => "HTTP3",
                _ => "HTTP1",
            }
        );
        PyString::new(py, &repr).unbind()
    }
}

impl HTTPVersion {
    pub(crate) fn as_rust(&self) -> http::Version {
        self.rs
    }
}
