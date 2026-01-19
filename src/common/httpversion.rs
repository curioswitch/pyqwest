use pyo3::{pyclass, pymethods, types::PyString, Py, PyResult, Python};

use crate::shared::constants::Constants;

/// An enumeration of HTTP versions.
#[pyclass(module = "pyqwest", frozen, eq, ord)]
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
    pub(crate) fn from_rust(version: http::Version, py: Python<'_>) -> PyResult<Py<Self>> {
        let constants = Constants::get(py)?;
        match version {
            http::Version::HTTP_2 => Ok(constants.http_2.clone_ref(py)),
            http::Version::HTTP_3 => Ok(constants.http_3.clone_ref(py)),
            _ => Ok(constants.http_1.clone_ref(py)),
        }
    }

    pub(crate) fn as_rust(&self) -> http::Version {
        self.rs
    }
}

impl PartialEq for HTTPVersion {
    fn eq(&self, other: &Self) -> bool {
        self.rs == other.rs
    }
}

impl PartialOrd for HTTPVersion {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        self.rs.partial_cmp(&other.rs)
    }
}
