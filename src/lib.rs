use pyo3::prelude::*;

mod asyncio;
mod common;
mod headers;
mod sync;

/// Entrypoint to pyqwest extension module.
#[pymodule(gil_used = false)]
fn pyqwest(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<asyncio::client::Client>()?;
    m.add_class::<asyncio::request::Request>()?;
    m.add_class::<asyncio::response::Response>()?;
    m.add_class::<common::HTTPVersion>()?;
    m.add_class::<headers::Headers>()?;
    m.add_class::<sync::client::SyncClient>()?;
    m.add_class::<sync::request::SyncRequest>()?;
    m.add_class::<sync::response::SyncResponse>()?;
    Ok(())
}
