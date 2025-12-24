use pyo3::prelude::*;

mod body;
mod client;
mod headers;
mod request;
mod response;

/// Entrypoint to pyqwest extension module.
#[pymodule]
fn pyqwest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<client::Client>()?;
    m.add_class::<client::HTTPVersion>()?;
    m.add_class::<body::Body>()?;
    m.add_class::<headers::Headers>()?;
    m.add_class::<request::Request>()?;
    m.add_class::<response::Response>()?;
    Ok(())
}
