use std::sync::Mutex;

use pyo3::{
    exceptions::PyBaseException,
    intern, pyclass, pymethods,
    sync::{MutexExt as _, PyOnceLock},
    types::PyAnyMethods as _,
    Bound, IntoPyObjectExt as _, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::{
    tokio::{future_into_py_with_locals, get_current_locals},
    TaskLocals,
};
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio_stream::wrappers::ReceiverStream;

pub(super) fn into_stream(
    py: Python<'_>,
    gen: Bound<'_, PyAny>,
) -> PyResult<(
    impl futures_core::Stream<Item = PyResult<Py<PyAny>>>,
    Py<PyAny>,
)> {
    static FORWARD_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
    let forward_fn = FORWARD_FN.get_or_try_init(py, || {
        let module = py.import("pyqwest._glue")?;
        Ok::<_, PyErr>(module.getattr("forward")?.unbind())
    })?;

    let locals = get_current_locals(py)?;
    let event_loop = locals.event_loop(py);
    let (tx, rx) = mpsc::channel::<PyResult<Py<PyAny>>>(10);
    let sender = Py::new(
        py,
        Sender {
            locals,
            tx: Mutex::new(Some(tx)),
        },
    )?;

    let coro = forward_fn.bind(py).call1((gen, sender))?;
    let task = event_loop.call_method1(intern!(py, "create_task"), (coro,))?;
    task.call_method1(intern!(py, "add_done_callback"), (TaskConsumer,))?;

    let stream = ReceiverStream::new(rx);
    Ok((stream, task.unbind()))
}

#[pyclass(module = "pyqwest._async", frozen)]
struct Sender {
    locals: TaskLocals,
    tx: Mutex<Option<mpsc::Sender<PyResult<Py<PyAny>>>>>,
}

#[pymethods]
impl Sender {
    fn send(&self, py: Python<'_>, item: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let item = if let Ok(item) = item.cast::<PyBaseException>() {
            Err(PyErr::from_value(item.clone().into_any()))
        } else {
            Ok(item.unbind())
        };

        let guard = self.tx.lock_py_attached(py).unwrap();
        // SAFETY - We never call send after close in _glue.py
        let tx = guard.as_ref().unwrap();
        match tx.try_send(item) {
            Ok(()) => true.into_py_any(py),
            Err(e) => match e {
                TrySendError::Full(item) => {
                    let tx = tx.clone();
                    future_into_py_with_locals(py, self.locals.clone(), async move {
                        let Some(permit) = tx.reserve().await.ok() else {
                            // receiving side disconnected
                            return Ok(false);
                        };
                        permit.send(item);
                        Ok(true)
                    })
                    .map(Bound::unbind)
                }
                TrySendError::Closed(_) => false.into_py_any(py),
            },
        }
    }

    fn close(&self, py: Python<'_>) {
        let mut guard = self.tx.lock_py_attached(py).unwrap();
        *guard = None;
    }
}

#[pyclass(module = "pyqwest._async", frozen)]
struct TaskConsumer;

#[pymethods]
impl TaskConsumer {
    #[allow(clippy::unused_self)]
    fn __call__(&self, py: Python<'_>, future: &Bound<'_, PyAny>) {
        // Suppress errors
        let _ = future.call_method0(intern!(py, "exception"));
    }
}
