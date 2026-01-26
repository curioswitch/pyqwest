use std::fmt;

use pyo3::sync::MutexExt as _;
use pyo3::types::{PyAnyMethods as _, PyString};
use pyo3::{exceptions::PyValueError, Py, PyResult, Python};
use pyo3::{Bound, PyAny};

use crate::headers::Headers;
use crate::shared::constants::Constants;
use crate::sync::timeout::get_timeout;

pub(crate) struct RequestHead {
    method: http::Method,
    url: reqwest::Url,
    headers: Py<Headers>,
}

impl RequestHead {
    pub(crate) fn new(method: &str, url: &str, headers: Py<Headers>) -> PyResult<Self> {
        let method = http::Method::try_from(method)
            .map_err(|e| PyValueError::new_err(format!("Invalid HTTP method: {e}")))?;
        let url = reqwest::Url::parse(url)
            .map_err(|e| PyValueError::new_err(format!("Invalid URL: {e}")))?;
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
    ) -> PyResult<reqwest::RequestBuilder> {
        let mut req_builder = client.request(self.method.clone(), self.url.clone());
        if http3 {
            req_builder = req_builder.version(http::Version::HTTP_3);
        }
        let hdrs = self.headers.bind(py).borrow();
        for (name, value) in hdrs.store.lock_py_attached(py).unwrap().iter() {
            req_builder = req_builder.header(name, value.as_http(py)?);
        }
        if let Some(timeout) = get_timeout(py)? {
            req_builder = req_builder.timeout(timeout);
        }
        Ok(req_builder)
    }

    pub(crate) fn method(&self, py: Python<'_>) -> PyResult<Py<PyString>> {
        let constants = Constants::get(py)?;
        let res = match self.method {
            http::Method::GET => constants.get.clone_ref(py),
            http::Method::POST => constants.post.clone_ref(py),
            http::Method::PUT => constants.put.clone_ref(py),
            http::Method::DELETE => constants.delete.clone_ref(py),
            http::Method::HEAD => constants.head.clone_ref(py),
            http::Method::OPTIONS => constants.options.clone_ref(py),
            http::Method::PATCH => constants.patch.clone_ref(py),
            http::Method::TRACE => constants.trace.clone_ref(py),
            _ => PyString::new(py, self.method.as_str()).unbind(),
        };
        Ok(res)
    }

    pub(crate) fn url(&self) -> &str {
        self.url.as_str()
    }

    pub(crate) fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.headers.clone_ref(py)
    }
}

pub(crate) type RequestStreamResult<T> = Result<T, RequestStreamError>;

#[derive(Debug)]
pub(crate) struct RequestStreamError {
    msg: String,
}

impl RequestStreamError {
    pub(crate) fn new(msg: String) -> Self {
        Self { msg }
    }

    pub(crate) fn from_py(err: &Bound<'_, PyAny>) -> Self {
        if let Ok(msg) = err.str() {
            Self {
                msg: msg.to_string(),
            }
        } else {
            Self {
                msg: "Unknown Error".to_string(),
            }
        }
    }
}

impl std::error::Error for RequestStreamError {}

impl fmt::Display for RequestStreamError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.msg.fmt(f)
    }
}
