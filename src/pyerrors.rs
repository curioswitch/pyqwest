use pyo3::{
    create_exception,
    exceptions::{PyConnectionError, PyException, PyRuntimeError, PyTimeoutError},
    pyclass, pymethods,
    types::PyString,
    Py, PyErr, Python,
};

create_exception!(pyqwest, ReadError, PyException);
create_exception!(pyqwest, WriteError, PyException);

pub fn from_reqwest(e: &reqwest::Error, msg: &str) -> PyErr {
    if let Some(e) = errors::find::<h2::Error>(e) {
        if e.is_remote() {
            return PyErr::new::<StreamError, _>(StreamError::as_args(e, msg));
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

#[pyclass(module = "pyqwest", frozen, eq, eq_int)]
#[derive(Clone, PartialEq)]
pub(crate) enum StreamErrorCode {
    #[pyo3(name = "NO_ERROR")]
    NoError,
    #[pyo3(name = "PROTOCOL_ERROR")]
    ProtocolError,
    #[pyo3(name = "INTERNAL_ERROR")]
    InternalError,
    #[pyo3(name = "FLOW_CONTROL_ERROR")]
    FlowControlError,
    #[pyo3(name = "SETTINGS_TIMEOUT")]
    SettingsTimeout,
    #[pyo3(name = "STREAM_CLOSED")]
    StreamClosed,
    #[pyo3(name = "FRAME_SIZE_ERROR")]
    FrameSizeError,
    #[pyo3(name = "REFUSED_STREAM")]
    RefusedStream,
    #[pyo3(name = "CANCEL")]
    Cancel,
    #[pyo3(name = "COMPRESSION_ERROR")]
    CompressionError,
    #[pyo3(name = "CONNECT_ERROR")]
    ConnectError,
    #[pyo3(name = "ENHANCE_YOUR_CALM")]
    EnhanceYourCalm,
    #[pyo3(name = "INADEQUATE_SECURITY")]
    InadequateSecurity,
    #[pyo3(name = "HTTP_1_1_REQUIRED")]
    HTTP11Required,
}

#[pyclass(module = "pyqwest", extends = PyException, frozen)]
pub(crate) struct StreamError {
    message: Py<PyString>,
    #[pyo3(get)]
    code: Py<StreamErrorCode>,
}

#[pymethods]
impl StreamError {
    #[new]
    #[allow(clippy::needless_pass_by_value)]
    fn py_new(message: Py<PyString>, code: Py<StreamErrorCode>) -> Self {
        Self { message, code }
    }

    fn __str__(&self, py: Python<'_>) -> Py<PyString> {
        self.message.clone_ref(py)
    }
}

impl StreamError {
    fn as_args(e: &h2::Error, msg: &str) -> (String, StreamErrorCode) {
        let code = if let Some(code) = e.reason() {
            match code {
                h2::Reason::NO_ERROR => StreamErrorCode::NoError,
                h2::Reason::PROTOCOL_ERROR => StreamErrorCode::ProtocolError,
                h2::Reason::INTERNAL_ERROR => StreamErrorCode::InternalError,
                h2::Reason::FLOW_CONTROL_ERROR => StreamErrorCode::FlowControlError,
                h2::Reason::SETTINGS_TIMEOUT => StreamErrorCode::SettingsTimeout,
                h2::Reason::STREAM_CLOSED => StreamErrorCode::StreamClosed,
                h2::Reason::FRAME_SIZE_ERROR => StreamErrorCode::FrameSizeError,
                h2::Reason::REFUSED_STREAM => StreamErrorCode::RefusedStream,
                h2::Reason::CANCEL => StreamErrorCode::Cancel,
                h2::Reason::COMPRESSION_ERROR => StreamErrorCode::CompressionError,
                h2::Reason::CONNECT_ERROR => StreamErrorCode::ConnectError,
                h2::Reason::ENHANCE_YOUR_CALM => StreamErrorCode::EnhanceYourCalm,
                h2::Reason::INADEQUATE_SECURITY => StreamErrorCode::InadequateSecurity,
                h2::Reason::HTTP_1_1_REQUIRED => StreamErrorCode::HTTP11Required,
                #[allow(clippy::match_same_arms)]
                _ => StreamErrorCode::InternalError,
            }
        } else {
            StreamErrorCode::InternalError
        };

        (format!("{msg}: {:+}", errors::fmt(e)), code)
    }
}
