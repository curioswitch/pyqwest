use std::sync::Arc;

use arc_swap::ArcSwapOption;
use pyo3::exceptions::PyRuntimeError;
use pyo3::sync::PyOnceLock;
use pyo3::{prelude::*, IntoPyObjectExt as _};
use pyo3_async_runtimes::tokio::future_into_py;

use crate::asyncio::awaitable::{EmptyAwaitable, ValueAwaitable};
use crate::asyncio::request::Request;
use crate::asyncio::response::Response;
use crate::common::httpversion::HTTPVersion;
use crate::pyerrors;
use crate::shared::constants::Constants;
use crate::shared::otel::{Instrumentation, Operation};
use crate::shared::transport::{get_default_reqwest_client, new_reqwest_client, ClientParams};

#[pyclass(module = "_pyqwest", name = "HTTPTransport", frozen)]
#[derive(Clone)]
pub struct HttpTransport {
    client: Arc<ArcSwapOption<reqwest::Client>>,
    http3: bool,
    close: bool,

    instrumentation: Instrumentation,
    constants: Constants,
}

#[pymethods]
impl HttpTransport {
    #[new]
    #[pyo3(signature = (
        *,
        tls_ca_cert = None,
        tls_key = None,
        tls_cert = None,
        http_version = None,
        timeout = None,
        connect_timeout = 30.0,
        read_timeout = None,
        pool_idle_timeout = 90.0,
        pool_max_idle_per_host = None,
        tcp_keepalive_interval = 30.0,
        enable_gzip = true,
        enable_brotli = true,
        enable_zstd = true,
        use_system_dns = false,
        meter_provider = None,
        tracer_provider = None,
    ))]
    pub(crate) fn new(
        py: Python<'_>,
        tls_ca_cert: Option<&[u8]>,
        tls_key: Option<&[u8]>,
        tls_cert: Option<&[u8]>,
        http_version: Option<Bound<'_, HTTPVersion>>,
        timeout: Option<f64>,
        connect_timeout: Option<f64>,
        read_timeout: Option<f64>,
        pool_idle_timeout: Option<f64>,
        pool_max_idle_per_host: Option<usize>,
        tcp_keepalive_interval: Option<f64>,
        enable_gzip: bool,
        enable_brotli: bool,
        enable_zstd: bool,
        use_system_dns: bool,
        meter_provider: Option<Bound<'_, PyAny>>,
        tracer_provider: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let (client, http3) = new_reqwest_client(ClientParams {
            tls_ca_cert,
            tls_key,
            tls_cert,
            http_version,
            timeout,
            connect_timeout,
            read_timeout,
            pool_idle_timeout,
            pool_max_idle_per_host,
            tcp_keepalive_interval,
            enable_gzip,
            enable_brotli,
            enable_zstd,
            use_system_dns,
        })?;
        let constants = Constants::get(py)?;
        Ok(Self {
            client: Arc::new(ArcSwapOption::from_pointee(client)),
            http3,
            close: true,
            instrumentation: Instrumentation::new(py, meter_provider, tracer_provider, &constants)?,
            constants,
        })
    }

    fn __aenter__(slf: Py<HttpTransport>, py: Python<'_>) -> PyResult<Py<PyAny>> {
        ValueAwaitable {
            value: Some(slf.into_any()),
        }
        .into_py_any(py)
    }

    fn __aexit__(
        &self,
        py: Python<'_>,
        _exc_type: Py<PyAny>,
        _exc_value: Py<PyAny>,
        _traceback: Py<PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.aclose(py)
    }

    fn execute<'py>(
        &self,
        py: Python<'py>,
        request: &Bound<'py, Request>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.do_stream(py, request.get())
    }

    fn aclose(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.close {
            self.client.store(None);
        }
        EmptyAwaitable.into_py_any(py)
    }
}

impl HttpTransport {
    pub(super) fn do_stream<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let (mut request_rs, request_iter_task) = request.new_reqwest(py, self.http3)?;
        let mut response = Response::pending(py, request_iter_task, self.constants.clone())?;
        let operation = self.instrumentation.start(py, &request.head)?;
        operation.inject(py, &mut request_rs)?;
        let fut = future_into_py(py, {
            let client = client.clone();
            let operation = operation.clone();
            async move {
                let res = client
                    .execute(request_rs)
                    .await
                    .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
                operation.fill_response(&res);
                response.fill(res).await;
                Ok(response)
            }
        })?;
        fut.call_method1(
            &self.constants.add_done_callback,
            (EndOperationCallback {
                operation,
                constants: self.constants.clone(),
            }
            .into_bound_py_any(py)?,),
        )?;
        Ok(fut)
    }

    pub(super) fn do_execute<'py>(
        &self,
        py: Python<'py>,
        request: &Request,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client_guard = self.client.load();
        let Some(client) = client_guard.as_ref() else {
            return Err(PyRuntimeError::new_err(
                "Executing request on already closed transport",
            ));
        };
        let (mut request_rs, request_iter_task) = request.new_reqwest(py, self.http3)?;
        let mut response = Response::pending(py, request_iter_task, self.constants.clone())?;
        let operation = self.instrumentation.start(py, &request.head)?;
        operation.inject(py, &mut request_rs)?;
        let fut = future_into_py(py, {
            let client = client.clone();
            let operation = operation.clone();
            async move {
                let res = client
                    .execute(request_rs)
                    .await
                    .map_err(|e| pyerrors::from_reqwest(&e, "Request failed"))?;
                operation.fill_response(&res);
                response.fill(res).await;
                let full_response = response.into_full_response().await?;
                Ok(full_response)
            }
        })?;
        fut.call_method1(
            &self.constants.add_done_callback,
            (EndOperationCallback {
                operation,
                constants: self.constants.clone(),
            }
            .into_bound_py_any(py)?,),
        )?;
        Ok(fut)
    }

    pub(super) fn py_default(py: Python<'_>) -> PyResult<Self> {
        let constants = Constants::get(py)?;
        Ok(Self {
            client: Arc::new(ArcSwapOption::from_pointee(get_default_reqwest_client(py))),
            http3: false,
            close: false,
            instrumentation: Instrumentation::new(py, None, None, &constants)?,
            constants,
        })
    }
}

static DEFAULT_TRANSPORT: PyOnceLock<Py<HttpTransport>> = PyOnceLock::new();

#[pyfunction]
pub(crate) fn get_default_transport(py: Python<'_>) -> PyResult<Py<HttpTransport>> {
    Ok(DEFAULT_TRANSPORT
        .get_or_try_init(py, || Py::new(py, HttpTransport::py_default(py)?))?
        .clone_ref(py))
}

#[pyclass(module = "_pyqwest.async", frozen)]
struct EndOperationCallback {
    operation: Operation,
    constants: Constants,
}

#[pymethods]
impl EndOperationCallback {
    fn __call__(&self, py: Python<'_>, fut: &Bound<'_, PyAny>) -> PyResult<()> {
        let res = fut.call_method0(&self.constants.result);
        self.operation.end(py, res.as_ref().err())
    }
}
