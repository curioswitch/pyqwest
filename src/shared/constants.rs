use std::{ops::Deref, sync::Arc};

use pyo3::{
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyBytes, PyString},
    Py, PyAny, PyResult, Python,
};

/// Constants used when creating Python objects. These are mostly strings,
/// which `PyO3` provides the intern! macro for, but it still has a very small amount
/// of overhead per access, but more importantly forces lazy initialization during
/// request processing. It's not too hard for us to memoize these at client init so
/// we go ahead and do it. Then, usage is just simple ref-counting.
pub(crate) struct ConstantsInner {
    /// An empty bytes object.
    pub(crate) empty_bytes: Py<PyBytes>,

    /// The string "__aiter__".
    pub(crate) __aiter__: Py<PyString>,
    /// The string "aclose".
    pub(crate) aclose: Py<PyString>,
    /// The string "`add_done_callback`".
    pub(crate) add_done_callback: Py<PyString>,
    /// The string "cancel".
    pub(crate) cancel: Py<PyString>,
    /// The string "close".
    pub(crate) close: Py<PyString>,
    /// The string "`create_task`".
    pub(crate) create_task: Py<PyString>,
    /// The string "exception".
    pub(crate) exception: Py<PyString>,
    /// The string "execute".
    pub(crate) execute: Py<PyString>,
    /// The string "`execute_sync`".
    pub(crate) execute_sync: Py<PyString>,

    /// The _glue.py function `execute_and_read_full`.
    pub(crate) execute_and_read_full: Py<PyAny>,
    /// The _glue.py function `forward`.
    pub(crate) forward: Py<PyAny>,
    /// The _glue.py function `read_content_sync`.
    pub(crate) read_content_sync: Py<PyAny>,

    /// The stdlib function `json.loads`.
    pub(crate) json_loads: Py<PyAny>,
}

static INSTANCE: PyOnceLock<Constants> = PyOnceLock::new();

#[derive(Clone)]
pub(crate) struct Constants {
    inner: Arc<ConstantsInner>,
}

impl Constants {
    pub(crate) fn get(py: Python<'_>) -> PyResult<Self> {
        Ok(INSTANCE.get_or_try_init(py, || Self::new(py))?.clone())
    }

    fn new(py: Python<'_>) -> PyResult<Self> {
        let glue = py.import("pyqwest._glue")?;
        Ok(Self {
            inner: Arc::new(ConstantsInner {
                empty_bytes: PyBytes::new(py, b"").unbind(),
                __aiter__: PyString::new(py, "__aiter__").unbind(),
                aclose: PyString::new(py, "aclose").unbind(),
                add_done_callback: PyString::new(py, "add_done_callback").unbind(),
                cancel: PyString::new(py, "cancel").unbind(),
                close: PyString::new(py, "close").unbind(),
                create_task: PyString::new(py, "create_task").unbind(),
                exception: PyString::new(py, "exception").unbind(),
                execute: PyString::new(py, "execute").unbind(),
                execute_sync: PyString::new(py, "execute_sync").unbind(),

                execute_and_read_full: glue.getattr("execute_and_read_full")?.unbind(),
                forward: glue.getattr("forward")?.unbind(),
                read_content_sync: glue.getattr("read_content_sync")?.unbind(),

                json_loads: py.import("json")?.getattr("loads")?.unbind(),
            }),
        })
    }
}

impl Deref for Constants {
    type Target = ConstantsInner;

    fn deref(&self) -> &Self::Target {
        &self.inner
    }
}
