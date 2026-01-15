use pyo3::{
    exceptions::{PyStopAsyncIteration, PyStopIteration},
    prelude::*,
    IntoPyObjectExt as _,
};

/// An awaitable that returns `None` when awaited.
#[pyclass(module = "_pyqwest.async", frozen)]
pub(super) struct EmptyAwaitable;

#[pymethods]
impl EmptyAwaitable {
    fn __await__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[allow(clippy::unused_self)]
    fn __next__(&self) -> Option<()> {
        None
    }
}

/// An awaitable that returns the given value when awaited.
#[pyclass(module = "_pyqwest.async")]
pub(super) struct ValueAwaitable {
    pub(super) value: Option<Py<PyAny>>,
}

#[pymethods]
impl ValueAwaitable {
    fn __await__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> PyResult<Py<PyAny>> {
        if let Some(value) = self.value.take() {
            Err(PyStopIteration::new_err(value))
        } else {
            // Shouldn't happen in practice.
            Err(PyStopIteration::new_err(()))
        }
    }
}

/// An awaitable that raises the given error when awaited.
#[pyclass(module = "_pyqwest.async")]
pub(super) struct ErrorAwaitable {
    pub(super) error: Option<PyErr>,
}

#[pymethods]
impl ErrorAwaitable {
    fn __await__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> PyResult<()> {
        if let Some(error) = self.error.take() {
            Err(error)
        } else {
            // Shouldn't happen in practice.
            Ok(())
        }
    }
}

/// An `AsyncIterator` that yields no items.
#[pyclass(module = "_pyqwest.async", frozen)]
pub(super) struct EmptyAsyncIterator;

#[pymethods]
impl EmptyAsyncIterator {
    fn __aiter__(slf: Py<EmptyAsyncIterator>) -> Py<EmptyAsyncIterator> {
        slf
    }

    #[allow(clippy::unused_self)]
    fn __anext__(&self) -> ErrorAwaitable {
        ErrorAwaitable {
            error: Some(PyStopAsyncIteration::new_err(())),
        }
    }
}

/// An `AsyncIterator` that yields a single value.
#[pyclass(module = "_pyqwest.async")]
pub(super) struct ValueAsyncIterator {
    pub(super) value: Option<Py<PyAny>>,
}

#[pymethods]
impl ValueAsyncIterator {
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __anext__(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if let Some(value) = self.value.take() {
            ValueAwaitable { value: Some(value) }.into_py_any(py)
        } else {
            ErrorAwaitable {
                error: Some(PyStopAsyncIteration::new_err(())),
            }
            .into_py_any(py)
        }
    }
}
