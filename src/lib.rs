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
from collections.abc import Awaitable as _Awaitable
from typing import Protocol as _Protocol, runtime_checkable as _runtime_checkable

@_runtime_checkable
class Transport(_Protocol):
    def execute(self, request: Request) -> _Awaitable[Response]: ...

@_runtime_checkable
class SyncTransport(_Protocol):
    def execute_sync(self, request: SyncRequest) -> SyncResponse: ...

del _Protocol
del _runtime_checkable
"#
        ),
        Some(&module_dict),
        None,
    )
}

#[pymodule(name = "_pyqwest", gil_used = false)]
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
    use common::decompress::{BrotliDecompressor, ZstdDecompressor};
    #[pymodule_export]
    use common::{headername::HttpHeaderName, httpversion::HTTPVersion, FullResponse};
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
    use sync::timeout::{get_timeout, set_timeout};
    #[pymodule_export]
    use sync::transport::{get_default_sync_transport, SyncHttpTransport};

    #[pymodule_init]
    fn init(m: &Bound<'_, PyModule>) -> PyResult<()> {
        add_protocols(m.py(), m)?;

        Ok(())
    }
}
