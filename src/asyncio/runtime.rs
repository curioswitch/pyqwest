//! Runtime dispatch for the awaitables the transport hands back to Python.
//!
//! A Rust future is spawned on the tokio runtime as soon as it is created, on
//! either async library, and races an abort signal. Each library supplies the
//! awaitable Python gets back and the way tokio reports the outcome. asyncio's
//! are built here: an `asyncio.Future`, completed through the loop's
//! `call_soon_threadsafe`. trio's come from `pyqwest._trio`: a coroutine
//! waiting on a `trio.Event`, which only Python can await.

use std::{
    any::Any,
    future::Future,
    panic::{catch_unwind, AssertUnwindSafe},
    sync::{Mutex, PoisonError},
};

use futures_util::FutureExt as _;
use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    sync::{MutexExt as _, PyOnceLock},
    types::{PyAnyMethods as _, PyModule},
    Bound, IntoPyObject, IntoPyObjectExt as _, Py, PyAny, PyErr, PyResult, Python,
};
use tokio::sync::oneshot;

use crate::shared::{constants::Constants, exception::panic_message, runtime::get_runtime};

/// The async library a request runs on. `execute()` detects it once, and every
/// awaitable the request creates reuses the answer, including each read of the
/// response body, so a response is read on the library that executed it.
#[derive(Clone, Copy)]
pub(super) enum AsyncLibrary {
    Asyncio,
    Trio,
}

impl AsyncLibrary {
    /// The running library, as sniffio reports it. With no library running,
    /// or without sniffio, this is asyncio, whose path then reports the
    /// missing event loop. Any other library is an error here instead: the
    /// asyncio path would report a missing loop, which hides the cause.
    pub(super) fn current(py: Python<'_>, constants: &Constants) -> PyResult<Self> {
        let Some(sniffio) = &constants.sniffio else {
            return Ok(Self::Asyncio);
        };
        let name = match sniffio.current_async_library.call0(py) {
            Ok(name) => name.into_bound(py),
            Err(e) if e.is_instance(py, sniffio.async_library_not_found.bind(py)) => {
                return Ok(Self::Asyncio);
            }
            Err(e) => return Err(e),
        };
        if name.eq(&constants.trio)? {
            Ok(Self::Trio)
        } else if name.eq(&constants.asyncio)? {
            Ok(Self::Asyncio)
        } else {
            Err(PyRuntimeError::new_err(format!(
                "pyqwest supports asyncio and trio, not {}",
                name.repr()?
            )))
        }
    }
}

/// `pyqwest._trio`, imported on first use so that trio stays an optional
/// dependency.
struct TrioGlue {
    /// `start_request(abort, on_done)`, returning the completion callback
    /// tokio reports through and the awaitable for the outcome.
    start_request: Py<PyAny>,
    /// `spawn_pump(fn, *args)`, starting `fn(*args)` as a system task and
    /// returning its `PumpHandle`.
    spawn_pump: Py<PyAny>,
}

