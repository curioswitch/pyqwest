use std::{
    any::Any,
    panic::{catch_unwind, AssertUnwindSafe},
};

use pyo3::{ffi, panic::PanicException, Python};

/// Runs `f` with the thread's pending exception set aside, for a `Drop` that
/// calls into Python. Python's C API requires a deallocator to leave the
/// exception state unchanged
/// (<https://docs.python.org/3/c-api/typeobj.html#c.PyTypeObject.tp_dealloc>).
/// pyo3 leaves that to each `Drop` and provides no helper for it
/// (<https://github.com/PyO3/pyo3/issues/2860>). An object can be deallocated
/// during exception propagation; without setting the propagating exception
/// aside for a `Drop`, the Python call sees it as its own, and the interpreter
/// resumes unwinding with a different exception or none. This can cause a
/// segfault.
///
/// A panic in `f` is reported as unraisable before the exception is restored.
/// If the panic escaped the `Drop`, pyo3 would raise it over the pending
/// exception.
pub(crate) fn with_exception_set_aside(py: Python<'_>, f: impl FnOnce()) {
    let _pending = PendingException::take(py);
    if let Err(payload) = catch_unwind(AssertUnwindSafe(f)) {
        PanicException::new_err(panic_message(&*payload).to_owned()).write_unraisable(py, None);
    }
}

// Raw FFI is used rather than `PyErr::take`/`restore`: `take` resumes the panic
// when the pending exception is a `PanicException`, and before Python 3.12 it
// normalizes the exception, which can run Python code.

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

fn panic_message(payload: &(dyn Any + Send)) -> &str {
    payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("panic from Rust code")
}
