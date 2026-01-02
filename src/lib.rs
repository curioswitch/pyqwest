use pyo3::ffi::c_str;
use pyo3::prelude::*;

mod asyncio;
mod common;
mod headers;
/// Shared utilities between asyncio and sync modules.
/// Code exposed to Python should be in common instead.
pub(crate) mod shared;
mod sync;

fn add_protocols(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    let module_dict = m.dict();
    py.run(
        c_str!(
            r#"
from typing import Protocol as _Protocol

class Transport(_Protocol):
    async def execute(self, request: Request) -> Response: ...

class SyncTransport(_Protocol):
    def execute(self, request: SyncRequest) -> SyncResponse: ...

del _Protocol
"#
        ),
        Some(&module_dict),
        None,
    )
}

/// Entrypoint to pyqwest extension module.
#[pymodule(gil_used = false)]
fn pyqwest(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<asyncio::client::Client>()?;
    m.add_class::<asyncio::request::Request>()?;
    m.add_class::<asyncio::response::Response>()?;
    m.add_class::<asyncio::transport::HttpTransport>()?;
    m.add_class::<common::HTTPVersion>()?;
    m.add_class::<headers::Headers>()?;
    m.add_class::<sync::client::SyncClient>()?;
    m.add_class::<sync::request::SyncRequest>()?;
    m.add_class::<sync::response::SyncResponse>()?;
    m.add_class::<sync::transport::SyncHttpTransport>()?;
    add_protocols(py, m)?;
    Ok(())
}
