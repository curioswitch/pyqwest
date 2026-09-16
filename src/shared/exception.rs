use std::any::Any;

#[cfg(Py_3_12)]
use pyo3::ffi;
use pyo3::Python;

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
    let _pending = PendingException::take(py);
    f();
}

// Raw FFI is used rather than `PyErr::take`/`restore`, `take` resumes the panic
// when the pending exception is a `PanicException` instead of taking it.

/// A thread's pending exception, moved out of the thread state and moved back
/// when dropped. The `Python<'py>` token ties it to the attach scope and, being
/// `!Send`, to this thread.
struct PendingException<'py> {
    /// An owned reference to the pending exception, or null if none was
    /// pending.
    #[cfg(Py_3_12)]
    exc: *mut ffi::PyObject,
    /// Owned references to the pending exception's type, value and traceback,
    /// as `PyErr_Fetch` returns them: all null if none was pending.
    #[cfg(not(Py_3_12))]
    exc: [*mut ffi::PyObject; 3],
    _py: Python<'py>,
}

impl<'py> PendingException<'py> {
    #[cfg(Py_3_12)]
    fn take(py: Python<'py>) -> Self {
        // SAFETY: an attached thread is required. This is guaranteed by pyo3
        // only providing a `py` token on an attached thread.
        let exc = unsafe { ffi::PyErr_GetRaisedException() };
        Self { exc, _py: py }
    }

    #[cfg(not(Py_3_12))]
    fn take(py: Python<'py>) -> Self {
        let [mut ptype, mut pvalue, mut ptraceback] = [std::ptr::null_mut(); 3];
        // SAFETY: an attached thread is required. This is guaranteed by pyo3
        // only providing a `py` token on an attached thread. The out-pointers
        // point to live locals, so they are valid for writes.
        unsafe { ffi::PyErr_Fetch(&raw mut ptype, &raw mut pvalue, &raw mut ptraceback) };
        Self {
            exc: [ptype, pvalue, ptraceback],
            _py: py,
        }
    }
}

impl Drop for PendingException<'_> {
    #[cfg(Py_3_12)]
    fn drop(&mut self) {
        // SAFETY: the guard holds a `Python<'py>` token, so it can only be
        // dropped while this thread is attached. The call takes a valid
        // exception or null, and steals the reference: `exc` is an owned
        // reference to the pending exception, or null.
        unsafe { ffi::PyErr_SetRaisedException(self.exc) }
    }

    #[cfg(not(Py_3_12))]
    fn drop(&mut self) {
        let [ptype, pvalue, ptraceback] = self.exc;
        // SAFETY: the guard holds a `Python<'py>` token, so it can only be
        // dropped while this thread is attached. The call steals the references
        // to `ptype`, `pvalue`, and `ptraceback`. If `ptype` is null, the
        // others must be too. This is ensured by `PyErr_Fetch`, and the values
        // are unmodified from that call during `take`.
        unsafe { ffi::PyErr_Restore(ptype, pvalue, ptraceback) }
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
