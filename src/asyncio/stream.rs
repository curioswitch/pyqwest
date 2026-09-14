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
    types::PyAnyMethods as _,
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::{
    tokio::{future_into_py_with_locals, get_current_locals},
    TaskLocals,
};
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio_stream::wrappers::ReceiverStream;

use crate::shared::{
    constants::Constants,
    request::{RequestStreamError, RequestStreamResult},
};

pub(super) fn into_stream(
    py: Python<'_>,
    gen: Bound<'_, PyAny>,
    constants: &Constants,
) -> PyResult<(
    impl Stream<Item = RequestStreamResult<Py<PyAny>>>,
    Py<PyAny>,
)> {
    let locals = get_current_locals(py)?;
    let event_loop = locals.event_loop(py);
    let (tx, rx) = mpsc::channel::<RequestStreamResult<Py<PyAny>>>(10);
    let finished = Arc::new(AtomicBool::new(false));
    let sender = Py::new(
        py,
        Sender {
            locals,
            tx: Mutex::new(Some(tx)),
            finished: Arc::clone(&finished),
        },
    )?;

    let task_consumer = TaskConsumer {
        constants: constants.clone(),
    };
    let coro = constants.forward.bind(py).call1((gen, sender))?;
    let task = event_loop.call_method1(&constants.create_task, (coro,))?;
    task.call_method1(&constants.add_done_callback, (task_consumer,))?;

    let stream = FailUnfinished {
        rx: ReceiverStream::new(rx),
        finished: Some(finished),
    };
    Ok((stream, task.unbind()))
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
    locals: TaskLocals,
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

        let guard = self
            .tx
            .lock_py_attached(py)
            .unwrap_or_else(PoisonError::into_inner);
        // forward() never sends after close; raise rather than panic if it does.
        let Some(tx) = guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Request body sender already closed",
            ));
        };
        match tx.try_send(item) {
            Ok(()) => true.into_py_any(py),
            Err(e) => match e {
                TrySendError::Full(item) => {
                    let tx = tx.clone();
                    future_into_py_with_locals(py, self.locals.clone(), async move {
                        let Some(permit) = tx.reserve().await.ok() else {
                            // receiving side disconnected
                            return Ok(false);
                        };
                        permit.send(item);
                        Ok(true)
                    })
                    .map(Bound::unbind)
                }
                TrySendError::Closed(_) => false.into_py_any(py),
            },
        }
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

#[pyclass(module = "_pyqwest.async", frozen)]
struct TaskConsumer {
    constants: Constants,
}

#[pymethods]
impl TaskConsumer {
    #[allow(clippy::unused_self)]
    fn __call__(&self, future: &Bound<'_, PyAny>) {
        // Suppress errors.
        let _ = future.call_method0(&self.constants.exception);
    }
}
