use std::fmt;

use http::HeaderValue;
use pyo3::sync::MutexExt as _;
use pyo3::types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyString, PyStringMethods as _};
use pyo3::{exceptions::PyValueError, Py, PyResult, Python};
use pyo3::{Bound, PyAny};
use url::form_urlencoded::Serializer;
use url::UrlQuery;

use crate::headers::Headers;
use crate::shared::constants::Constants;
use crate::sync::timeout::get_timeout;

const CONTENT_TYPE_JSON: HeaderValue = HeaderValue::from_static("application/json");

pub(crate) struct RequestHead {
    method: http::Method,
    url: reqwest::Url,
    headers: Py<Headers>,
    /// Whether to append JSON content type header automatically.
    json: bool,
}

impl RequestHead {
    pub(crate) fn new(
        method: &str,
        url: &str,
        headers: Py<Headers>,
        params: Option<Bound<'_, PyAny>>,
        json: bool,
    ) -> PyResult<Self> {
        let method = http::Method::try_from(method)
            .map_err(|e| PyValueError::new_err(format!("Invalid HTTP method: {e}")))?;
        let mut url = reqwest::Url::parse(url)
            .map_err(|e| PyValueError::new_err(format!("Invalid URL: {e}")))?;
        if let Some(params) = params {
            let mut url_params = url.query_pairs_mut();
            if let Ok(params_dict) = params.cast::<PyDict>() {
                for (key, value) in params_dict.iter() {
                    append_query_param(&mut url_params, &key, &value)?;
                }
            } else {
                for item in params.try_iter()? {
                    let item = item?;
                    let key = item.get_item(0)?;
                    let value = item.get_item(1)?;
                    append_query_param(&mut url_params, &key, &value)?;
                }
            }
        }
        Ok(Self {
            method,
            url,
            headers,
            json,
        })
    }

    pub(crate) fn new_reqwest(&self, py: Python<'_>, http3: bool) -> PyResult<reqwest::Request> {
        let mut req = reqwest::Request::new(self.method.clone(), self.url.clone());
        if http3 {
            *req.version_mut() = http::Version::HTTP_3;
        }
        let hdrs = self.headers.bind(py).borrow();
        for (name, value) in hdrs.store.lock_py_attached(py).unwrap().iter() {
            req.headers_mut().append(name, value.as_http(py)?);
        }
        if self.json && !req.headers().contains_key(http::header::CONTENT_TYPE) {
            req.headers_mut()
                .insert(http::header::CONTENT_TYPE, CONTENT_TYPE_JSON);
        }
        if let Some(timeout) = get_timeout(py)? {
            *req.timeout_mut() = Some(timeout);
        }
        Ok(req)
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

    pub(crate) fn parsed_url(&self) -> &reqwest::Url {
        &self.url
    }

    pub(crate) fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.headers.clone_ref(py)
    }

    pub(crate) fn json(&self) -> bool {
        self.json
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

fn append_query_param(
    url_params: &mut Serializer<'_, UrlQuery<'_>>,
    key: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let key = key.cast::<PyString>()?;
    if value.is_none() {
        url_params.append_key_only(key.to_str()?);
    } else {
        let value = value.cast::<PyString>()?;
        url_params.append_pair(key.to_str()?, value.to_str()?);
    }
    Ok(())
}

pub(crate) fn maybe_encode_json_content<'py>(
    py: Python<'py>,
    value: &Option<Bound<'py, PyAny>>,
    constants: &Constants,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    if !value.is_instance_of::<PyDict>() {
        return Ok(None);
    }
    let json_str = constants.json_dumps.bind(py).call1((value,))?;
    Ok(Some(json_str.cast::<PyString>()?.encode_utf8()?.into_any()))
}
