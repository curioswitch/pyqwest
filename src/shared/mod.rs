/// Backoff utility for retry middleware.
pub(crate) mod backoff;
/// Shared utilities for bytes buffers.
pub(crate) mod buffer;
/// Connection metrics reported by the connection pool.
pub(crate) mod connection_metrics;
/// Shared constants.
pub(crate) mod constants;
/// Shared utilities for the thread's Python exception state.
pub(crate) mod exception;
/// Shared utilities for `OTel` instrumentation.
pub(crate) mod otel;
/// Shared utilities for request handling.
pub(crate) mod request;
/// Shared utilities for response handling.
pub(crate) mod response;
/// The tokio runtime the crate runs its futures on.
pub(crate) mod runtime;
/// Shared utilities for creating transports.
pub(crate) mod transport;
/// Shared utilities for validating arguments.
pub(crate) mod validation;
/// Sockets that wake Python code from tokio threads without the GIL.
pub(crate) mod wakeup;
