use std::sync::Arc;

use bytes::Bytes;
use http::{response::Parts, HeaderMap};
use http_body::Frame;
use http_body_util::BodyExt as _;
use pyo3::{exceptions::PyRuntimeError, Py, PyResult, Python};
use tokio::sync::Mutex;

use crate::{common::HTTPVersion, headers::Headers};

pub(crate) struct ResponseHead {
    head: Parts,

    /// The response headers. We convert from Rust to Python lazily mainly to make sure
    /// it happens on a Python thread instead of Tokio.
    headers: Option<Py<Headers>>,
}

impl ResponseHead {
    pub(crate) fn new(head: Parts) -> Self {
        ResponseHead {
            head,
            headers: None,
        }
    }

    pub(crate) fn status(&self) -> u16 {
        self.head.status.as_u16()
    }

    pub(crate) fn http_version(&self) -> HTTPVersion {
        match self.head.version {
            http::Version::HTTP_09 => HTTPVersion::HTTP1,
            http::Version::HTTP_10 => HTTPVersion::HTTP1,
            http::Version::HTTP_11 => HTTPVersion::HTTP1,
            http::Version::HTTP_2 => HTTPVersion::HTTP2,
            http::Version::HTTP_3 => HTTPVersion::HTTP3,
            _ => HTTPVersion::HTTP1,
        }
    }

    pub(crate) fn headers<'py>(&mut self, py: Python<'py>) -> PyResult<Py<Headers>> {
        if let Some(headers) = &self.headers {
            Ok(headers.clone_ref(py))
        } else {
            let headers = Py::new(py, Headers::from_response_headers(&self.head.headers))?;
            self.headers = Some(headers.clone_ref(py));
            Ok(headers)
        }
    }
}

enum Trailers {
    Http(HeaderMap),
    Py(Py<Headers>),
    None,
}

struct ResponseBodyInner {
    body: reqwest::Body,

    /// The response trailers. Will only be present after consuming the response content.
    /// If after consumption, it is still None, it means there were no trailers. We
    /// store the trailers as-is before converting to Python to allow the conversion to
    /// happen on a Python thread.
    trailers: Trailers,
}

#[derive(Clone)]
pub(crate) struct ResponseBody {
    inner: Arc<Mutex<ResponseBodyInner>>,
}

impl ResponseBody {
    pub(crate) fn new(body: reqwest::Body) -> Self {
        ResponseBody {
            inner: Arc::new(Mutex::new(ResponseBodyInner {
                body,
                trailers: Trailers::None,
            })),
        }
    }

    pub(crate) async fn chunk(&mut self) -> PyResult<Option<Bytes>> {
        let mut inner = self.inner.lock().await;
        // loop to ignore unrecognized frames
        loop {
            if let Some(res) = inner.body.frame().await {
                let frame = res.map_err(|e| {
                    PyRuntimeError::new_err(format!("Error reading HTTP body frame: {}", e))
                })?;
                // A frame is either data or trailers.
                match frame.into_data().map_err(Frame::into_trailers) {
                    Ok(buf) => {
                        return Ok(Some(buf));
                    }
                    Err(Ok(trailers)) => {
                        inner.trailers = Trailers::Http(trailers);
                    }
                    Err(Err(_)) => (),
                }
            } else {
                return Ok(None);
            }
        }
    }

    pub(crate) fn trailers<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Py<Headers>>> {
        let mut inner = py.detach(|| self.inner.blocking_lock());
        match &inner.trailers {
            Trailers::Py(trailers) => Ok(Some(trailers.clone_ref(py))),
            Trailers::Http(trailers) => {
                let headers = Py::new(py, Headers::from_response_headers(trailers))?;
                inner.trailers = Trailers::Py(headers.clone_ref(py));
                Ok(Some(headers))
            }
            Trailers::None => Ok(None),
        }
    }
}
