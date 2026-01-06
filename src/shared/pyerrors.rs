use pyo3::{
    exceptions::{PyConnectionError, PyRuntimeError, PyTimeoutError},
    PyErr,
};

pub fn from_reqwest(e: reqwest::Error, msg: &str) -> PyErr {
    let msg = format!("{msg}: {:+}", errors::fmt(&e));
    if e.is_timeout() {
        PyTimeoutError::new_err(msg)
    } else if e.is_connect() {
        PyConnectionError::new_err(msg)
    } else {
        PyRuntimeError::new_err(msg)
    }
}
