use std::sync::Arc;

use bytes::Bytes;
use http::response::Parts;
use http_body::Frame;
use http_body_util::BodyExt as _;
use pyo3::{exceptions::PyRuntimeError, Py, PyResult, Python};
use tokio::sync::Mutex;

use crate::{common::HTTPVersion, headers::Headers};

pub(crate) struct ResponseHead {
    status: http::StatusCode,
    version: http::Version,
    headers: Py<Headers>,
}

impl ResponseHead {
    pub(crate) fn pending(py: Python<'_>) -> Self {
        ResponseHead {
            status: http::StatusCode::INTERNAL_SERVER_ERROR,
            version: http::Version::HTTP_11,
            headers: Py::new(py, Headers::empty()).unwrap(),
        }
    }

    pub(crate) fn fill(&mut self, parts: Parts) {
        self.status = parts.status;
        self.version = parts.version;
        self.headers.get().fill(parts.headers);
    }

    pub(crate) fn from_py(
        py: Python<'_>,
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
                Some(hdrs) => hdrs,
                None => Py::new(py, Headers::empty())?,
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

    pub(crate) fn headers(&mut self, py: Python<'_>) -> Py<Headers> {
        self.headers.clone_ref(py)
    }
}

struct ResponseBodyInner {
    body: reqwest::Body,
    trailers: Py<Headers>,
}

#[derive(Clone)]
pub(crate) struct ResponseBody {
    inner: Arc<Mutex<ResponseBodyInner>>,
}

impl ResponseBody {
    pub(crate) fn pending(py: Python<'_>) -> Self {
        ResponseBody {
            inner: Arc::new(Mutex::new(ResponseBodyInner {
                body: reqwest::Body::from(Bytes::new()),
                trailers: Py::new(py, Headers::empty()).unwrap(),
            })),
        }
    }

    pub(crate) async fn fill(&self, body: reqwest::Body) {
        let mut inner = self.inner.lock().await;
        inner.body = body;
    }

    pub(crate) async fn chunk(&self) -> PyResult<Option<Bytes>> {
        let mut inner = self.inner.lock().await;
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
                        inner.trailers.get().fill(trailers);
                    }
                    Err(Err(_)) => (),
                }
            } else {
                return Ok(None);
            }
        }
    }

    pub(crate) fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        let inner = py.detach(|| self.inner.blocking_lock());
        inner.trailers.clone_ref(py)
    }
}
