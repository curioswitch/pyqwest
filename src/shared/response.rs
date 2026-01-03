use std::sync::Arc;

use bytes::Bytes;
use http::{response::Parts, HeaderMap};
use http_body::Frame;
use http_body_util::BodyExt as _;
use pyo3::{exceptions::PyRuntimeError, Py, PyResult, Python};
use tokio::sync::Mutex;

use crate::{common::HTTPVersion, headers::Headers};

enum ResponseHeaders {
    Http(HeaderMap),
    Py(Py<Headers>),
}

pub(crate) struct ResponseHead {
    status: http::StatusCode,
    version: http::Version,

    /// The response headers. We convert from Rust to Python lazily mainly to make sure
    /// it happens on a Python thread instead of Tokio.
    headers: ResponseHeaders,
}

impl ResponseHead {
    pub(crate) fn new(head: Parts) -> Self {
        ResponseHead {
            status: head.status,
            version: head.version,
            headers: ResponseHeaders::Http(head.headers),
        }
    }

    pub(crate) fn from_py(
        status: u16,
        http_version: &HTTPVersion,
        headers: Option<Py<Headers>>,
    ) -> PyResult<Self> {
        let version = match http_version {
            HTTPVersion::HTTP1 => http::Version::HTTP_11,
            HTTPVersion::HTTP2 => http::Version::HTTP_2,
            HTTPVersion::HTTP3 => http::Version::HTTP_3,
        };
        Ok(ResponseHead {
            status: http::StatusCode::from_u16(status)
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid status code: {e}")))?,
            version,
            headers: match headers {
                Some(hdrs) => ResponseHeaders::Py(hdrs),
                None => ResponseHeaders::Http(HeaderMap::new()),
            },
        })
    }

    pub(crate) fn status(&self) -> u16 {
        self.status.as_u16()
    }

    pub(crate) fn http_version(&self) -> HTTPVersion {
        match self.version {
            http::Version::HTTP_2 => HTTPVersion::HTTP2,
            http::Version::HTTP_3 => HTTPVersion::HTTP3,
            _ => HTTPVersion::HTTP1,
        }
    }

    pub(crate) fn headers(&mut self, py: Python<'_>) -> PyResult<Py<Headers>> {
        match &self.headers {
            ResponseHeaders::Py(headers) => Ok(headers.clone_ref(py)),
            ResponseHeaders::Http(headers) => {
                let headers = Py::new(py, Headers::from_response_headers(py, headers))?;
                self.headers = ResponseHeaders::Py(headers.clone_ref(py));
                Ok(headers)
            }
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
                    PyRuntimeError::new_err(format!("Error reading HTTP body frame: {e}"))
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

    pub(crate) fn trailers(&mut self, py: Python<'_>) -> PyResult<Option<Py<Headers>>> {
        let mut inner = py.detach(|| self.inner.blocking_lock());
        match &inner.trailers {
            Trailers::Py(trailers) => Ok(Some(trailers.clone_ref(py))),
            Trailers::Http(trailers) => {
                let headers = Py::new(py, Headers::from_response_headers(py, trailers))?;
                inner.trailers = Trailers::Py(headers.clone_ref(py));
                Ok(Some(headers))
            }
            Trailers::None => Ok(None),
        }
    }
}
