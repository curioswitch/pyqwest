//! Waits for work on the tokio runtime from a sync call.
//!
//! A sync call usually blocks its thread, detached from the interpreter, until
//! tokio finishes. Under gevent or eventlet one thread runs many greenlets, and
//! blocking it stalls all of them. Both libraries patch `select.select` to
//! switch to their hub until a descriptor is ready, so while it is patched, the
//! call instead waits through it on a socket that the tokio task writes to when
//! it finishes, and other greenlets run in the meantime. As with the asyncio
//! waker, tokio threads never attach to the interpreter to wake the caller.

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

/// Runs `future` on the tokio runtime and returns its output, waiting
/// cooperatively if `select.select` is patched and blocking the thread
/// otherwise.
///
/// A cooperative wait raises whatever the patched `select` raises, such as
/// `gevent.Timeout` or `GreenletExit` when another greenlet interrupts the
/// waiting one, and cancels `future`.
pub(crate) fn wait<T, F>(py: Python<'_>, future: F) -> PyResult<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    if let Some(select) = patched_select(py)? {
        // Without a socket the call can still block, so it does.
        if let Some(waker) = take_waker(py) {
            return wait_with_select(py, &select, waker, future);
        }
    }
    let (tx, rx) = oneshot::channel();
    get_runtime().spawn(async move {
        let _ = tx.send(future.await);
    });
    py.detach(|| rx.blocking_recv()).map_err(|_| task_failed())
}

/// Returns `select.select` if a library such as gevent has replaced it, which
/// it does with a function written in Python or Cython rather than C.
fn patched_select(py: Python<'_>) -> PyResult<Option<Bound<'_, PyAny>>> {
    static SELECT_MODULE: PyOnceLock<Py<PyModule>> = PyOnceLock::new();
    let module =
        SELECT_MODULE.get_or_try_init(py, || PyModule::import(py, "select").map(Bound::unbind))?;
    // Libraries usually patch on startup, but may do so after the first
    // request, so the function is looked up each time.
    let select = module.bind(py).getattr(intern!(py, "select"))?;
    Ok((!select.is_instance_of::<PyCFunction>()).then_some(select))
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
        // The output is sent before the wakeup is written, so a waiter woken by
        // it always finds the output.
        let _ = tx.send(future.await);
        let _ = write_wakeup(&task_waker.writer);
    });
    let rlist = PyTuple::new(py, [socket_fd(&waker.reader)])?;
    let empty = PyTuple::empty(py);
    loop {
        // Draining before checking for the output means an output sent after
        // the check leaves its wakeup pending for `select`. A wakeup left by a
        // previous user of the socket only costs another check.
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
            // Nobody will read the output, so the work is stopped. The socket
            // is not reused, as the task may still write to it.
            task.abort();
            return Err(e);
        }
    }
}

fn task_failed() -> PyErr {
    PyRuntimeError::new_err("pyqwest runtime task ended without a result")
}

/// A pair of sockets a waiting call watches one end of while the tokio task it
/// waits for holds the other.
struct SyncWaker {
    reader: WakerSocket,
    writer: WakerSocket,
}

/// Sockets of finished waits, kept to save creating a pair for every request
/// and every chunk of a streamed response.
static IDLE_WAKERS: Mutex<Vec<Arc<SyncWaker>>> = Mutex::new(Vec::new());

/// The most idle socket pairs to keep. Waits beyond this many at once close
/// their sockets when they finish.
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
