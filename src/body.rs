use std::pin::Pin;
use std::task::{Context, Poll};

use bytes::Bytes;
use futures_core::Stream;
use pyo3::exceptions::{PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyIterator, PyModule};
use pyo3_async_runtimes::tokio::into_stream_with_locals_v2;
use pyo3_async_runtimes::TaskLocals;
use tokio::sync::mpsc;

enum BodyInner {
    Bytes(Bytes),
    Iter(Py<PyIterator>),
    AsyncIter { iter: Py<PyAny>, locals: TaskLocals },
}

#[pyclass(frozen)]
struct BodyChunk {
    bytes: Bytes,
}

#[pyfunction]
fn wrap_body_chunk(py: Python<'_>, data: Bound<'_, PyAny>) -> PyResult<Py<BodyChunk>> {
    let bytes = data.extract::<Bytes>()?;
    Py::new(py, BodyChunk { bytes })
}

#[pyclass]
pub struct Body {
    inner: BodyInner,
}

#[pymethods]
impl Body {
    #[new]
    fn new(body: Bound<'_, PyAny>) -> PyResult<Self> {
        Self::from_py_any(body)
    }
}

impl Body {
    pub(crate) fn from_py_any<'py>(body: Bound<'py, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = body.extract::<Bytes>() {
            return Ok(Self {
                inner: BodyInner::Bytes(bytes),
            });
        }

        let py = body.py();
        if body.hasattr(pyo3::intern!(py, "__aiter__"))? {
            let aiter = body.call_method0(pyo3::intern!(py, "__aiter__"))?;
            if !aiter.hasattr(pyo3::intern!(py, "__anext__"))? {
                return Err(PyTypeError::new_err(
                    "Async iterator must implement __anext__",
                ));
            }
            let locals = pyo3_async_runtimes::tokio::get_current_locals(py).map_err(|err| {
                PyRuntimeError::new_err(format!(
                    "Async iterator requires a running event loop: {}",
                    err
                ))
            })?;
            let wrapped = wrap_async_iter(py, aiter.unbind())?;
            return Ok(Self {
                inner: BodyInner::AsyncIter {
                    iter: wrapped,
                    locals,
                },
            });
        }

        let iter = PyIterator::from_object(&body).map_err(|err| {
            PyTypeError::new_err(format!(
                "Body must be bytes or an iterator/async iterator yielding bytes: {}",
                err
            ))
        })?;
        Ok(Self {
            inner: BodyInner::Iter(iter.unbind()),
        })
    }

    pub(crate) fn into_reqwest_body(self, py: Python<'_>) -> PyResult<reqwest::Body> {
        match self.inner {
            BodyInner::Bytes(bytes) => Ok(reqwest::Body::from(bytes)),
            BodyInner::Iter(iter) => Ok(reqwest::Body::wrap_stream(IterStream::new(iter))),
            BodyInner::AsyncIter { iter, locals } => {
                let inner = into_stream_with_locals_v2(locals, iter.into_bound(py))?;
                Ok(reqwest::Body::wrap_stream(AsyncIterStream {
                    inner: Box::pin(inner),
                }))
            }
        }
    }
}

#[derive(Debug)]
struct BodyError {
    message: String,
}

impl BodyError {
    fn from_py_err(err: PyErr) -> Self {
        Self {
            message: err.to_string(),
        }
    }
}

impl std::fmt::Display for BodyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for BodyError {}

fn wrap_async_iter(py: Python<'_>, iter: Py<PyAny>) -> PyResult<Py<PyAny>> {
    static WRAP_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
    static GEN_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

    let wrap_fn = WRAP_FN
        .get_or_try_init(py, || {
            pyo3::wrap_pyfunction!(wrap_body_chunk, py).map(|func| func.unbind().into())
        })?
        .clone_ref(py);

    let gen_fn = GEN_FN
        .get_or_try_init(py, || {
            let module = PyModule::import(py, "pyqwest._glue")?;
            module.getattr("wrap_body_gen").map(Bound::unbind)
        })?
        .clone_ref(py);

    gen_fn.call1(py, (iter, wrap_fn))
}

struct IterStream {
    iter: Option<Py<PyIterator>>,
    rx: Option<mpsc::Receiver<Result<Bytes, BodyError>>>,
}

impl IterStream {
    fn new(iter: Py<PyIterator>) -> Self {
        Self {
            iter: Some(iter),
            rx: None,
        }
    }

    fn start(&mut self) {
        if self.rx.is_some() {
            return;
        }

        let iter = match self.iter.take() {
            Some(iter) => iter,
            None => return,
        };
        let (tx, rx) = mpsc::channel(1);
        self.rx = Some(rx);

        tokio::task::spawn_blocking(move || {
            Python::attach(|py| {
                let mut iter = iter.into_bound(py);
                loop {
                    let result = match iter.next() {
                        Some(Ok(item)) => item
                            .extract::<Bytes>()
                            .map(Some)
                            .map_err(|err| BodyError::from_py_err(err.into())),
                        Some(Err(err)) => Err(BodyError::from_py_err(err)),
                        None => Ok(None),
                    };

                    let should_break = py.detach(|| match result {
                        Ok(Some(bytes)) => tx.blocking_send(Ok(bytes)).is_err(),
                        Ok(None) => true,
                        Err(err) => {
                            let _ = tx.blocking_send(Err(err));
                            true
                        }
                    });

                    if should_break {
                        break;
                    }
                }
            });
        });
    }
}

impl Stream for IterStream {
    type Item = Result<Bytes, BodyError>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        if this.rx.is_none() {
            this.start();
        }

        match this.rx.as_mut() {
            Some(rx) => match rx.poll_recv(cx) {
                Poll::Ready(Some(item)) => Poll::Ready(Some(item)),
                Poll::Ready(None) => Poll::Ready(None),
                Poll::Pending => Poll::Pending,
            },
            None => Poll::Ready(None),
        }
    }
}

struct AsyncIterStream {
    inner: Pin<Box<dyn Stream<Item = Py<PyAny>> + Send>>,
}

impl Stream for AsyncIterStream {
    type Item = Result<Bytes, BodyError>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        match this.inner.as_mut().poll_next(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Some(item)) => Poll::Ready(Some(Ok(bytes_from_chunk(item)))),
            Poll::Ready(None) => Poll::Ready(None),
        }
    }
}

fn bytes_from_chunk(item: Py<PyAny>) -> Bytes {
    // Safety: items originate from wrap_body_gen, which yields BodyChunk instances.
    let chunk: Py<BodyChunk> = unsafe { std::mem::transmute(item) };
    chunk.get().bytes.clone()
}
