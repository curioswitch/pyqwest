use pyo3::{intern, prelude::*};

use crate::asyncio::request::Request;
use crate::asyncio::transport::HttpTransport;

enum Transport {
    Http(HttpTransport),
    Custom(Py<PyAny>),
}

#[pyclass(module = "pyqwest", frozen)]
pub struct Client {
    transport: Transport,
}

#[pymethods]
impl Client {
    #[new]
    fn new(transport: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let transport = if let Some(transport) = transport {
            if let Ok(transport) = transport.extract::<HttpTransport>() {
                Transport::Http(transport)
            } else {
                Transport::Custom(transport.unbind())
            }
        } else {
            Transport::Http(HttpTransport::new(None, None)?)
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
        let request = Request::new(py, method, url, headers, content)?;
        match &self.transport {
            Transport::Http(transport) => transport.do_execute(py, &request),
            Transport::Custom(transport) => transport
                .bind(py)
                .call_method1(intern!(py, "execute"), (request,)),
        }
    }
}
