use std::any::Any;

use pyo3::{PyErr, Python};

/// Runs `f` with any pending exception unset, restoring it after the function
/// completes. This is important for `Drop` implementations that call Python
/// functions, which must be run without a pending exception, or they will
/// themselves raise an exception, which pyo3 fetches to convert into a `PyErr`,
/// leaving no exception during an unwind and crashing the interpreter.
///
/// `f` must not panic.
// TODO: Replace Drop implementations that run Python with __del__
// https://github.com/PyO3/pyo3/pull/2484
pub(crate) fn without_pending_exception(py: Python<'_>, f: impl FnOnce()) {
    let pending = PyErr::take(py);
    f();
    if let Some(pending) = pending {
        pending.restore(py);
    }
}

/// The message a panic was raised with.
pub(crate) fn panic_message(payload: &(dyn Any + Send)) -> &str {
    payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("panic from Rust code")
}
