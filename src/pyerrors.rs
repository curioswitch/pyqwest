use pyo3::{
    create_exception,
    exceptions::{PyConnectionError, PyException, PyRuntimeError, PyTimeoutError},
    import_exception, PyErr,
};

use crate::shared::request::RequestStreamError;

create_exception!(pyqwest, ReadError, PyException);
create_exception!(pyqwest, WriteError, PyException);
create_exception!(pyqwest, TooManyRedirects, PyException);
import_exception!(pyqwest._errors, ConnectTimeout);
import_exception!(pyqwest._errors, RemoteProtocolError);
import_exception!(pyqwest._errors, StreamError);

pub fn from_reqwest(e: &reqwest::Error, msg: &str) -> PyErr {
    if let Some(e) = errors::find::<h2::Error>(e) {
        if e.is_remote() {
            return stream_error(e, msg);
        }
    }

    // A request body error's source only carries the HTTP/2 reset reason for
    // hyper, so the message ends at the body error.
    let msg = match errors::iter::sources(e)
        .position(<dyn std::error::Error>::is::<RequestStreamError>)
    {
        Some(depth) => format!("{msg}: {:+.*}", depth + 1, errors::fmt(e)),
        None => format!("{msg}: {:+}", errors::fmt(e)),
    };
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

/// The error for an HTTP/2 stream the peer ended with a `RST_STREAM` or GOAWAY
/// frame.
///
/// h2 fails a stream with a GOAWAY only when the stream's identifier is above
/// the frame's last stream identifier, which RFC 9113 §6.8 guarantees the peer
/// did not process. For a graceful GOAWAY the reason is `NO_ERROR`, which
/// describes the connection, not the stream, so the stream is reported with
/// `REFUSED_STREAM`, the code a `RST_STREAM` carries for the same outcome
/// (§8.7). A GOAWAY with any other reason is a connection error, and keeps it.
///
/// reqwest resends a request whose stream was refused either way, on another
/// connection, when the request body can be replayed, so one of these reaching
/// Python means the body was streamed or the retries ran out.
fn stream_error(e: &h2::Error, msg: &str) -> PyErr {
    let reason = e.reason().unwrap_or(h2::Reason::INTERNAL_ERROR);
    let (code, msg) = if e.is_go_away() && reason == h2::Reason::NO_ERROR {
        (
            h2::Reason::REFUSED_STREAM,
            format!("{msg}: stream refused by GOAWAY: {e}"),
        )
    } else {
        (reason, format!("{msg}: {e}"))
    };
    StreamError::new_err((msg, u32::from(code)))
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
