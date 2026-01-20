use http::{header, HeaderName};
use pyo3::{
    exceptions::PyValueError,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyString, PyStringMethods as _},
    Bound, Py, PyAny, PyResult, Python,
};

#[pyclass(module = "pyqwest", name = "HTTPHeaderName", frozen)]
pub(crate) struct HttpHeaderName {
    py: Py<PyString>,
    rs: HeaderName,
}

#[pymethods]
impl HttpHeaderName {
    /// Creates a new `HTTPHeaderName` from a string. Prefer one of the class
    /// attributes when available.
    #[new]
    fn py_new(name: Bound<'_, PyString>) -> PyResult<Self> {
        let name_str = name.to_str()?;
        let header_name = HeaderName::from_bytes(name_str.as_bytes())
            .map_err(|_| PyValueError::new_err("Invalid header name"))?;
        Ok(Self {
            py: name.unbind(),
            rs: header_name,
        })
    }

    /// The "accept" header.
    #[pyo3(name = "ACCEPT")]
    #[classattr]
    fn accept(py: Python<'_>) -> Self {
        Self::new(py, header::ACCEPT)
    }

    /// The "accept-charset" header.
    #[pyo3(name = "ACCEPT_CHARSET")]
    #[classattr]
    fn accept_charset(py: Python<'_>) -> Self {
        Self::new(py, header::ACCEPT_CHARSET)
    }

    /// The "accept-encoding" header.
    #[pyo3(name = "ACCEPT_ENCODING")]
    #[classattr]
    fn accept_encoding(py: Python<'_>) -> Self {
        Self::new(py, header::ACCEPT_ENCODING)
    }

    /// The "accept-language" header.
    #[pyo3(name = "ACCEPT_LANGUAGE")]
    #[classattr]
    fn accept_language(py: Python<'_>) -> Self {
        Self::new(py, header::ACCEPT_LANGUAGE)
    }

    /// The "accept-ranges" header.
    #[pyo3(name = "ACCEPT_RANGES")]
    #[classattr]
    fn accept_ranges(py: Python<'_>) -> Self {
        Self::new(py, header::ACCEPT_RANGES)
    }

