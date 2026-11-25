//! Wakes an asyncio event loop from tokio workers without the GIL.
//!
//! A worker that finishes a request queues its completion on the loop's waker
//! and writes a byte to a socket the loop watches with `add_reader`. The loop
//! then runs every queued completion on its own thread, so tokio threads never
//! attach to the interpreter and completions that finish together wake the
//! loop once.
//!
//! pyqwest runs on whatever loop the caller has, so each loop gets its own
//! waker the first time a request runs on it. Loops without `add_reader`, such
//! as the default asyncio loop on Windows, have no waker, and their completions
//! go through `call_soon_threadsafe` instead.

use std::sync::{Arc, Mutex, PoisonError, Weak};

use pyo3::{
    exceptions::{PyNotImplementedError, PyTypeError},
    pyclass, pymethods,
    sync::{MutexExt as _, PyOnceLock},
    types::{PyAnyMethods as _, PyBool, PyModule, PyWeakrefMethods as _, PyWeakrefReference},
    Bound, Py, PyAny, PyErr, PyResult, Python,
};

use crate::shared::wakeup::{
    drain_wakeups, nonblocking_socket_pair, socket_fd, write_wakeup, WakerSocket,
};

/// A completion run on the event loop thread while attached to the
/// interpreter.
pub(super) type LoopTask = Box<dyn FnOnce(Python<'_>) + Send>;

/// A handle to the completion queue of an event loop. It does not keep the
/// queue alive: the loop's waker owns it, so a completion that holds the handle
/// cannot keep its loop alive through the queue it sits in.
pub(super) struct LoopQueue(Weak<Queue>);

struct Queue {
    writer: WakerSocket,
    state: Mutex<QueueState>,
}

#[derive(Default)]
struct QueueState {
    tasks: Vec<LoopTask>,
    /// Is set when the loop has been woken and has not drained its tasks yet,
    /// so tasks queued together wake it only once.
    wake_pending: bool,
}

impl LoopQueue {
    /// Queues `task` to run on the event loop thread. Must be called from the
    /// tokio runtime.
    pub(super) fn dispatch(&self, task: LoopTask) {
        let Some(queue) = self.0.upgrade() else {
            // The loop released its waker, usually by closing, and will not run
            // the task. Its Python objects are released while attached, as with
            // a loop that raises from `call_soon_threadsafe`.
            tokio::task::spawn_blocking(move || Python::try_attach(|_| drop(task)));
            return;
        };
        let wake = {
            let mut state = queue.state.lock().unwrap_or_else(PoisonError::into_inner);
            state.tasks.push(task);
            !std::mem::replace(&mut state.wake_pending, true)
        };
        if wake {
            let _ = write_wakeup(&queue.writer);
        }
    }
}

/// The reader callback registered with the event loop, which runs the queued
/// completions. Only the loop's reader handle keeps it alive, so closing the
/// loop releases it along with its queue and sockets.
#[pyclass(module = "_pyqwest.async", frozen, weakref)]
struct LoopWaker {
    queue: Arc<Queue>,
    reader: WakerSocket,
}

impl LoopWaker {
    fn handle(&self) -> LoopQueue {
        LoopQueue(Arc::downgrade(&self.queue))
    }
}

#[pymethods]
impl LoopWaker {
    fn __call__(&self, py: Python<'_>) {
        // Clear the wakeup before taking the tasks, so a task queued in between
        // wakes the loop again rather than being missed.
        drain_wakeups(&self.reader);
        let tasks = {
            let mut state = self
                .queue
                .state
                .lock_py_attached(py)
                .unwrap_or_else(PoisonError::into_inner);
            state.wake_pending = false;
            std::mem::take(&mut state.tasks)
        };
        for task in tasks {
            task(py);
        }
    }
}

/// `weakref.WeakKeyDictionary` mapping each event loop to a weak reference to
/// its `LoopWaker`, or to `False` for a loop that cannot watch a socket. The
/// loop owns its waker through its reader handle, so a strong reference here
/// would keep the waker alive as long as the loop, and a closed loop alive
/// through the completions still queued on its waker.
fn wakers(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    static WAKERS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
    WAKERS
        .get_or_try_init(py, || {
            Ok::<_, PyErr>(
                PyModule::import(py, "weakref")?
                    .getattr("WeakKeyDictionary")?
                    .call0()?
                    .unbind(),
            )
        })
        .map(|wakers| wakers.bind(py))
}

/// Returns the completion queue of `event_loop`, which must be running on this
/// thread, registering a waker with it on first use. Returns `None` if the loop
/// cannot watch a socket.
pub(super) fn loop_queue(
    py: Python<'_>,
    event_loop: &Bound<'_, PyAny>,
) -> PyResult<Option<LoopQueue>> {
    let wakers = wakers(py)?;
    let existing = match wakers.call_method1("get", (event_loop,)) {
        Ok(existing) => existing,
        // A loop that cannot be weakly referenced cannot be tracked.
        Err(e) if e.is_instance_of::<PyTypeError>(py) => return Ok(None),
        Err(e) => return Err(e),
    };
    if existing.is(PyBool::new(py, false)) {
        return Ok(None);
    }
    if let Ok(waker_ref) = existing.cast::<PyWeakrefReference>() {
        if let Some(waker) = waker_ref.upgrade_as::<LoopWaker>()? {
            return Ok(Some(waker.get().handle()));
        }
    }

    // Without the sockets, completions go through `call_soon_threadsafe`, and
    // the next request tries again.
    let Ok((reader, writer)) = nonblocking_socket_pair() else {
        return Ok(None);
    };
    let fd = socket_fd(&reader);
    let waker = Bound::new(
        py,
        LoopWaker {
            queue: Arc::new(Queue {
                writer,
                state: Mutex::default(),
            }),
            reader,
        },
    )?;
    // The loop's reader handle copies the current context, which would keep the
    // first request's context variables alive as long as the loop, so it is
    // registered from an empty one.
    let context = PyModule::import(py, "contextvars")?
        .getattr("Context")?
        .call0()?;
    let add_reader = event_loop.getattr("add_reader")?;
    match context.call_method1("run", (add_reader, fd, &waker)) {
        Ok(_) => {
            wakers.set_item(event_loop, PyWeakrefReference::new(&waker)?)?;
            Ok(Some(waker.get().handle()))
        }
        Err(e) if e.is_instance_of::<PyNotImplementedError>(py) => {
            wakers.set_item(event_loop, false)?;
            Ok(None)
        }
        Err(e) => Err(e),
    }
}
