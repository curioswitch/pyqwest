use http::HeaderMap;
use pyo3::exceptions::PyValueError;
use pyo3::sync::MutexExt as _;
use pyo3::{pyclass, pymethods, Bound, PyAny, PyResult, Python};

use crate::headers::Headers;

/// A proxy to route requests through, with optional authentication,
/// extra headers, and scheme / `no_proxy` based routing rules.
#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct Proxy {
    inner: reqwest::Proxy,
    url: String,
    scheme: String,
}

#[pymethods]
impl Proxy {
    #[new]
    #[pyo3(signature = (url, *, auth=None, headers=None, no_proxy=None, scheme="all"))]
    fn py_new(
        py: Python<'_>,
        url: &str,
        auth: Option<(String, String)>,
        headers: Option<Bound<'_, PyAny>>,
        no_proxy: Option<&str>,
        scheme: &str,
    ) -> PyResult<Self> {
        let mut proxy = parse_proxy(match scheme {
            "all" => reqwest::Proxy::all(url),
            "http" => reqwest::Proxy::http(url),
            "https" => reqwest::Proxy::https(url),
            _ => {
                return Err(PyValueError::new_err(format!(
                    "Invalid proxy scheme '{scheme}', must be 'all', 'http', or 'https'"
                )))
            }
        })?;
        if let Some((username, password)) = auth {
            proxy = proxy.basic_auth(&username, &password);
        }
        if let Some(headers) = headers {
            let headers = if let Ok(headers) = headers.cast::<Headers>() {
                headers.clone()
            } else {
                Bound::new(py, Headers::py_new(Some(headers))?)?
            };
            let mut header_map = HeaderMap::new();
            for (name, value) in headers.get().store.lock_py_attached(py).unwrap().iter() {
                header_map.append(name, value.as_http(py)?);
            }
            proxy = proxy.headers(header_map);
        }
        if let Some(no_proxy) = no_proxy {
            proxy = proxy.no_proxy(reqwest::NoProxy::from_string(no_proxy));
        }
        Ok(Self {
            inner: proxy,
            url: url.to_string(),
            scheme: scheme.to_string(),
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "Proxy(url=\"{}\", scheme=\"{}\")",
            mask_url(&self.url),
            self.scheme
        )
    }
}

impl Proxy {
    pub(crate) fn as_reqwest(&self) -> reqwest::Proxy {
        self.inner.clone()
    }
}

/// Parses a proxy URL that routes all requests, as `Proxy(url)` does.
pub(crate) fn proxy_from_url(url: &str) -> PyResult<reqwest::Proxy> {
    parse_proxy(reqwest::Proxy::all(url))
}

fn parse_proxy(proxy: reqwest::Result<reqwest::Proxy>) -> PyResult<reqwest::Proxy> {
    proxy.map_err(|e| {
        PyValueError::new_err(format!("Failed to parse proxy URL: {:+}", errors::fmt(&e)))
    })
}

/// Masks credentials in the URL for display.
fn mask_url(url: &str) -> String {
    let parsed = url::Url::parse(url)
        .or_else(|_| url::Url::parse(&format!("http://{url}")))
        .ok();
    match parsed {
        Some(mut parsed) => {
            if parsed.password().is_some() {
                let _ = parsed.set_password(Some("********"));
            }
            parsed.to_string()
        }
        None => url.to_string(),
    }
}