    /// The "access-control-allow-credentials" header.
    #[pyo3(name = "ACCESS_CONTROL_ALLOW_CREDENTIALS")]
    #[classattr]
    fn access_control_allow_credentials(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_ALLOW_CREDENTIALS)
    }

    /// The "access-control-allow-headers" header.
    #[pyo3(name = "ACCESS_CONTROL_ALLOW_HEADERS")]
    #[classattr]
    fn access_control_allow_headers(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_ALLOW_HEADERS)
    }

    /// The "access-control-allow-methods" header.
    #[pyo3(name = "ACCESS_CONTROL_ALLOW_METHODS")]
    #[classattr]
    fn access_control_allow_methods(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_ALLOW_METHODS)
    }

    /// The "access-control-allow-origin" header.
    #[pyo3(name = "ACCESS_CONTROL_ALLOW_ORIGIN")]
    #[classattr]
    fn access_control_allow_origin(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_ALLOW_ORIGIN)
    }

    /// The "access-control-expose-headers" header.
    #[pyo3(name = "ACCESS_CONTROL_EXPOSE_HEADERS")]
    #[classattr]
    fn access_control_expose_headers(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_EXPOSE_HEADERS)
    }

    /// The "access-control-max-age" header.
    #[pyo3(name = "ACCESS_CONTROL_MAX_AGE")]
    #[classattr]
    fn access_control_max_age(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_MAX_AGE)
    }

    /// The "access-control-request-headers" header.
    #[pyo3(name = "ACCESS_CONTROL_REQUEST_HEADERS")]
    #[classattr]
    fn access_control_request_headers(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_REQUEST_HEADERS)
    }

    /// The "access-control-request-method" header.
    #[pyo3(name = "ACCESS_CONTROL_REQUEST_METHOD")]
    #[classattr]
    fn access_control_request_method(py: Python<'_>) -> Self {
        Self::new(py, header::ACCESS_CONTROL_REQUEST_METHOD)
    }

    /// The "age" header.
    #[pyo3(name = "AGE")]
    #[classattr]
    fn age(py: Python<'_>) -> Self {
        Self::new(py, header::AGE)
    }

    /// The "allow" header.
    #[pyo3(name = "ALLOW")]
    #[classattr]
    fn allow(py: Python<'_>) -> Self {
        Self::new(py, header::ALLOW)
    }

    /// The "alt-svc" header.
    #[pyo3(name = "ALT_SVC")]
    #[classattr]
    fn alt_svc(py: Python<'_>) -> Self {
        Self::new(py, header::ALT_SVC)
    }

    /// The "authorization" header.
    #[pyo3(name = "AUTHORIZATION")]
    #[classattr]
    fn authorization(py: Python<'_>) -> Self {
        Self::new(py, header::AUTHORIZATION)
    }

    /// The "cache-control" header.
    #[pyo3(name = "CACHE_CONTROL")]
    #[classattr]
    fn cache_control(py: Python<'_>) -> Self {
        Self::new(py, header::CACHE_CONTROL)
    }

    /// The "cache-status" header.
    #[pyo3(name = "CACHE_STATUS")]
    #[classattr]
    fn cache_status(py: Python<'_>) -> Self {
        Self::new(py, header::CACHE_STATUS)
    }

    /// The "cdn-cache-control" header.
    #[pyo3(name = "CDN_CACHE_CONTROL")]
    #[classattr]
    fn cdn_cache_control(py: Python<'_>) -> Self {
        Self::new(py, header::CDN_CACHE_CONTROL)
    }

    /// The "connection" header.
    #[pyo3(name = "CONNECTION")]
    #[classattr]
    fn connection(py: Python<'_>) -> Self {
        Self::new(py, header::CONNECTION)
    }

    /// The "content-disposition" header.
    #[pyo3(name = "CONTENT_DISPOSITION")]
    #[classattr]
    fn content_disposition(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_DISPOSITION)
    }

    /// The "content-encoding" header.
    #[pyo3(name = "CONTENT_ENCODING")]
    #[classattr]
    fn content_encoding(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_ENCODING)
    }

    /// The "content-language" header.
    #[pyo3(name = "CONTENT_LANGUAGE")]
    #[classattr]
    fn content_language(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_LANGUAGE)
    }

    /// The "content-length" header.
    #[pyo3(name = "CONTENT_LENGTH")]
    #[classattr]
    fn content_length(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_LENGTH)
    }

    /// The "content-location" header.
    #[pyo3(name = "CONTENT_LOCATION")]
    #[classattr]
    fn content_location(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_LOCATION)
    }

    /// The "content-range" header.
    #[pyo3(name = "CONTENT_RANGE")]
    #[classattr]
    fn content_range(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_RANGE)
    }

    /// The "content-security-policy" header.
    #[pyo3(name = "CONTENT_SECURITY_POLICY")]
    #[classattr]
    fn content_security_policy(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_SECURITY_POLICY)
    }

    /// The "content-security-policy-report-only" header.
    #[pyo3(name = "CONTENT_SECURITY_POLICY_REPORT_ONLY")]
    #[classattr]
    fn content_security_policy_report_only(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_SECURITY_POLICY_REPORT_ONLY)
    }

    /// The "content-type" header.
    #[pyo3(name = "CONTENT_TYPE")]
    #[classattr]
    fn content_type(py: Python<'_>) -> Self {
        Self::new(py, header::CONTENT_TYPE)
    }

    /// The "cookie" header.
    #[pyo3(name = "COOKIE")]
    #[classattr]
    fn cookie(py: Python<'_>) -> Self {
        Self::new(py, header::COOKIE)
    }

    /// The "dnt" header.
    #[pyo3(name = "DNT")]
    #[classattr]
    fn dnt(py: Python<'_>) -> Self {
        Self::new(py, header::DNT)
    }

    /// The "date" header.
    #[pyo3(name = "DATE")]
    #[classattr]
    fn date(py: Python<'_>) -> Self {
        Self::new(py, header::DATE)
    }

    /// The "etag" header.
    #[pyo3(name = "ETAG")]
    #[classattr]
    fn etag(py: Python<'_>) -> Self {
        Self::new(py, header::ETAG)
    }

    /// The "expect" header.
    #[pyo3(name = "EXPECT")]
    #[classattr]
    fn expect(py: Python<'_>) -> Self {
        Self::new(py, header::EXPECT)
    }

    /// The "expires" header.
    #[pyo3(name = "EXPIRES")]
    #[classattr]
    fn expires(py: Python<'_>) -> Self {
        Self::new(py, header::EXPIRES)
    }

    /// The "forwarded" header.
    #[pyo3(name = "FORWARDED")]
    #[classattr]
    fn forwarded(py: Python<'_>) -> Self {
        Self::new(py, header::FORWARDED)
    }

    /// The "from" header.
    #[pyo3(name = "FROM")]
    #[classattr]
    fn from(py: Python<'_>) -> Self {
        Self::new(py, header::FROM)
    }

    /// The "host" header.
    #[pyo3(name = "HOST")]
    #[classattr]
    fn host(py: Python<'_>) -> Self {
        Self::new(py, header::HOST)
    }

    /// The "if-match" header.
    #[pyo3(name = "IF_MATCH")]
    #[classattr]
    fn if_match(py: Python<'_>) -> Self {
        Self::new(py, header::IF_MATCH)
    }

    /// The "if-modified-since" header.
    #[pyo3(name = "IF_MODIFIED_SINCE")]
    #[classattr]
    fn if_modified_since(py: Python<'_>) -> Self {
        Self::new(py, header::IF_MODIFIED_SINCE)
    }

    /// The "if-none-match" header.
    #[pyo3(name = "IF_NONE_MATCH")]
    #[classattr]
    fn if_none_match(py: Python<'_>) -> Self {
        Self::new(py, header::IF_NONE_MATCH)
    }

    /// The "if-range" header.
    #[pyo3(name = "IF_RANGE")]
    #[classattr]
    fn if_range(py: Python<'_>) -> Self {
        Self::new(py, header::IF_RANGE)
    }

    /// The "if-unmodified-since" header.
    #[pyo3(name = "IF_UNMODIFIED_SINCE")]
    #[classattr]
    fn if_unmodified_since(py: Python<'_>) -> Self {
        Self::new(py, header::IF_UNMODIFIED_SINCE)
    }

    /// The "last-modified" header.
    #[pyo3(name = "LAST_MODIFIED")]
    #[classattr]
    fn last_modified(py: Python<'_>) -> Self {
        Self::new(py, header::LAST_MODIFIED)
    }

    /// The "link" header.
    #[pyo3(name = "LINK")]
    #[classattr]
    fn link(py: Python<'_>) -> Self {
        Self::new(py, header::LINK)
    }

    /// The "location" header.
    #[pyo3(name = "LOCATION")]
    #[classattr]
    fn location(py: Python<'_>) -> Self {
        Self::new(py, header::LOCATION)
    }

    /// The "max-forwards" header.
    #[pyo3(name = "MAX_FORWARDS")]
    #[classattr]
    fn max_forwards(py: Python<'_>) -> Self {
        Self::new(py, header::MAX_FORWARDS)
    }

    /// The "origin" header.
    #[pyo3(name = "ORIGIN")]
    #[classattr]
    fn origin(py: Python<'_>) -> Self {
        Self::new(py, header::ORIGIN)
    }

    /// The "pragma" header.
    #[pyo3(name = "PRAGMA")]
    #[classattr]
    fn pragma(py: Python<'_>) -> Self {
        Self::new(py, header::PRAGMA)
    }

    /// The "proxy-authenticate" header.
    #[pyo3(name = "PROXY_AUTHENTICATE")]
    #[classattr]
    fn proxy_authenticate(py: Python<'_>) -> Self {
        Self::new(py, header::PROXY_AUTHENTICATE)
    }

    /// The "proxy-authorization" header.
    #[pyo3(name = "PROXY_AUTHORIZATION")]
    #[classattr]
    fn proxy_authorization(py: Python<'_>) -> Self {
        Self::new(py, header::PROXY_AUTHORIZATION)
    }

    /// The "public-key-pins" header.
    #[pyo3(name = "PUBLIC_KEY_PINS")]
    #[classattr]
    fn public_key_pins(py: Python<'_>) -> Self {
        Self::new(py, header::PUBLIC_KEY_PINS)
    }

    /// The "public-key-pins-report-only" header.
    #[pyo3(name = "PUBLIC_KEY_PINS_REPORT_ONLY")]
    #[classattr]
    fn public_key_pins_report_only(py: Python<'_>) -> Self {
        Self::new(py, header::PUBLIC_KEY_PINS_REPORT_ONLY)
    }

    /// The "range" header.
    #[pyo3(name = "RANGE")]
    #[classattr]
    fn range(py: Python<'_>) -> Self {
        Self::new(py, header::RANGE)
    }

    /// The "referer" header.
    #[pyo3(name = "REFERER")]
    #[classattr]
    fn referer(py: Python<'_>) -> Self {
        Self::new(py, header::REFERER)
    }

    /// The "referrer-policy" header.
    #[pyo3(name = "REFERRER_POLICY")]
    #[classattr]
    fn referrer_policy(py: Python<'_>) -> Self {
        Self::new(py, header::REFERRER_POLICY)
    }

    /// The "refresh" header.
    #[pyo3(name = "REFRESH")]
    #[classattr]
    fn refresh(py: Python<'_>) -> Self {
        Self::new(py, header::REFRESH)
    }

    /// The "retry-after" header.
    #[pyo3(name = "RETRY_AFTER")]
    #[classattr]
    fn retry_after(py: Python<'_>) -> Self {
        Self::new(py, header::RETRY_AFTER)
    }

    /// The "sec-websocket-accept" header.
    #[pyo3(name = "SEC_WEBSOCKET_ACCEPT")]
    #[classattr]
    fn sec_websocket_accept(py: Python<'_>) -> Self {
        Self::new(py, header::SEC_WEBSOCKET_ACCEPT)
    }

    /// The "sec-websocket-extensions" header.
    #[pyo3(name = "SEC_WEBSOCKET_EXTENSIONS")]
    #[classattr]
    fn sec_websocket_extensions(py: Python<'_>) -> Self {
        Self::new(py, header::SEC_WEBSOCKET_EXTENSIONS)
    }

    /// The "sec-websocket-key" header.
    #[pyo3(name = "SEC_WEBSOCKET_KEY")]
    #[classattr]
    fn sec_websocket_key(py: Python<'_>) -> Self {
        Self::new(py, header::SEC_WEBSOCKET_KEY)
    }

    /// The "sec-websocket-protocol" header.
    #[pyo3(name = "SEC_WEBSOCKET_PROTOCOL")]
    #[classattr]
    fn sec_websocket_protocol(py: Python<'_>) -> Self {
        Self::new(py, header::SEC_WEBSOCKET_PROTOCOL)
    }

    /// The "sec-websocket-version" header.
    #[pyo3(name = "SEC_WEBSOCKET_VERSION")]
    #[classattr]
    fn sec_websocket_version(py: Python<'_>) -> Self {
        Self::new(py, header::SEC_WEBSOCKET_VERSION)
    }

    /// The "server" header.
    #[pyo3(name = "SERVER")]
    #[classattr]
    fn server(py: Python<'_>) -> Self {
        Self::new(py, header::SERVER)
    }

    /// The "set-cookie" header.
    #[pyo3(name = "SET_COOKIE")]
    #[classattr]
    fn set_cookie(py: Python<'_>) -> Self {
        Self::new(py, header::SET_COOKIE)
    }

    /// The "strict-transport-security" header.
    #[pyo3(name = "STRICT_TRANSPORT_SECURITY")]
    #[classattr]
    fn strict_transport_security(py: Python<'_>) -> Self {
        Self::new(py, header::STRICT_TRANSPORT_SECURITY)
    }

    /// The "te" header.
    #[pyo3(name = "TE")]
    #[classattr]
    fn te(py: Python<'_>) -> Self {
        Self::new(py, header::TE)
    }

    /// The "trailer" header.
    #[pyo3(name = "TRAILER")]
    #[classattr]
    fn trailer(py: Python<'_>) -> Self {
        Self::new(py, header::TRAILER)
    }

    /// The "transfer-encoding" header.
    #[pyo3(name = "TRANSFER_ENCODING")]
    #[classattr]
    fn transfer_encoding(py: Python<'_>) -> Self {
        Self::new(py, header::TRANSFER_ENCODING)
    }

    /// The "user-agent" header.
    #[pyo3(name = "USER_AGENT")]
    #[classattr]
    fn user_agent(py: Python<'_>) -> Self {
        Self::new(py, header::USER_AGENT)
    }

    /// The "upgrade" header.
    #[pyo3(name = "UPGRADE")]
    #[classattr]
    fn upgrade(py: Python<'_>) -> Self {
        Self::new(py, header::UPGRADE)
    }

    /// The "upgrade-insecure-requests" header.
    #[pyo3(name = "UPGRADE_INSECURE_REQUESTS")]
    #[classattr]
    fn upgrade_insecure_requests(py: Python<'_>) -> Self {
        Self::new(py, header::UPGRADE_INSECURE_REQUESTS)
    }

    /// The "vary" header.
    #[pyo3(name = "VARY")]
    #[classattr]
    fn vary(py: Python<'_>) -> Self {
        Self::new(py, header::VARY)
    }

    /// The "via" header.
    #[pyo3(name = "VIA")]
    #[classattr]
    fn via(py: Python<'_>) -> Self {
        Self::new(py, header::VIA)
    }

    /// The "warning" header.
    #[pyo3(name = "WARNING")]
    #[classattr]
    fn warning(py: Python<'_>) -> Self {
        Self::new(py, header::WARNING)
    }

    /// The "www-authenticate" header.
    #[pyo3(name = "WWW_AUTHENTICATE")]
    #[classattr]
    fn www_authenticate(py: Python<'_>) -> Self {
        Self::new(py, header::WWW_AUTHENTICATE)
    }

    /// The "x-content-type-options" header.
    #[pyo3(name = "X_CONTENT_TYPE_OPTIONS")]
    #[classattr]
    fn x_content_type_options(py: Python<'_>) -> Self {
        Self::new(py, header::X_CONTENT_TYPE_OPTIONS)
    }

    /// The "x-dns-prefetch-control" header.
    #[pyo3(name = "X_DNS_PREFETCH_CONTROL")]
    #[classattr]
    fn x_dns_prefetch_control(py: Python<'_>) -> Self {
        Self::new(py, header::X_DNS_PREFETCH_CONTROL)
    }

    /// The "x-frame-options" header.
    #[pyo3(name = "X_FRAME_OPTIONS")]
    #[classattr]
    fn x_frame_options(py: Python<'_>) -> Self {
        Self::new(py, header::X_FRAME_OPTIONS)
    }

    /// The "x-xss-protection" header.
    #[pyo3(name = "X_XSS_PROTECTION")]
    #[classattr]
    fn x_xss_protection(py: Python<'_>) -> Self {
        Self::new(py, header::X_XSS_PROTECTION)
    }

    fn __str__(&self, py: Python<'_>) -> Py<PyString> {
        self.py.clone_ref(py)
    }

    fn __hash__(&self, py: Python<'_>) -> PyResult<isize> {
        self.py.bind(py).hash()
    }

    fn __eq__(&self, other: &Bound<'_, PyAny>) -> bool {
        if let Ok(other_header_name) = other.cast::<HttpHeaderName>() {
            self.rs == other_header_name.get().rs
        } else if let Ok(other_str) = other.cast::<PyString>() {
            self.rs.as_str() == other_str.to_str().unwrap_or("")
        } else {
            false
        }
    }

    fn __repr__(&self, py: Python<'_>) -> Py<PyString> {
        let repr = format!("HTTPHeaderName('{}')", self.rs.as_str());
        PyString::new(py, &repr).unbind()
    }
}

impl HttpHeaderName {
    fn new(py: Python<'_>, name: HeaderName) -> Self {
        let name_str = name.as_str();
        Self {
            py: PyString::new(py, name_str).unbind(),
            rs: name,
        }
    }

    pub(crate) fn as_py(&self, py: Python<'_>) -> Py<PyString> {
        self.py.clone_ref(py)
    }

    pub(crate) fn as_rust(&self) -> &HeaderName {
        &self.rs
    }
}
