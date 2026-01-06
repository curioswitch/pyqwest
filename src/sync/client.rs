use pyo3::{intern, prelude::*};

use crate::headers::Headers;
use crate::sync::request::SyncRequest;
use crate::sync::transport::SyncHttpTransport;

enum Transport {
    Http(SyncHttpTransport),
    Custom(Py<PyAny>),
}

#[pyclass(module = "pyqwest", frozen)]
pub struct SyncClient {
    transport: Transport,
}

#[pymethods]
impl SyncClient {
    #[new]
    fn new(transport: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let transport = if let Some(transport) = transport {
            if let Ok(transport) = transport.extract::<SyncHttpTransport>() {
                Transport::Http(transport)
            } else {
                Transport::Custom(transport.unbind())
            }
        } else {
            Transport::Http(SyncHttpTransport::new(None, None)?)
        };
        Ok(Self { transport })
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
        let headers = if let Some(headers) = headers {
            if let Ok(headers) = headers.cast::<Headers>() {
                Some(headers.clone())
            } else {
                Some(Bound::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let request = SyncRequest::new(py, method, url, headers, content, timeout)?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute(py, &request),
            Transport::Custom(transport) => transport
                .bind(py)
                .call_method1(intern!(py, "execute"), (request,)),
        }
    }
}
