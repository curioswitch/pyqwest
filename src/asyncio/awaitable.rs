use pyo3::{exceptions::PyStopIteration, prelude::*};

/// An awaitable that returns `None` when awaited.
#[pyclass(module = "pyqwest._async", frozen)]
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
#[pyclass(module = "pyqwest._async")]
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
