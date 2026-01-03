use pyo3::{intern, prelude::*};

use crate::sync::request::SyncRequest;
use crate::sync::transport::SyncHttpTransport;

enum Transport {
    Http(SyncHttpTransport),
    Custom(Py<PyAny>),
}

#[pyclass(module = "pyqwest")]
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

    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn execute<'py>(
        &self,
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = SyncRequest::new(py, method, url, headers, content)?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute(py, &request),
            Transport::Custom(transport) => transport
                .bind(py)
                .call_method1(intern!(py, "execute"), (request,)),
        }
    }
}
