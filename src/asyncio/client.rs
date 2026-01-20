use pyo3::prelude::*;

use crate::asyncio::request::Request;
use crate::asyncio::transport::{get_default_transport, HttpTransport};
use crate::headers::Headers;
use crate::shared::constants::Constants;

enum Transport {
    Http(HttpTransport),
    Custom(Py<PyAny>),
}

#[pyclass(module = "_pyqwest.async", frozen)]
pub(crate) struct Client {
    transport: Transport,

    constants: Constants,
}

#[pymethods]
impl Client {
    #[new]
    #[pyo3(signature = (transport=None))]
    fn new(py: Python<'_>, transport: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let transport = if let Some(transport) = transport {
            if let Ok(transport) = transport.extract::<HttpTransport>() {
                Transport::Http(transport)
            } else {
                Transport::Custom(transport.unbind())
            }
        } else {
            let transport = get_default_transport(py)?;
            Transport::Http(transport.get().clone())
        };
        Ok(Self {
            transport,
            constants: Constants::get(py)?,
        })
    }

    #[pyo3(signature = (url, headers=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "GET", url, headers, None)
    }

    #[pyo3(signature = (url, headers=None, content=None))]
    fn post<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "POST", url, headers, content)
    }

    #[pyo3(signature = (url, headers=None))]
    fn delete<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "DELETE", url, headers, None)
    }

    #[pyo3(signature = (url, headers=None))]
    fn head<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "HEAD", url, headers, None)
    }

    #[pyo3(signature = (url, headers=None))]
    fn options<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "OPTIONS", url, headers, None)
    }

    #[pyo3(signature = (url, headers=None, content=None))]
    fn patch<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "PATCH", url, headers, content)
    }

    #[pyo3(signature = (url, headers=None, content=None))]
    fn put<'py>(
        &self,
        py: Python<'py>,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        self.execute(py, "PUT", url, headers, content)
    }

    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn execute<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let headers = if let Some(headers) = headers {
            if let Ok(headers) = headers.cast::<Headers>() {
                Some(headers.clone())
            } else {
                Some(Bound::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let request = Request::new(py, method, url, headers, content, self.constants.clone())?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute_and_read_full(py, &request),
            Transport::Custom(transport) => self
                .constants
                .execute_and_read_full
                .bind(py)
                .call1((transport, request)),
        }
    }

    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn stream<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let headers = if let Some(headers) = headers {
            if let Ok(headers) = headers.cast::<Headers>() {
                Some(headers.clone())
            } else {
                Some(Bound::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let request = Request::py_new(py, method, url, headers, content)?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute(py, &request),
            Transport::Custom(transport) => transport
                .bind(py)
                .call_method1(&self.constants.execute, (request,)),
        }
    }
}
