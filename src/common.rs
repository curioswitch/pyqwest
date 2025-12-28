use pyo3::pyclass;

#[pyclass(frozen, eq, eq_int)]
#[derive(Clone, PartialEq)]
pub(crate) enum HTTPVersion {
    HTTP1,
    HTTP2,
    HTTP3,
}
