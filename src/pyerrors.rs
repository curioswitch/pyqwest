use pyo3::{
    create_exception,
    exceptions::{PyConnectionError, PyException, PyRuntimeError, PyTimeoutError},
    import_exception, PyErr,
};

create_exception!(pyqwest, ReadError, PyException);
create_exception!(pyqwest, WriteError, PyException);
import_exception!(pyqwest._errors, StreamError);

pub fn from_reqwest(e: &reqwest::Error, msg: &str) -> PyErr {
    if let Some(e) = errors::find::<h2::Error>(e) {
        if e.is_remote() {
            let code: u32 = e.reason().unwrap_or(h2::Reason::INTERNAL_ERROR).into();
            return StreamError::new_err((msg.to_string(), code));
        }
    }

    let msg = format!("{msg}: {:+}", errors::fmt(e));
    if e.is_timeout() {
        PyTimeoutError::new_err(msg)
    } else if e.is_connect() {
        PyConnectionError::new_err(msg)
    } else if e.is_request() {
        WriteError::new_err(msg)
    } else if e.is_body() {
        ReadError::new_err(msg)
    } else {
        PyRuntimeError::new_err(msg)
    }
}
