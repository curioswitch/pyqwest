use pyo3::sync::MutexExt as _;
use pyo3::{exceptions::PyValueError, Bound, Py, PyAny, PyResult, Python};

use crate::headers::Headers;

pub(crate) struct RequestHead {
    method: http::Method,
    url: reqwest::Url,
    headers: Option<Py<Headers>>,
}

impl RequestHead {
    pub(crate) fn new(
        py: Python<'_>,
        method: &str,
        url: &str,
        headers: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let method = http::Method::try_from(method)
            .map_err(|e| PyValueError::new_err(format!("Invalid HTTP method: {e}")))?;
        let url = reqwest::Url::parse(url)
            .map_err(|e| PyValueError::new_err(format!("Invalid URL: {e}")))?;
        let headers = if let Some(headers) = headers {
            if let Ok(hdrs) = headers.cast::<Headers>() {
                Some(hdrs.clone().unbind())
            } else {
                Some(Py::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        Ok(Self {
            method,
            url,
            headers,
        })
    }

    pub(crate) fn new_request_builder(
        &self,
        py: Python<'_>,
        client: &reqwest::Client,
        http3: bool,
    ) -> reqwest::RequestBuilder {
        let mut req_builder = client.request(self.method.clone(), self.url.clone());
        if http3 {
            req_builder = req_builder.version(http::Version::HTTP_3);
        }
        if let Some(hdrs) = &self.headers {
            let hdrs = hdrs.bind(py).borrow();
            for (name, value) in hdrs.store.lock_py_attached(py).unwrap().iter() {
                req_builder = req_builder.header(name, value);
            }
        }
        req_builder
    }
}