fn trio_glue(py: Python<'_>) -> PyResult<&'static TrioGlue> {
    static GLUE: PyOnceLock<TrioGlue> = PyOnceLock::new();
    GLUE.get_or_try_init(py, || {
        let module = PyModule::import(py, "pyqwest._trio")?;
        Ok(TrioGlue {
            start_request: module.getattr("start_request")?.unbind(),
            spawn_pump: module.getattr("spawn_pump")?.unbind(),
        })
    })
}

/// Starts `forward(gen, sender)`, the request body pump, as a detached task on
/// `library`. Returns its `PumpHandle`: `cancel()` on the loop's thread,
/// `cancel_soon()` from any.
pub(super) fn spawn_pump(
    py: Python<'_>,
    library: AsyncLibrary,
    constants: &Constants,
    gen: Bound<'_, PyAny>,
    sender: Py<PyAny>,
) -> PyResult<Py<PyAny>> {
    match library {
        AsyncLibrary::Asyncio => {
            let event_loop = constants.get_running_loop.call0(py)?.into_bound(py);
            let coro = constants.forward.bind(py).call1((gen, sender))?;
            let task = event_loop.call_method1(&constants.create_task, (coro,))?;
            task.call_method1(
                &constants.add_done_callback,
                (ConsumeTask {
                    constants: constants.clone(),
                },),
            )?;
            TaskHandle {
                task: task.unbind(),
                constants: constants.clone(),
            }
            .into_py_any(py)
        }
        AsyncLibrary::Trio => trio_glue(py)?
            .spawn_pump
            .call1(py, (&constants.forward, gen, sender)),
    }
}

/// Retrieves a finished pump task's outcome so the loop never logs it: the
/// pump reports errors through its sender, and cancellation is how it stops.
#[pyclass(module = "_pyqwest.async", frozen)]
struct ConsumeTask {
    constants: Constants,
}

#[pymethods]
impl ConsumeTask {
    fn __call__(&self, task: &Bound<'_, PyAny>) {
        let _ = task.call_method0(&self.constants.exception);
    }
}

/// The `PumpHandle` of an asyncio task.
#[pyclass(module = "_pyqwest.async", frozen)]
struct TaskHandle {
    task: Py<PyAny>,
    constants: Constants,
}

#[pymethods]
impl TaskHandle {
    fn cancel(&self, py: Python<'_>) -> PyResult<()> {
        self.task.call_method0(py, &self.constants.cancel)?;
        Ok(())
    }

    fn cancel_soon(&self, py: Python<'_>) -> PyResult<()> {
        let event_loop = self.task.call_method0(py, &self.constants.get_loop)?;
        let cancel = self.task.getattr(py, &self.constants.cancel)?;
        match event_loop.call_method1(py, &self.constants.call_soon_threadsafe, (cancel,)) {
            // A closed loop raises RuntimeError; its task is already gone.
            Err(e) if e.is_instance_of::<PyRuntimeError>(py) => Ok(()),
            res => res.map(drop),
        }
    }
}

/// Spawns `fut` and wraps its outcome in an awaitable for `library`.
///
/// `on_done`, if given, is called once the future settles, whether or not the
/// awaitable is awaited, with an object whose `result()` returns the value or
/// raises the error: the `asyncio.Future` itself, or its trio stand-in. An
/// `Exception` from `on_done` is logged under either library.
pub(super) fn into_awaitable<'py, F, T>(
    py: Python<'py>,
    library: AsyncLibrary,
    constants: &Constants,
    fut: F,
    on_done: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    let (abort_tx, mut abort_rx) = oneshot::channel();
    let abort = Abort {
        abort: Mutex::new(Some(abort_tx)),
    };
    let (completion, awaitable) = match library {
        AsyncLibrary::Asyncio => asyncio_awaitable(py, constants, abort, on_done)?,
        AsyncLibrary::Trio => {
            let (callback, awaitable): (Py<PyAny>, Bound<'py, PyAny>) = trio_glue(py)?
                .start_request
                .bind(py)
                .call1((abort, on_done))?
                .extract()?;
            (Completion::Trio(callback), awaitable)
        }
    };

    let constants = constants.clone();
    get_runtime().spawn(async move {
        let outcome = tokio::select! {
            biased;
            // Only an explicit abort cancels. A dropped handle leaves the
            // request running, as dropping an `asyncio.Future` does.
            Ok(()) = &mut abort_rx => None,
            // A panic in the future is caught so it still reaches the awaiter.
            res = AssertUnwindSafe(fut).catch_unwind() => Some(res),
        };
        // Converting the result and calling back into Python need the GIL,
        // which a tokio worker must not wait for, so both happen on the
        // blocking pool.
        tokio::task::spawn_blocking(move || {
            Python::try_attach(move |py| report(py, &constants, &completion, outcome));
        });
    });
    Ok(awaitable)
}

/// asyncio Future and completion callback.
fn asyncio_awaitable<'py>(
    py: Python<'py>,
    constants: &Constants,
    abort: Abort,
    on_done: Option<Bound<'py, PyAny>>,
) -> PyResult<(Completion, Bound<'py, PyAny>)> {
    let event_loop = constants.get_running_loop.call0(py)?.into_bound(py);
    let future = event_loop.call_method0(&constants.create_future)?;
    future.call_method1(
        &constants.add_done_callback,
        (AbortOnCancel {
            abort,
            constants: constants.clone(),
        },),
    )?;
    if let Some(on_done) = on_done {
        future.call_method1(&constants.add_done_callback, (on_done,))?;
    }
    let deliver = Py::new(
        py,
        Deliver {
            future: future.clone().unbind(),
            constants: constants.clone(),
        },
    )?;
    let completion = Completion::Asyncio {
        event_loop: event_loop.unbind(),
        deliver,
    };
    Ok((completion, future))
}

