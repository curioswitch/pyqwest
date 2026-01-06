use std::sync::Arc;

use bytes::Bytes;
use http::response::Parts;
use http_body::Frame;
use http_body_util::BodyExt as _;
use pyo3::{exceptions::PyRuntimeError, Bound, Py, PyResult, Python};
use tokio::sync::{watch, Mutex};

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

    pub(crate) fn new(
        py: Python<'_>,
        status: u16,
        http_version: &HTTPVersion,
        headers: Option<Bound<'_, Headers>>,
    ) -> PyResult<Self> {
        let version = match http_version {
            HTTPVersion::HTTP1 => http::Version::HTTP_11,
            HTTPVersion::HTTP2 => http::Version::HTTP_2,
            HTTPVersion::HTTP3 => http::Version::HTTP_3,
        };
        let headers = Headers::from_option(py, headers)?;
        Ok(ResponseHead {
            status: http::StatusCode::from_u16(status)
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid status code: {e}")))?,
            version,
            headers,
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

    pub(crate) fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.headers.clone_ref(py)
    }
}

struct ResponseBodyInner {
    body: Mutex<Option<reqwest::Body>>,
    trailers: Py<Headers>,
    read_lock: Mutex<()>,
    cancel_tx: watch::Sender<bool>,
}

#[derive(Clone)]
pub(crate) struct ResponseBody {
    inner: Arc<ResponseBodyInner>,
}

impl ResponseBody {
    pub(crate) fn pending(py: Python<'_>) -> Self {
        let (cancel_tx, _) = watch::channel(false);
        ResponseBody {
            inner: Arc::new(ResponseBodyInner {
                body: Mutex::new(None),
                trailers: Py::new(py, Headers::empty()).unwrap(),
                read_lock: Mutex::new(()),
                cancel_tx,
            }),
        }
    }

    pub(crate) async fn fill(&self, body: reqwest::Body) {
        let mut self_body = self.inner.body.lock().await;
        *self_body = Some(body);
    }

    pub(crate) async fn chunk(&self) -> PyResult<Option<Bytes>> {
        let _read_guard = self.inner.read_lock.lock().await;
        let mut cancel_rx = self.inner.cancel_tx.subscribe();
        if *cancel_rx.borrow() {
            return Ok(None);
        }
        let Some(mut body) = ({
            let mut body_guard = self.inner.body.lock().await;
            body_guard.take()
        }) else {
            return Ok(None);
        };
        loop {
            let res = tokio::select! {
                _ = cancel_rx.changed() => {
                    return Ok(None);
                }
                res = body.frame() => res,
            };
            let Some(res) = res else {
                return Ok(None);
            };
            let frame = res.map_err(|e| {
                PyRuntimeError::new_err(format!(
                    "Error reading HTTP body frame: {:+}",
                    errors::fmt(&e)
                ))
            })?;
            // A frame is either data or trailers.
            match frame.into_data().map_err(Frame::into_trailers) {
                Ok(buf) => {
                    let mut body_guard = self.inner.body.lock().await;
                    *body_guard = Some(body);
                    return Ok(Some(buf));
                }
                Err(Ok(trailers)) => {
                    self.inner.trailers.get().fill(trailers);
                }
                Err(Err(_)) => (),
            }
        }
    }

    pub(crate) fn trailers(&self, py: Python<'_>) -> Py<Headers> {
        self.inner.trailers.clone_ref(py)
    }

    pub(crate) async fn close(&self) {
        let _ = self.inner.cancel_tx.send(true);
        let _read_guard = self.inner.read_lock.lock().await;
        let mut body = self.inner.body.lock().await;
        *body = None;
    }

    pub(crate) fn try_close(&self) -> bool {
        let _ = self.inner.cancel_tx.send(true);
        let Ok(_read_guard) = self.inner.read_lock.try_lock() else {
            return false;
        };
        if let Ok(mut body) = self.inner.body.try_lock() {
            *body = None;
            true
        } else {
            false
        }
    }
}
