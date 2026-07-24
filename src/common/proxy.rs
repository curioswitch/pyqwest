use http::HeaderMap;
use pyo3::exceptions::PyValueError;
use pyo3::{pyclass, pymethods, Bound, PyAny, PyResult, Python};

use crate::headers::Headers;

/// A proxy to route requests through, with optional authentication,
/// extra headers, and scheme / `no_proxy` based routing rules.
#[pyclass(module = "pyqwest", frozen)]
pub(crate) struct Proxy {
    inner: reqwest::Proxy,
    url: String,
    scheme: Option<String>,
}

#[pymethods]
impl Proxy {
    #[new]
    #[pyo3(signature = (url, *, auth=None, headers=None, no_proxy=None, scheme=None))]
    fn py_new(
        py: Python<'_>,
        url: &str,
        auth: Option<(String, String)>,
        headers: Option<Bound<'_, PyAny>>,
        no_proxy: Option<&str>,
        scheme: Option<&str>,
    ) -> PyResult<Self> {
        let mut proxy = parse_proxy(match scheme {
            None => reqwest::Proxy::all(url),
            Some("http") => reqwest::Proxy::http(url),
            Some("https") => reqwest::Proxy::https(url),
            Some(scheme) => {
                return Err(PyValueError::new_err(format!(
                    "Invalid proxy scheme '{scheme}', must be 'http' or 'https'"
                )))
            }
        })?;
        if let Some((username, password)) = auth {
            proxy = proxy.basic_auth(&username, &password);
        }
        if let Some(headers) = headers {
            let headers = Headers::coerce(py, &headers)?;
            let mut header_map = HeaderMap::new();
            headers.get().append_to(py, &mut header_map)?;
            proxy = proxy.headers(header_map);
        }
        if let Some(no_proxy) = no_proxy {
            proxy = proxy.no_proxy(reqwest::NoProxy::from_string(no_proxy));
        }
        Ok(Self {
            inner: proxy,
            url: url.to_string(),
            scheme: scheme.map(str::to_string),
        })
    }

    fn __repr__(&self) -> String {
        match &self.scheme {
            Some(scheme) => format!(
                "Proxy(url=\"{}\", scheme=\"{}\")",
                mask_url(&self.url),
                scheme
            ),
            None => format!("Proxy(url=\"{}\")", mask_url(&self.url)),
        }
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
