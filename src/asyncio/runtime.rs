//! Runtime dispatch for the awaitables the transport hands back to Python.
//!
//! Under asyncio a Rust future becomes an `asyncio.Future` through
//! pyo3-async-runtimes. Under trio it is wrapped in a [`Kickoff`], which
//! `pyqwest._trio.await_kickoff` awaits.
//!
//! The two runtimes differ in when work starts: asyncio futures run from the
//! moment they are created, while a trio kickoff runs only once its
//! coroutine is awaited.

use std::{
    any::Any,
    future::Future,
    panic::{catch_unwind, AssertUnwindSafe},
    sync::{Mutex, PoisonError},
};

use pyo3::{
    exceptions::PyRuntimeError,
    pyclass, pymethods,
    sync::{MutexExt as _, PyOnceLock},
    types::{PyAnyMethods as _, PyModule},
    Bound, IntoPyObject, IntoPyObjectExt as _, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::{
    err::RustPanic,
    tokio::{future_into_py, future_into_py_with_locals, get_current_locals, get_runtime},
    TaskLocals,
};
use tokio::{sync::oneshot, task::JoinError};

use crate::shared::{constants::Constants, exception::panic_message};

/// Spawns the wrapped future, routing its outcome to the completion callback.
type Starter = Box<dyn FnOnce(Py<PyAny>) -> Abort + Send>;

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

/// What a spawned future needs from the calling task, captured up front for
/// awaitables created later from a different context (the request body pump).
pub(super) enum Locals {
    Asyncio(TaskLocals),
    Trio,
}

impl Locals {
    pub(super) fn current(py: Python<'_>, library: AsyncLibrary) -> PyResult<Self> {
        match library {
            AsyncLibrary::Asyncio => get_current_locals(py).map(Self::Asyncio),
            AsyncLibrary::Trio => Ok(Self::Trio),
        }
    }
}

/// Wraps `fut` in an awaitable for `library`.
pub(super) fn into_awaitable<'py, F, T>(
    py: Python<'py>,
    library: AsyncLibrary,
    fut: F,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    match library {
        AsyncLibrary::Asyncio => future_into_py(py, fut),
        AsyncLibrary::Trio => trio_awaitable(py, fut, None),
    }
}

/// Like [`into_awaitable`], for a task whose locals were captured earlier.
pub(super) fn into_awaitable_with_locals<'py, F, T>(
    py: Python<'py>,
    locals: &Locals,
    fut: F,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    match locals {
        Locals::Asyncio(locals) => future_into_py_with_locals(py, locals.clone(), fut),
        Locals::Trio => trio_awaitable(py, fut, None),
    }
}

/// Like [`into_awaitable`], with `on_done` invoked once the awaitable settles.
/// It receives an object with `result()`: the `asyncio.Future` itself, or its
/// trio stand-in. An `Exception` from `on_done` is logged under either runtime.
pub(super) fn into_awaitable_with_done<'py, F, T>(
    py: Python<'py>,
    library: AsyncLibrary,
    constants: &Constants,
    fut: F,
    on_done: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    match library {
        AsyncLibrary::Asyncio => {
            let fut = future_into_py(py, fut)?;
            fut.call_method1(&constants.add_done_callback, (on_done,))?;
            Ok(fut)
        }
        AsyncLibrary::Trio => trio_awaitable(py, fut, Some(on_done)),
    }
}

/// The `spawn_pump(fn, *args)` of `library`, which starts `fn(*args)` as a
/// detached task and returns its `PumpHandle`.
pub(super) fn pump_spawner<'a>(
    py: Python<'_>,
    library: AsyncLibrary,
    constants: &'a Constants,
) -> PyResult<&'a Py<PyAny>> {
    Ok(match library {
        AsyncLibrary::Asyncio => &constants.spawn_pump,
        AsyncLibrary::Trio => &trio_glue(py)?.spawn_pump,
    })
}

/// `pyqwest._trio`, imported on first use.
struct TrioGlue {
    await_kickoff: Py<PyAny>,
    spawn_pump: Py<PyAny>,
}