/// How tokio reports a request's outcome, `(value, error, cancelled)`, to
/// Python.
enum Completion {
    /// `deliver(value, error, cancelled)`, scheduled on the event loop.
    Asyncio {
        event_loop: Py<PyAny>,
        deliver: Py<Deliver>,
    },
    /// The callback `pyqwest._trio.start_request` returned.
    Trio(Py<PyAny>),
}

/// Reports `outcome` exactly once: `None` for an abort, otherwise the future's
/// result or its panic.
fn report<T>(
    py: Python<'_>,
    constants: &Constants,
    completion: &Completion,
    outcome: Option<Result<PyResult<T>, Box<dyn Any + Send>>>,
) where
    T: for<'a> IntoPyObject<'a>,
{
    let result = match outcome {
        None => Ok(None),
        Some(Err(payload)) => Err(panic_error(&*payload)),
        // A panicking conversion must still wake the awaiter.
        Some(Ok(res)) => catch_unwind(AssertUnwindSafe(|| {
            res.and_then(|value| value.into_py_any(py)).map(Some)
        }))
        .unwrap_or_else(|payload| Err(panic_error(&*payload))),
    };
    let (value, error, cancelled) = match result {
        Ok(Some(value)) => (value, py.None(), false),
        Ok(None) => (py.None(), py.None(), true),
        Err(err) => (py.None(), err.into_value(py).into_any(), false),
    };
    let reported = match completion {
        Completion::Asyncio {
            event_loop,
            deliver,
        } => event_loop
            .call_method1(
                py,
                &constants.call_soon_threadsafe,
                (deliver, value, error, cancelled),
            )
            .map(drop)
            // A closed loop raises RuntimeError: nobody waits.
            .or_else(|e| {
                e.is_instance_of::<PyRuntimeError>(py)
                    .then_some(())
                    .ok_or(e)
            }),
        Completion::Trio(callback) => callback.call1(py, (value, error, cancelled)).map(drop),
    };
    if let Err(e) = reported {
        e.write_unraisable(py, None);
    }
}

/// The error for a panic in a request task.
fn panic_error(payload: &(dyn Any + Send)) -> PyErr {
    PyRuntimeError::new_err(format!("rust future panicked: {}", panic_message(payload)))
}

/// asyncio's done callback: a Future that asyncio cancelled aborts the request.
#[pyclass(module = "_pyqwest.async", frozen)]
struct AbortOnCancel {
    abort: Abort,
    constants: Constants,
}

#[pymethods]
impl AbortOnCancel {
    fn __call__(&self, py: Python<'_>, future: &Bound<'_, PyAny>) -> PyResult<()> {
        if future
            .call_method0(&self.constants.cancelled)?
            .is_truthy()?
        {
            self.abort.abort(py);
        }
        Ok(())
    }
}

/// Completes the Future on the loop thread with what tokio reported.
#[pyclass(module = "_pyqwest.async", frozen)]
struct Deliver {
    future: Py<PyAny>,
    constants: Constants,
}

#[pymethods]
impl Deliver {
    fn __call__(
        &self,
        py: Python<'_>,
        value: &Bound<'_, PyAny>,
        error: &Bound<'_, PyAny>,
        cancelled: bool,
    ) -> PyResult<()> {
        let future = self.future.bind(py);
        // Cancelled here first: this is its abort, or a result that raced it.
        if future.call_method0(&self.constants.done)?.is_truthy()? {
            return Ok(());
        }
        if cancelled {
            future.call_method0(&self.constants.cancel)?;
        } else if !error.is_none() {
            future.call_method1(&self.constants.set_exception, (error,))?;
        } else {
            future.call_method1(&self.constants.set_result, (value,))?;
        }
        Ok(())
    }
}

/// Cancels the spawned future. The completion then reports `cancelled=True`,
/// unless the future finished first. Dropping the handle without calling
/// `abort` leaves the future running.
#[pyclass(module = "_pyqwest.async", frozen)]
struct Abort {
    abort: Mutex<Option<oneshot::Sender<()>>>,
}

#[pymethods]
impl Abort {
    fn abort(&self, py: Python<'_>) {
        let tx = self
            .abort
            .lock_py_attached(py)
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        if let Some(tx) = tx {
            let _ = tx.send(());
        }
    }
}
