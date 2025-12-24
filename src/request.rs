use pyo3::{pyclass, pymethods, Bound, Py, PyAny, PyResult, Python};

use crate::{body::Body, headers::Headers};

#[pyclass]
pub struct Request {
    pub(crate) method: http::Method,
    pub(crate) url: reqwest::Url,
    pub(crate) headers: Option<Py<Headers>>,
    pub(crate) body: Option<Body>,
}

#[pymethods]
impl Request {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None))]
    fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        let method = method.parse::<http::Method>().map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid HTTP method: {}", e))
        })?;
        let url = reqwest::Url::parse(url)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid URL: {}", e)))?;
        let headers = if let Some(headers) = headers {
            if let Ok(hdrs) = headers.cast::<Headers>() {
                Some(hdrs.clone().unbind())
            } else {
                Some(Py::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let body = match content {
            Some(body) => Some(Body::from_py_any(body)?),
            None => None,
        };
        Ok(Self {
            method,
            url,
            headers,
            body,
        })
    }
}
