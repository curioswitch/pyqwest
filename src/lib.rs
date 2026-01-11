use pyo3::ffi::c_str;
use pyo3::prelude::*;

mod asyncio;
mod common;
mod headers;
mod pyerrors;
/// Shared utilities between asyncio and sync modules.
/// Code exposed to Python should be in common or pyerrors
/// instead.
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

#[pymodule(gil_used = false)]
mod pyqwest {
    #[allow(clippy::wildcard_imports)]
    use crate::*;

    #[pymodule_export]
    use asyncio::client::Client;
    #[pymodule_export]
    use asyncio::request::Request;
    #[pymodule_export]
    use asyncio::response::Response;
    #[pymodule_export]
    use asyncio::transport::{get_default_transport, HttpTransport};
    #[pymodule_export]
    use common::{FullResponse, HTTPVersion};
    #[pymodule_export]
    use headers::Headers;
    #[pymodule_export]
    use pyerrors::{ReadError, StreamError, StreamErrorCode, WriteError};
    #[pymodule_export]
    use sync::client::SyncClient;
    #[pymodule_export]
    use sync::request::SyncRequest;
    #[pymodule_export]
    use sync::response::SyncResponse;
    #[pymodule_export]
    use sync::transport::{get_default_sync_transport, SyncHttpTransport};

    #[pymodule_init]
    fn init(m: &Bound<'_, PyModule>) -> PyResult<()> {
        add_protocols(m.py(), m)?;
        Ok(())
    }
}
