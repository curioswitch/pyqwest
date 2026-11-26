//! Waiting for tokio work from sync calls.
//!
//! A sync call normally blocks its thread until the tokio task finishes. Under
//! gevent, that would block every greenlet on the thread. gevent patches
//! `select.select` to yield to its hub, so if it was patched when pyqwest was
//! imported, sync calls wait in `select` on a socket that the tokio task writes
//! to when it's done. As with the asyncio waker, tokio threads never take the
//! GIL to wake the caller.

use std::{
    future::Future,
    sync::{Arc, Mutex, PoisonError},
};

use pyo3::{
    exceptions::PyRuntimeError,
    intern,
    sync::{MutexExt as _, PyOnceLock},
    types::{PyAnyMethods as _, PyCFunction, PyModule, PyTuple},
    Bound, Py, PyAny, PyErr, PyResult, Python,
};
use tokio::sync::oneshot::{self, error::TryRecvError};

use crate::shared::{
    runtime::get_runtime,
    wakeup::{drain_wakeups, nonblocking_socket_pair, socket_fd, write_wakeup, WakerSocket},
};

/// Runs `future` on the tokio runtime and returns its output. If
/// `select.select` was patched at import, waits in it so other greenlets can
/// run. Otherwise, blocks the thread.
///
/// If the patched `select` raises, e.g. `gevent.Timeout` or `GreenletExit`,
/// `future` is cancelled and the error is returned.
pub(crate) fn wait<T, F>(py: Python<'_>, future: F) -> PyResult<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    if let Some(Some(select)) = PATCHED_SELECT.get(py) {
        // If we can't get a socket, fall back to blocking.
        if let Some(waker) = take_waker(py) {
            return wait_with_select(py, select.bind(py), waker, future);
        }
    }
    let (tx, rx) = oneshot::channel();
    get_runtime().spawn(async move {
        let _ = tx.send(future.await);
    });
    py.detach(|| rx.blocking_recv()).map_err(|_| task_failed())
}

/// `select.select` as of import if it was patched, otherwise `None`. Any
/// `select` that isn't the builtin C function counts as patched.
static PATCHED_SELECT: PyOnceLock<Option<Py<PyAny>>> = PyOnceLock::new();

/// Checks `select.select` once at import, so sync calls don't pay for the
/// check. gevent has to patch before pyqwest is imported.
pub(crate) fn init(py: Python<'_>) -> PyResult<()> {
    let select = PyModule::import(py, "select")?.getattr(intern!(py, "select"))?;
    PATCHED_SELECT.get_or_init(py, || {
        (!select.is_instance_of::<PyCFunction>()).then(|| select.unbind())
    });
    Ok(())
}

fn wait_with_select<T, F>(
    py: Python<'_>,
    select: &Bound<'_, PyAny>,
    waker: Arc<SyncWaker>,
    future: F,
) -> PyResult<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    let (tx, mut rx) = oneshot::channel();
    let task_waker = waker.clone();
    let task = get_runtime().spawn(async move {
        // Send the output before writing the wakeup, so a woken waiter always
        // finds it.
        let _ = tx.send(future.await);
        let _ = write_wakeup(&task_waker.writer);
    });
    let rlist = PyTuple::new(py, [socket_fd(&waker.reader)])?;
    let empty = PyTuple::empty(py);
    loop {
        // Drain before checking for the output, so if the output arrives after
        // the check, its wakeup is still there for `select`. A stale wakeup
        // from an earlier use of the socket just costs one extra loop.
        drain_wakeups(&waker.reader);
        match rx.try_recv() {
            Ok(output) => {
                put_waker(py, waker);
                return Ok(output);
            }
            Err(TryRecvError::Closed) => return Err(task_failed()),
            Err(TryRecvError::Empty) => {}
        }
        if let Err(e) = select.call1((&rlist, &empty, &empty)) {
            // Nobody will read the output, so cancel the task. Don't reuse the
            // socket, since the task may still write to it.
            task.abort();
            return Err(e);
        }
    }
}

fn task_failed() -> PyErr {
    PyRuntimeError::new_err("pyqwest runtime task ended without a result")
}

/// The socket pair for one wait. The caller waits on `reader`, and the tokio
/// task writes to `writer`.
struct SyncWaker {
    reader: WakerSocket,
    writer: WakerSocket,
}

/// Socket pairs from finished waits, reused so we don't create a pair for every
/// request and every streamed chunk.
static IDLE_WAKERS: Mutex<Vec<Arc<SyncWaker>>> = Mutex::new(Vec::new());

/// The maximum number of idle socket pairs to keep. Extra pairs are closed when
/// their wait finishes.
const MAX_IDLE_WAKERS: usize = 64;

fn take_waker(py: Python<'_>) -> Option<Arc<SyncWaker>> {
    let idle = IDLE_WAKERS
        .lock_py_attached(py)
        .unwrap_or_else(PoisonError::into_inner)
        .pop();
    idle.or_else(|| {
        let (reader, writer) = nonblocking_socket_pair().ok()?;
        Some(Arc::new(SyncWaker { reader, writer }))
    })
}

fn put_waker(py: Python<'_>, waker: Arc<SyncWaker>) {
    let mut idle = IDLE_WAKERS
        .lock_py_attached(py)
        .unwrap_or_else(PoisonError::into_inner);
    if idle.len() < MAX_IDLE_WAKERS {
        idle.push(waker);
    }
}
