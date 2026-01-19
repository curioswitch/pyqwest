use std::{ops::Deref, sync::Arc};

use pyo3::{
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyBytes, PyString},
    Py, PyAny, PyResult, PyTypeInfo, Python,
};

use crate::common::httpversion::HTTPVersion;

/// Constants used when creating Python objects. These are mostly strings,
/// which `PyO3` provides the intern! macro for, but it still has a very small amount
/// of overhead per access, but more importantly forces lazy initialization during
/// request processing. It's not too hard for us to memoize these at client init so
/// we go ahead and do it. Then, usage is just simple ref-counting.
pub(crate) struct ConstantsInner {
    /// An empty bytes object.
    pub empty_bytes: Py<PyBytes>,

    /// The string "__aiter__".
    pub __aiter__: Py<PyString>,
    /// The string "aclose".
    pub aclose: Py<PyString>,
    /// The string "`add_done_callback`".
    pub add_done_callback: Py<PyString>,
    /// The string "cancel".
    pub cancel: Py<PyString>,
    /// The string "close".
    pub close: Py<PyString>,
    /// The string "`create_task`".
    pub create_task: Py<PyString>,
    /// The string "exception".
    pub exception: Py<PyString>,
    /// The string "execute".
    pub execute: Py<PyString>,
    /// The string "`execute_sync`".
    pub execute_sync: Py<PyString>,

    // HTTP Versions
    /// HTTPVersion.HTTP1
    pub http_1: Py<HTTPVersion>,
    /// HTTPVersion.HTTP2
    pub http_2: Py<HTTPVersion>,
    /// HTTPVersion.HTTP3
    pub http_3: Py<HTTPVersion>,

    // HTTP method strings
    /// The string "DELETE".
    pub delete: Py<PyString>,
    /// The string "GET".
    pub get: Py<PyString>,
    /// The string "HEAD".
    pub head: Py<PyString>,
    /// The string "OPTIONS".
    pub options: Py<PyString>,
    /// The string "PATCH".
    pub patch: Py<PyString>,
    /// The string "POST".
    pub post: Py<PyString>,
    /// The string "PUT".
    pub put: Py<PyString>,
    /// The string "TRACE".
    pub trace: Py<PyString>,

    /// The _glue.py function `execute_and_read_full`.
    pub execute_and_read_full: Py<PyAny>,
    /// The _glue.py function `forward`.
    pub forward: Py<PyAny>,
    /// The _glue.py function `read_content_sync`.
    pub read_content_sync: Py<PyAny>,

    /// The stdlib function `json.loads`.
    pub json_loads: Py<PyAny>,
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

                http_1: get_class_attr::<HTTPVersion>(py, "HTTP1")?,
                http_2: get_class_attr::<HTTPVersion>(py, "HTTP2")?,
                http_3: get_class_attr::<HTTPVersion>(py, "HTTP3")?,

                delete: PyString::new(py, "DELETE").unbind(),
                get: PyString::new(py, "GET").unbind(),
                head: PyString::new(py, "HEAD").unbind(),
                options: PyString::new(py, "OPTIONS").unbind(),
                patch: PyString::new(py, "PATCH").unbind(),
                post: PyString::new(py, "POST").unbind(),
                put: PyString::new(py, "PUT").unbind(),
                trace: PyString::new(py, "TRACE").unbind(),

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

fn get_class_attr<T: PyTypeInfo>(py: Python<'_>, name: &str) -> PyResult<Py<T>> {
    let cls = py.get_type::<T>();
    let attr = cls.getattr(name)?;
    Ok(attr.cast::<T>()?.clone().unbind())
}
