use std::sync::{Mutex, PoisonError};

use pyo3::{
    exceptions::{PyBaseException, PyRuntimeError},
    pyclass, pymethods,
    sync::MutexExt as _,
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio_stream::wrappers::ReceiverStream;

use crate::{
    asyncio::runtime::{into_awaitable, spawn_pump, AsyncLibrary},
    shared::{
        constants::Constants,
        request::{RequestStreamError, RequestStreamResult},
    },
};

/// Pumps a Python async iterator into a Rust stream from a detached task on
/// `library`, returning the stream and the task's `PumpHandle`.
pub(super) fn into_stream(
    py: Python<'_>,
    gen: Bound<'_, PyAny>,
    constants: &Constants,
    library: AsyncLibrary,
) -> PyResult<(
    impl futures_core::Stream<Item = RequestStreamResult<Py<PyAny>>>,
    Py<PyAny>,
)> {
    let (tx, rx) = mpsc::channel::<RequestStreamResult<Py<PyAny>>>(10);
    let sender = Py::new(
        py,
        Sender {
            library,
            constants: constants.clone(),
            tx: Mutex::new(Some(tx)),
        },
    )?;

    let handle = spawn_pump(py, library, constants, gen, sender.into_any())?;

    let stream = ReceiverStream::new(rx);
    Ok((stream, handle))
}

#[pyclass(module = "_pyqwest.async", frozen)]
struct Sender {
    library: AsyncLibrary,
    constants: Constants,
    tx: Mutex<Option<mpsc::Sender<RequestStreamResult<Py<PyAny>>>>>,
}

#[pymethods]
impl Sender {
    fn send(&self, py: Python<'_>, item: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let item = if let Ok(item) = item.cast::<PyBaseException>() {
            Err(RequestStreamError::from_py(item))
        } else {
            Ok(item.unbind())
        };

        let (tx, item) = {
            let guard = self
                .tx
                .lock_py_attached(py)
                .unwrap_or_else(PoisonError::into_inner);
            // forward() never sends after close. Raise rather than panic if it
            // does: a panic escaping trio's system task ends the whole run.
            let tx = guard
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("request body sender already closed"))?;
            match tx.try_send(item) {
                Ok(()) => return true.into_py_any(py),
                Err(TrySendError::Closed(_)) => return false.into_py_any(py),
                Err(TrySendError::Full(item)) => (tx.clone(), item),
            }
        };
        into_awaitable(
            py,
            self.library,
            &self.constants,
            async move {
                let Some(permit) = tx.reserve().await.ok() else {
                    // receiving side disconnected
                    return Ok(false);
                };
                permit.send(item);
                Ok(true)
            },
            None,
        )
        .map(Bound::unbind)
    }

    fn close(&self, py: Python<'_>) {
        *self
            .tx
            .lock_py_attached(py)
            .unwrap_or_else(PoisonError::into_inner) = None;
    }
}
