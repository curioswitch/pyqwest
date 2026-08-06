use pyo3::{
    create_exception,
    exceptions::{PyConnectionError, PyException, PyRuntimeError, PyTimeoutError},
    import_exception, PyErr,
};

create_exception!(pyqwest, ReadError, PyException);
create_exception!(pyqwest, WriteError, PyException);
create_exception!(pyqwest, TooManyRedirects, PyException);
import_exception!(pyqwest._errors, ConnectTimeout);
import_exception!(pyqwest._errors, RemoteProtocolError);
import_exception!(pyqwest._errors, StreamError);

pub fn from_reqwest(e: &reqwest::Error, msg: &str) -> PyErr {
    if let Some(e) = errors::find::<h2::Error>(e) {
        if e.is_remote() {
            let code: u32 = e.reason().unwrap_or(h2::Reason::INTERNAL_ERROR).into();
            return StreamError::new_err((msg.to_string(), code));
        }
    }

    let msg = format!("{msg}: {:+}", errors::fmt(e));
    if e.is_connect() {
        if e.is_timeout() {
            ConnectTimeout::new_err(msg)
        } else {
            PyConnectionError::new_err(msg)
        }
    } else if e.is_timeout() {
        PyTimeoutError::new_err(msg)
    } else if is_peer_protocol_violation(e) {
        RemoteProtocolError::new_err(msg)
    } else if e.is_redirect() {
        TooManyRedirects::new_err(msg)
    } else if e.is_request() {
        WriteError::new_err(msg)
    } else if e.is_body() {
        ReadError::new_err(msg)
    } else {
        PyRuntimeError::new_err(msg)
    }
}

/// Reports whether the error was caused by the peer violating HTTP framing, as
/// opposed to the connection breaking or the request body failing. A message cut
/// short by a clean EOF counts, one cut short by a reset does not.
fn is_peer_protocol_violation(e: &reqwest::Error) -> bool {
    let Some(e) = errors::find::<hyper::Error>(e) else {
        return false;
    };

    // is_parse covers every response head hyper could not decode, including an
    // unparseable status code and an oversized head.
    if e.is_parse() || e.is_incomplete_message() {
        return true;
    }

    // hyper has no predicate for a body it could not frame, reporting one as an
    // io error underneath its body error, so the io error kind is what separates
    // malformed framing from the connection breaking. Reading the io error out of
    // the hyper error rather than the whole chain keeps response decoders, which
    // sit outside hyper and fail with InvalidData on corrupt content, out of this.
    errors::find::<std::io::Error>(e).is_some_and(|e| {
        matches!(
            e.kind(),
            std::io::ErrorKind::InvalidInput | std::io::ErrorKind::UnexpectedEof
        )
    })
}
