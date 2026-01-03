use http::HeaderValue;
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

    pub(crate) fn new_request(&self, py: Python<'_>, http3: bool) -> PyResult<reqwest::Request> {
        let mut req = reqwest::Request::new(self.method.clone(), self.url.clone());
        if http3 {
            *req.version_mut() = http::Version::HTTP_3;
        }
        if let Some(hdrs) = &self.headers {
            let hdrs = hdrs.bind(py).borrow();
            let hdrs_map = req.headers_mut();
            hdrs.with_store(py, |store| -> PyResult<()> {
                for (name, value) in store {
                    let value_str = value.extract::<&str>(py)?;
                    hdrs_map.append(
                        name.clone(),
                        HeaderValue::from_str(value_str).map_err(|e| {
                            PyValueError::new_err(format!("Invalid header value for '{name}': {e}"))
                        })?,
                    );
                }
                Ok(())
            })?;
        }
        Ok(req)
    }
}
