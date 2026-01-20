use pyo3::{prelude::*, IntoPyObjectExt as _};

use crate::headers::Headers;
use crate::shared::constants::Constants;
use crate::shared::validation::validate_timeout;
use crate::sync::request::SyncRequest;
use crate::sync::response::SyncResponse;
use crate::sync::timeout::set_timeout;
use crate::sync::transport::{get_default_sync_transport, SyncHttpTransport};

enum Transport {
    Http(SyncHttpTransport),
    Custom(Py<PyAny>),
}

#[pyclass(module = "_pyqwest", frozen)]
pub struct SyncClient {
    transport: Transport,

    constants: Constants,
}

#[pymethods]
impl SyncClient {
    #[new]
    #[pyo3(signature = (transport=None))]
    fn new(py: Python<'_>, transport: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let transport = if let Some(transport) = transport {
            if let Ok(transport) = transport.extract::<SyncHttpTransport>() {
                Transport::Http(transport)
            } else {
                Transport::Custom(transport.unbind())
            }
        } else {
            let transport = get_default_sync_transport(py)?;
            Transport::Http(transport.get().clone())
        };
        Ok(Self {
            transport,
            constants: Constants::get(py)?,
        })
    }

    #[pyo3(signature = (url, headers=None, timeout=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "GET", url, headers, None, timeout)
    }

    #[pyo3(signature = (url, headers=None, content=None, timeout=None))]
    fn post<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "POST", url, headers, content, timeout)
    }

    #[pyo3(signature = (url, headers=None, timeout=None))]
    fn delete<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "DELETE", url, headers, None, timeout)
    }

    #[pyo3(signature = (url, headers=None, timeout=None))]
    fn head<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "HEAD", url, headers, None, timeout)
    }

    #[pyo3(signature = (url, headers=None, timeout=None))]
    fn options<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "OPTIONS", url, headers, None, timeout)
    }

    #[pyo3(signature = (url, headers=None, content=None, timeout=None))]
    fn patch<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "PATCH", url, headers, content, timeout)
    }

    #[pyo3(signature = (url, headers=None, content=None, timeout=None))]
    fn put<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "PUT", url, headers, content, timeout)
    }

    #[pyo3(signature = (method, url, headers=None, content=None, timeout=None))]
    fn execute<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let response = self.do_stream(py, method, url, headers, content, timeout)?;
        response.get().read_full(py)
    }

    #[pyo3(signature = (method, url, headers=None, content=None, timeout=None))]
    fn stream<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let response = self.do_stream(py, method, url, headers, content, timeout)?;
        ResponseContextManager {
            response: response.unbind(),
        }
        .into_bound_py_any(py)
    }
}

impl SyncClient {
    fn do_stream<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, SyncResponse>> {
        let timeout = validate_timeout(timeout)?;
        let _timeout_guard = if let Some(timeout) = timeout {
            Some(set_timeout(py, timeout)?.enter(py))
        } else {
            None
        };
        let headers = if let Some(headers) = headers {
            if let Ok(headers) = headers.cast::<Headers>() {
                Some(headers.clone())
            } else {
                Some(Bound::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let request = SyncRequest::new(py, method, url, headers, content)?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute(py, &request)?.into_pyobject(py),
            Transport::Custom(transport) => {
                let res = transport
                    .bind(py)
                    .call_method1(&self.constants.execute_sync, (request,))?;
                Ok(res.cast_into::<SyncResponse>()?)
            }
        }
    }
}

#[pyclass(module = "_pyqwest.sync", frozen)]
struct ResponseContextManager {
    response: Py<SyncResponse>,
}

#[pymethods]
impl ResponseContextManager {
    fn __enter__(&self, py: Python<'_>) -> Py<SyncResponse> {
        self.response.clone_ref(py)
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        exc_type: Option<&Bound<'_, PyAny>>,
        exc_value: Option<&Bound<'_, PyAny>>,
        traceback: Option<&Bound<'_, PyAny>>,
    ) {
        self.response
            .get()
            .__exit__(py, exc_type, exc_value, traceback);
    }
}
