use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

use pyo3::prelude::*;

/// How often a blocking wait wakes to re-check for shutdown, and how often the
/// `atexit` drain polls the in-flight count.
const POLL: Duration = Duration::from_millis(20);

/// How long the `atexit` drain waits for in-flight requests before giving up.
const DRAIN_TIMEOUT: Duration = Duration::from_secs(10);

/// Set once the interpreter has begun shutting down.
///
/// We learn about this from an `atexit` callback rather than by querying the
/// interpreter: the public `Py_IsFinalizing` only exists on 3.13+, and `atexit`
/// callbacks run early in `Py_FinalizeEx` — before the runtime is marked as
/// finalizing and starts forcing non-main threads to exit when they take the
/// GIL.
static SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);

/// Number of threads currently inside a blocking request that will reattach to
/// the interpreter (i.e. holding the GIL released and intending to reacquire
/// it). The `atexit` drain waits for this to reach zero before returning, so
/// that no thread is mid-reattach when the runtime is marked finalizing.
static ACTIVE: AtomicUsize = AtomicUsize::new(0);

/// Returns `true` once the interpreter has begun shutting down.
#[inline]
pub(crate) fn is_finalizing() -> bool {
    SHUTTING_DOWN.load(Ordering::SeqCst)
}

#[pyfunction]
fn _note_finalizing(py: Python<'_>) {
    SHUTTING_DOWN.store(true, Ordering::SeqCst);
    // Wait (with the GIL released so detached threads can reattach) until every
    // in-flight blocking request has either reattached and returned, or parked.
    // After this returns, CPython proceeds to mark the runtime finalizing; by
    // then no thread is mid-reattach, so none gets `pthread_exit`-ed through
    // our Rust frames.
    py.detach(|| {
        let mut waited = Duration::ZERO;
        while ACTIVE.load(Ordering::SeqCst) > 0 && waited < DRAIN_TIMEOUT {
            std::thread::sleep(POLL);
            waited += POLL;
        }
    });
}

/// Register our `atexit` hook so [`is_finalizing`] reports interpreter shutdown
/// and in-flight requests are drained before the dangerous teardown phase.
pub(crate) fn register(py: Python<'_>) -> PyResult<()> {
    let module = PyModule::new(py, "_pyqwest_shutdown")?;
    let func = wrap_pyfunction!(_note_finalizing, &module)?;
    py.import("atexit")?.call_method1("register", (func,))?;
    Ok(())
}

/// Park the current thread until the process exits.
fn park_forever() -> ! {
    loop {
        std::thread::park();
    }
}

/// Release the GIL and park the current thread forever, never reattaching.
///
/// Used once shutdown has begun: reattaching to a finalizing interpreter makes
/// the interpreter `pthread_exit` this thread, and that forced unwind crosses pyo3's
/// `catch_unwind` trampoline and aborts the process (`FATAL: exception not
/// rethrown`, SIGABRT/SIGSEGV). The interpreter is already exiting, so the
/// process will `_exit` and reap the parked thread; an abandoned in-flight
/// request owes no cleanup.
fn park_detached(py: Python<'_>) -> ! {
    py.detach(|| {
        park_forever();
    });
    // `park_forever` diverges, so `detach` never returns and never reattaches.
    unreachable!()
}

/// Wait for a blocking request's result with the GIL released, polling for
/// interpreter shutdown so the thread parks (rather than reattaching and
/// crashing) if finalization begins while the request is in flight.
///
/// Returns `Err(())` if the sender was dropped without producing a value.
pub(crate) fn wait_for<T: Send>(py: Python<'_>, rx: Receiver<T>) -> Result<T, ()> {
    // Publish that we're entering the reattach-bound region *before* checking
    // the shutdown flag. Paired with the SeqCst store/load in `_note_finalizing`
    // (set flag, then read ACTIVE), this guarantees the drain either observes
    // this thread or this thread observes the flag — never both miss.
    ACTIVE.fetch_add(1, Ordering::SeqCst);
    if is_finalizing() {
        ACTIVE.fetch_sub(1, Ordering::SeqCst);
        park_detached(py);
    }
    let out = py.detach(move || loop {
        match rx.recv_timeout(POLL) {
            Ok(value) => break Ok(value),
            Err(RecvTimeoutError::Disconnected) => break Err(()),
            Err(RecvTimeoutError::Timeout) => {
                if is_finalizing() {
                    ACTIVE.fetch_sub(1, Ordering::SeqCst);
                    park_forever();
                }
            }
        }
    });
    ACTIVE.fetch_sub(1, Ordering::SeqCst);
    out
}

/// Like [`Python::detach`], but tracked by the shutdown drain and parking
/// instead of reattaching if shutdown begins. Use for blocking work that does
/// not flow through [`wait_for`]'s channel.
pub(crate) fn detach_tracked<T, F>(py: Python<'_>, f: F) -> T
where
    F: FnOnce() -> T + Send,
    T: Send,
{
    ACTIVE.fetch_add(1, Ordering::SeqCst);
    if is_finalizing() {
        ACTIVE.fetch_sub(1, Ordering::SeqCst);
        park_detached(py);
    }
    let out = py.detach(|| {
        let result = f();
        if is_finalizing() {
            ACTIVE.fetch_sub(1, Ordering::SeqCst);
            park_forever();
        }
        result
    });
    ACTIVE.fetch_sub(1, Ordering::SeqCst);
    out
}
