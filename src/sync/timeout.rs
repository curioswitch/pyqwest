use std::time::{Duration, Instant};

use pyo3::{pyclass, pyfunction, pymethods, Bound, Py, PyAny, PyResult, Python};

use crate::shared::constants::Constants;

#[pyclass(module = "_pyqwest", frozen)]
struct Deadline(Instant);

#[pyfunction(name = "set_sync_timeout")]
pub(crate) fn set_timeout(py: Python<'_>, timeout: f64) -> PyResult<DeadlineManager> {
    let deadline = Deadline(Instant::now() + Duration::from_secs_f64(timeout));
    let constants = Constants::get(py)?;

    let token = constants.timeout_context_var_set.call1(py, (deadline,))?;

    Ok(DeadlineManager { token })
}

#[pyfunction(name = "get_sync_timeout")]
pub(crate) fn get_timeout(py: Python<'_>) -> PyResult<Option<Duration>> {
    let constants = Constants::get(py)?;
    let deadline: Option<Bound<'_, Deadline>> = constants
        .timeout_context_var_get
        .call1(py, (py.None(),))?
        .extract(py)?;

    Ok(deadline.map(|d| d.get().0 - Instant::now()))
}

#[pyclass(module = "_pyqwest", frozen)]
pub(crate) struct DeadlineManager {
    token: Py<PyAny>,
}

#[pymethods]
impl DeadlineManager {
    fn __enter__(slf: Py<DeadlineManager>) -> Py<DeadlineManager> {
        slf
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc_value: Option<&Bound<'_, PyAny>>,
        _traceback: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<()> {
        let constants = Constants::get(py)?;
        constants
            .timeout_context_var_reset
            .call1(py, (&self.token,))?;
        Ok(())
    }
}

impl DeadlineManager {
    pub(super) fn enter(self, py: Python<'_>) -> DeadlineManagerGuard<'_> {
        DeadlineManagerGuard { manager: self, py }
    }
}

pub(super) struct DeadlineManagerGuard<'py> {
    manager: DeadlineManager,
    py: Python<'py>,
}

impl Drop for DeadlineManagerGuard<'_> {
    fn drop(&mut self) {
        let _ = self.manager.__exit__(self.py, None, None, None);
    }
}
