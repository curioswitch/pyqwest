use std::sync::Arc;

use bytes::Bytes;
use http::response::Parts;
use http_body::Frame;
use http_body_util::BodyExt as _;
use pyo3::{
    exceptions::PyRuntimeError,
    types::{PyBytes, PyInt},
    Bound, IntoPyObject, Py, PyErr, PyResult, Python,
};
use tokio::sync::{watch, Mutex};

use crate::{
    common::{httpversion::HTTPVersion, FullResponse},
    headers::Headers,
    pyerrors::{self, ReadError},
    shared::constants::Constants,
};

pub(crate) struct ResponseHead {
    status: http::StatusCode,
    version: http::Version,
    pub(crate) headers: Py<Headers>,
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
        let version = http_version.as_rust();
        let headers = Headers::from_option(py, headers)?;
        Ok(ResponseHead {
            status: http::StatusCode::from_u16(status)
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid status code: {e}")))?,
            version,
            headers,
        })
    }

    pub(crate) fn status(&self, py: Python<'_>) -> PyResult<Py<PyInt>> {
        let constants = Constants::get(py)?;
        Ok(constants.status_code(py, self.status))
    }

    pub(crate) fn http_status(&self) -> http::StatusCode {
        self.status
    }

    pub(crate) fn http_version(&self, py: Python<'_>) -> PyResult<Py<HTTPVersion>> {
        HTTPVersion::from_rust(self.version, py)
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
    pub(crate) fn pending(trailers: Py<Headers>) -> Self {
        let (cancel_tx, _) = watch::channel(false);
        ResponseBody {
            inner: Arc::new(ResponseBodyInner {
                body: Mutex::new(None),
                trailers,
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
                    return Err(ReadError::new_err("Response body read cancelled"));
                }
                res = body.frame() => res,
            };
            let Some(res) = res else {
                return Ok(None);
            };
            let frame = match res {
                Ok(frame) => frame,
                Err(e) => {
                    if let Some(e) = errors::find::<h2::Error>(&e) {
                        if matches!(e.reason(), Some(h2::Reason::NO_ERROR)) {
                            return Ok(None);
                        }
                    }
                    return Err(pyerrors::from_reqwest(&e, "Error reading content"));
                }
            };
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

    pub(crate) async fn close(&self) {
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

    pub(crate) async fn read_full(&self) -> PyResult<Bytes> {
        let _read_guard = self.inner.read_lock.lock().await;
        let mut cancel_rx = self.inner.cancel_tx.subscribe();
        if *cancel_rx.borrow() {
            return Ok(Bytes::new());
        }
        let Some(body) = ({
            let mut body_guard = self.inner.body.lock().await;
            body_guard.take()
        }) else {
            return Ok(Bytes::new());
        };
        let collected = tokio::select! {
            _ = cancel_rx.changed() => {
                return Err(ReadError::new_err("Read cancelled"));
            }
            res = body.collect() => res,
        };
        let collected =
            collected.map_err(|e| pyerrors::from_reqwest(&e, "Error reading content"))?;

        if let Some(trailers) = collected.trailers() {
            self.inner.trailers.get().fill(trailers.clone());
        }
        Ok(collected.to_bytes())
    }

    pub(crate) fn read_pending(&self) -> bool {
        self.inner.read_lock.try_lock().is_err()
    }
}

/// Information for `FullResponse` that can be constructed without the GIL.
/// We can create this in a tokio future and pyo3 will then call `IntoPyObject`
/// with the GIL outside of tokio.
pub(crate) struct RustFullResponse {
    pub(crate) status: http::StatusCode,
    pub(crate) headers: Py<Headers>,
    pub(crate) body: Bytes,
    pub(crate) trailers: Py<Headers>,
}

impl<'py> IntoPyObject<'py> for RustFullResponse {
    type Target = FullResponse;
    type Output = Bound<'py, FullResponse>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let body = PyBytes::new(py, &self.body);
        FullResponse::new(py, self.status, self.headers, body.unbind(), self.trailers)?
            .into_pyobject(py)
    }
}
