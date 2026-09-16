use std::{
    pin::Pin,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, PoisonError,
    },
    task::{ready, Context, Poll},
};

use futures_core::Stream;
use pyo3::{
    exceptions::{PyBaseException, PyRuntimeError},
    pyclass, pymethods,
    sync::MutexExt as _,
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio_stream::wrappers::ReceiverStream;

use crate::{
    asyncio::runtime::{into_awaitable, spawn_pump, AsyncLibrary},
    shared::{
        constants::Constants,
        request::{RequestStreamError, RequestStreamResult},
    },
};

/// Pumps a Python async iterator into a Rust stream from a detached task on
/// `library`, returning the stream and the task's `PumpHandle`.
pub(super) fn into_stream(
    py: Python<'_>,
    gen: Bound<'_, PyAny>,
    constants: &Constants,
    library: AsyncLibrary,
) -> PyResult<(
    impl Stream<Item = RequestStreamResult<Py<PyAny>>>,
    Py<PyAny>,
)> {
    let (tx, rx) = mpsc::channel::<RequestStreamResult<Py<PyAny>>>(10);
    let finished = Arc::new(AtomicBool::new(false));
    let sender = Py::new(
        py,
        Sender {
            library,
            constants: constants.clone(),
            tx: Mutex::new(Some(tx)),
            finished: Arc::clone(&finished),
        },
    )?;

    let handle = spawn_pump(py, library, constants, gen, sender.into_any())?;

    let stream = FailUnfinished {
        rx: ReceiverStream::new(rx),
        finished: Some(finished),
    };
    Ok((stream, handle))
}

/// Fails a body whose sender closed without `Sender::finish`, as when its
/// iterator is interrupted: ending the stream would send the truncated body as
/// complete.
struct FailUnfinished {
    rx: ReceiverStream<RequestStreamResult<Py<PyAny>>>,
    /// Taken when the channel ends, so the error is yielded once.
    finished: Option<Arc<AtomicBool>>,
}

impl Stream for FailUnfinished {
    type Item = RequestStreamResult<Py<PyAny>>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        if let Some(item) = ready!(Pin::new(&mut this.rx).poll_next(cx)) {
            return Poll::Ready(Some(item));
        }
        let truncated = this
            .finished
            .take()
            .is_some_and(|finished| !finished.load(Ordering::Acquire));
        Poll::Ready(truncated.then(|| Err(RequestStreamError::unfinished())))
    }
}

#[pyclass(module = "_pyqwest.async", frozen)]
struct Sender {
    library: AsyncLibrary,
    constants: Constants,
    tx: Mutex<Option<mpsc::Sender<RequestStreamResult<Py<PyAny>>>>>,
    finished: Arc<AtomicBool>,
}

#[pymethods]
impl Sender {
    fn send(&self, py: Python<'_>, item: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let item = if let Ok(item) = item.cast::<PyBaseException>() {
            Err(RequestStreamError::from_py(item))
        } else {
            Ok(item.unbind())
        };

        let (tx, item) = {
            let guard = self
                .tx
                .lock_py_attached(py)
                .unwrap_or_else(PoisonError::into_inner);
            // forward() never sends after close. Raise rather than panic if it
            // does: a panic escaping trio's system task ends the whole run.
            let tx = guard
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("request body sender already closed"))?;
            match tx.try_send(item) {
                Ok(()) => return true.into_py_any(py),
                Err(TrySendError::Closed(_)) => return false.into_py_any(py),
                Err(TrySendError::Full(item)) => (tx.clone(), item),
            }
        };
        into_awaitable(
            py,
            self.library,
            &self.constants,
            async move {
                let Some(permit) = tx.reserve().await.ok() else {
                    // receiving side disconnected
                    return Ok(false);
                };
                permit.send(item);
                Ok(true)
            },
            None,
        )
        .map(Bound::unbind)
    }

    /// Closes the channel with the body complete.
    fn finish(&self, py: Python<'_>) {
        // Set before closing, so the receiver sees it when the channel ends.
        self.finished.store(true, Ordering::Release);
        self.close(py);
    }

    fn close(&self, py: Python<'_>) {
        *self
            .tx
            .lock_py_attached(py)
            .unwrap_or_else(PoisonError::into_inner) = None;
    }
}
