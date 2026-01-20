use pyo3::{exceptions::PyValueError, PyResult};

pub(crate) fn validate_timeout(timeout: Option<f64>) -> PyResult<Option<f64>> {
    if let Some(t) = timeout {
        if t < 0.0 || !t.is_finite() {
            return Err(PyValueError::new_err(
                "Timeout must be non-negative".to_string(),
            ));
        }
    }
    Ok(timeout)
}