fn trio_glue(py: Python<'_>) -> PyResult<&'static TrioGlue> {
    static GLUE: PyOnceLock<TrioGlue> = PyOnceLock::new();
    GLUE.get_or_try_init(py, || {
        let module = PyModule::import(py, "pyqwest._trio")?;
        Ok(TrioGlue {
            await_kickoff: module.getattr("await_kickoff")?.unbind(),
            spawn_pump: module.getattr("spawn_pump")?.unbind(),
        })
    })
}

fn trio_awaitable<'py, F, T>(
    py: Python<'py>,
    fut: F,
    on_done: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>>
where
    F: Future<Output = PyResult<T>> + Send + 'static,
    T: for<'a> IntoPyObject<'a> + Send + 'static,
{
    let await_kickoff = trio_glue(py)?.await_kickoff.bind(py);

    // The future races the abort signal in its own task, so a panic in it
    // comes back as a `JoinError` and is still reported. Converting the result
    // and waking trio need the GIL, which a tokio worker must not wait for, so
    // both happen on the blocking pool.
    let starter: Starter = Box::new(move |completion: Py<PyAny>| {
        let (abort_tx, mut abort_rx) = oneshot::channel();
        let runtime = get_runtime();
        let race = runtime.spawn(async move {
            tokio::select! {
                biased;
                _ = &mut abort_rx => None,
                res = fut => Some(res),
            }
        });
        runtime.spawn(async move {
            let outcome = race.await;
            // try_attach: once the interpreter is finalizing, nothing awaits.
            let _ = tokio::task::spawn_blocking(move || {
                Python::try_attach(move |py| report(py, &completion, outcome));
            })
            .await;
        });
        Abort {
            abort: Mutex::new(Some(abort_tx)),
        }
    });
    let kickoff = Kickoff {
        starter: Mutex::new(Some(starter)),
    };
    await_kickoff.call1((kickoff, on_done))
}

/// Calls `completion(value, error, cancelled)` exactly once for `outcome`.
fn report<T>(
    py: Python<'_>,
    completion: &Py<PyAny>,
    outcome: Result<Option<PyResult<T>>, JoinError>,
) where
    T: for<'a> IntoPyObject<'a>,
{
    let result = match outcome {
        Ok(None) => Ok(None),
        // A panicking conversion must still wake the awaiter.
        Ok(Some(res)) => catch_unwind(AssertUnwindSafe(|| {
            res.and_then(|value| value.into_py_any(py)).map(Some)
        }))
        .unwrap_or_else(|payload| Err(panic_error(&*payload))),
        Err(join) => Err(join.try_into_panic().map_or_else(
            |_| PyRuntimeError::new_err("request task cancelled by its runtime"),
            |payload| panic_error(&*payload),
        )),
    };
    let args = match result {
        Ok(Some(value)) => (value, py.None(), false),
        Ok(None) => (py.None(), py.None(), true),
        Err(err) => (py.None(), err.into_value(py).into_any(), false),
    };
    if let Err(e) = completion.call1(py, args) {
        e.write_unraisable(py, None);
    }
}

/// The error for a panic in a request task: the same `RustPanic` callers see
/// from an asyncio request.
fn panic_error(payload: &(dyn Any + Send)) -> PyErr {
    RustPanic::new_err(format!("rust future panicked: {}", panic_message(payload)))
}

/// A future not yet running; `start` spawns it and routes its outcome to
/// `completion(value, error, cancelled)` on a tokio thread.
#[pyclass(module = "_pyqwest.async", frozen)]
struct Kickoff {
    starter: Mutex<Option<Starter>>,
}

#[pymethods]
impl Kickoff {
    fn start(&self, py: Python<'_>, completion: Py<PyAny>) -> PyResult<Abort> {
        let starter = self
            .starter
            .lock_py_attached(py)
            .unwrap_or_else(PoisonError::into_inner)
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("kickoff already started"))?;
        Ok(starter(completion))
    }
}

/// Cancels the spawned future. The completion callback then reports
/// `cancelled=True`, unless the future finished first. Dropping the handle
/// without calling `abort` cancels the future the same way.
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
