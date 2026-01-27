use std::time::Duration;

use backoff::{backoff::Backoff as _, ExponentialBackoff, ExponentialBackoffBuilder};
use pyo3::{pyclass, pymethods};

#[pyclass(module = "_pyqwest.backoff", name = "_Backoff")]
pub(crate) struct Backoff {
    delegate: ExponentialBackoff,
}

#[pymethods]
impl Backoff {
    #[new]
    fn new(
        initial_interval: f64,
        randomization_factor: f64,
        multiplier: f64,
        max_interval: f64,
    ) -> Self {
        let delegate = ExponentialBackoffBuilder::new()
            .with_initial_interval(Duration::from_secs_f64(initial_interval))
            .with_randomization_factor(randomization_factor)
            .with_multiplier(multiplier)
            .with_max_interval(Duration::from_secs_f64(max_interval))
            .build();
        Self { delegate }
    }
    fn next_backoff(&mut self) -> Option<f64> {
        self.delegate.next_backoff().map(|d| d.as_secs_f64())
    }
}
