use std::{ops::Deref, sync::Arc};

use http::{header, HeaderName, StatusCode};
use pyo3::{
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyBytes, PyInt, PyString},
    Py, PyAny, PyResult, PyTypeInfo, Python,
};

use crate::common::{headername::HttpHeaderName, httpversion::HTTPVersion};

/// Constants used when creating Python objects. These are mostly strings,
/// which `PyO3` provides the intern! macro for, but it still has a very small amount
/// of overhead per access, but more importantly forces lazy initialization during
/// request processing. It's not too hard for us to memoize these at client init so
/// we go ahead and do it. Then, usage is just simple ref-counting.
pub(crate) struct ConstantsInner {
    /// An empty bytes object.
    pub empty_bytes: Py<PyBytes>,

    /// The string "__aiter__".
    pub __aiter__: Py<PyString>,
    /// The string "aclose".
    pub aclose: Py<PyString>,
    /// The string "`add_done_callback`".
    pub add_done_callback: Py<PyString>,
    /// The string "cancel".
    pub cancel: Py<PyString>,
    /// The string "close".
    pub close: Py<PyString>,
    /// The string "`create_task`".
    pub create_task: Py<PyString>,
    /// The string "exception".
    pub exception: Py<PyString>,
    /// The string "execute".
    pub execute: Py<PyString>,
    /// The string "`execute_sync`".
    pub execute_sync: Py<PyString>,

    /// The _glue.py function `execute_and_read_full`.
    pub execute_and_read_full: Py<PyAny>,
    /// The _glue.py function `forward`.
    pub forward: Py<PyAny>,
    /// The _glue.py function `read_content_sync`.
    pub read_content_sync: Py<PyAny>,

    /// The stdlib function `json.loads`.
    pub json_loads: Py<PyAny>,

    // HTTP Versions
    /// HTTPVersion.HTTP1
    pub http_1: Py<HTTPVersion>,
    /// HTTPVersion.HTTP2
    pub http_2: Py<HTTPVersion>,
    /// HTTPVersion.HTTP3
    pub http_3: Py<HTTPVersion>,

    // HTTP method strings
    /// The string "DELETE".
    pub delete: Py<PyString>,
    /// The string "GET".
    pub get: Py<PyString>,
    /// The string "HEAD".
    pub head: Py<PyString>,
    /// The string "OPTIONS".
    pub options: Py<PyString>,
    /// The string "PATCH".
    pub patch: Py<PyString>,
    /// The string "POST".
    pub post: Py<PyString>,
    /// The string "PUT".
    pub put: Py<PyString>,
    /// The string "TRACE".
    pub trace: Py<PyString>,

    /// ContextVar.get to get request timeout.
    pub timeout_context_var_get: Py<PyAny>,
    /// ContextVar.set to store request timeout.
    pub timeout_context_var_set: Py<PyAny>,
    /// ContextVar.reset to reset request timeout.
    pub timeout_context_var_reset: Py<PyAny>,

    // HTTP numeric status codes. We only cache non-informational ones
    // since they have no protocol implications.
    /// The code OK.
    status_ok: Py<PyInt>,
    /// The code Created.
    status_created: Py<PyInt>,
    /// The code Accepted.
    status_accepted: Py<PyInt>,
    /// The code Non Authoritative Information.
    status_non_authoritative_information: Py<PyInt>,
    /// The code No Content.
    status_no_content: Py<PyInt>,
    /// The code Reset Content.
    status_reset_content: Py<PyInt>,
    /// The code Partial Content.
    status_partial_content: Py<PyInt>,
    /// The code Multi-Status.
    status_multi_status: Py<PyInt>,
    /// The code Already Reported.
    status_already_reported: Py<PyInt>,
    /// The code IM Used.
    status_im_used: Py<PyInt>,
    /// The code Multiple Choices.
    status_multiple_choices: Py<PyInt>,
    /// The code Moved Permanently.
    status_moved_permanently: Py<PyInt>,
    /// The code Found.
    status_found: Py<PyInt>,
    /// The code See Other.
    status_see_other: Py<PyInt>,
    /// The code Not Modified.
    status_not_modified: Py<PyInt>,
    /// The code Use Proxy.
    status_use_proxy: Py<PyInt>,
    /// The code Temporary Redirect.
    status_temporary_redirect: Py<PyInt>,
    /// The code Permanent Redirect.
    status_permanent_redirect: Py<PyInt>,
    /// The code Bad Request.
    status_bad_request: Py<PyInt>,
    /// The code Unauthorized.
    status_unauthorized: Py<PyInt>,
    /// The code Payment Required.
    status_payment_required: Py<PyInt>,
    /// The code Forbidden.
    status_forbidden: Py<PyInt>,
    /// The code Not Found.
    status_not_found: Py<PyInt>,
    /// The code Method Not Allowed.
    status_method_not_allowed: Py<PyInt>,
    /// The code Not Acceptable.
    status_not_acceptable: Py<PyInt>,
    /// The code Proxy Authentication Required.
    status_proxy_authentication_required: Py<PyInt>,
    /// The code Request Timeout.
    status_request_timeout: Py<PyInt>,
    /// The code Conflict.
    status_conflict: Py<PyInt>,
    /// The code Gone.
    status_gone: Py<PyInt>,
    /// The code Length Required.
    status_length_required: Py<PyInt>,
    /// The code Precondition Failed.
    status_precondition_failed: Py<PyInt>,
    /// The code Payload Too Large.
    status_payload_too_large: Py<PyInt>,
    /// The code URI Too Long.
    status_uri_too_long: Py<PyInt>,
    /// The code Unsupported Media Type.
    status_unsupported_media_type: Py<PyInt>,
    /// The code Range Not Satisfiable.
    status_range_not_satisfiable: Py<PyInt>,
    /// The code Expectation Failed.
    status_expectation_failed: Py<PyInt>,
    /// The code I'm a teapot.
    status_im_a_teapot: Py<PyInt>,
    /// The code Misdirected Request.
    status_misdirected_request: Py<PyInt>,
    /// The code Unprocessable Entity.
    status_unprocessable_entity: Py<PyInt>,
    /// The code Locked.
    status_locked: Py<PyInt>,
    /// The code Failed Dependency.
    status_failed_dependency: Py<PyInt>,
    /// The code Too Early.
    status_too_early: Py<PyInt>,
    /// The code Upgrade Required.
    status_upgrade_required: Py<PyInt>,
    /// The code Precondition Required.
    status_precondition_required: Py<PyInt>,
    /// The code Too Many Requests.
    status_too_many_requests: Py<PyInt>,
    /// The code Request Header Fields Too Large.
    status_request_header_fields_too_large: Py<PyInt>,
    /// The code Unavailable For Legal Reasons.
    status_unavailable_for_legal_reasons: Py<PyInt>,
    /// The code Internal Server Error.
    status_internal_server_error: Py<PyInt>,
    /// The code Not Implemented.
    status_not_implemented: Py<PyInt>,
    /// The code Bad Gateway.
    status_bad_gateway: Py<PyInt>,
    /// The code Service Unavailable.
    status_service_unavailable: Py<PyInt>,
    /// The code Gateway Timeout.
    status_gateway_timeout: Py<PyInt>,
    /// The code HTTP Version Not Supported.
    status_http_version_not_supported: Py<PyInt>,
    /// The code Variant Also Negotiates.
    status_variant_also_negotiates: Py<PyInt>,
    /// The code Insufficient Storage.
    status_insufficient_storage: Py<PyInt>,
    /// The code Loop Detected.
    status_loop_detected: Py<PyInt>,
    /// The code Not Extended.
    status_not_extended: Py<PyInt>,
    /// The code Network Authentication Required.
    status_network_authentication_required: Py<PyInt>,

    /// The 'accept' header.
    header_accept: Py<HttpHeaderName>,
    /// The 'accept-charset' header.
    header_accept_charset: Py<HttpHeaderName>,
    /// The 'accept-encoding' header.
    header_accept_encoding: Py<HttpHeaderName>,
    /// The 'accept-language' header.
    header_accept_language: Py<HttpHeaderName>,
    /// The 'accept-ranges' header.
    header_accept_ranges: Py<HttpHeaderName>,
    /// The 'access-control-allow-credentials' header.
    header_access_control_allow_credentials: Py<HttpHeaderName>,
    /// The 'access-control-allow-headers' header.
    header_access_control_allow_headers: Py<HttpHeaderName>,
    /// The 'access-control-allow-methods' header.
    header_access_control_allow_methods: Py<HttpHeaderName>,
    /// The 'access-control-allow-origin' header.
    header_access_control_allow_origin: Py<HttpHeaderName>,
    /// The 'access-control-expose-headers' header.
    header_access_control_expose_headers: Py<HttpHeaderName>,
    /// The 'access-control-max-age' header.
    header_access_control_max_age: Py<HttpHeaderName>,
    /// The 'access-control-request-headers' header.
    header_access_control_request_headers: Py<HttpHeaderName>,
    /// The 'access-control-request-method' header.
    header_access_control_request_method: Py<HttpHeaderName>,
    /// The 'age' header.
    header_age: Py<HttpHeaderName>,
    /// The 'allow' header.
    header_allow: Py<HttpHeaderName>,
    /// The 'alt-svc' header.
    header_alt_svc: Py<HttpHeaderName>,
    /// The 'authorization' header.
    header_authorization: Py<HttpHeaderName>,
    /// The 'cache-control' header.
    header_cache_control: Py<HttpHeaderName>,
    /// The 'cache-status' header.
    header_cache_status: Py<HttpHeaderName>,
    /// The 'cdn-cache-control' header.
    header_cdn_cache_control: Py<HttpHeaderName>,
    /// The 'connection' header.
    header_connection: Py<HttpHeaderName>,
    /// The 'content-disposition' header.
    header_content_disposition: Py<HttpHeaderName>,
    /// The 'content-encoding' header.
    header_content_encoding: Py<HttpHeaderName>,
    /// The 'content-language' header.
    header_content_language: Py<HttpHeaderName>,
    /// The 'content-length' header.
    header_content_length: Py<HttpHeaderName>,
    /// The 'content-location' header.
    header_content_location: Py<HttpHeaderName>,
    /// The 'content-range' header.
    header_content_range: Py<HttpHeaderName>,
    /// The 'content-security-policy' header.
    header_content_security_policy: Py<HttpHeaderName>,
    /// The 'content-security-policy-report-only' header.
    header_content_security_policy_report_only: Py<HttpHeaderName>,
    /// The 'content-type' header.
    header_content_type: Py<HttpHeaderName>,
    /// The 'cookie' header.
    header_cookie: Py<HttpHeaderName>,
    /// The 'dnt' header.
    header_dnt: Py<HttpHeaderName>,
    /// The 'date' header.
    header_date: Py<HttpHeaderName>,
    /// The 'etag' header.
    header_etag: Py<HttpHeaderName>,
    /// The 'expect' header.
    header_expect: Py<HttpHeaderName>,
    /// The 'expires' header.
    header_expires: Py<HttpHeaderName>,
    /// The 'forwarded' header.
    header_forwarded: Py<HttpHeaderName>,
    /// The 'from' header.
    header_from: Py<HttpHeaderName>,
    /// The 'host' header.
    header_host: Py<HttpHeaderName>,
    /// The 'if-match' header.
    header_if_match: Py<HttpHeaderName>,
    /// The 'if-modified-since' header.
    header_if_modified_since: Py<HttpHeaderName>,
    /// The 'if-none-match' header.
    header_if_none_match: Py<HttpHeaderName>,
    /// The 'if-range' header.
    header_if_range: Py<HttpHeaderName>,
    /// The 'if-unmodified-since' header.
    header_if_unmodified_since: Py<HttpHeaderName>,
    /// The 'last-modified' header.
    header_last_modified: Py<HttpHeaderName>,
    /// The 'link' header.
    header_link: Py<HttpHeaderName>,
    /// The 'location' header.
    header_location: Py<HttpHeaderName>,
    /// The 'max-forwards' header.
    header_max_forwards: Py<HttpHeaderName>,
    /// The 'origin' header.
    header_origin: Py<HttpHeaderName>,
    /// The 'pragma' header.
    header_pragma: Py<HttpHeaderName>,
    /// The 'proxy-authenticate' header.
    header_proxy_authenticate: Py<HttpHeaderName>,
    /// The 'proxy-authorization' header.
    header_proxy_authorization: Py<HttpHeaderName>,
    /// The 'public-key-pins' header.
    header_public_key_pins: Py<HttpHeaderName>,
    /// The 'public-key-pins-report-only' header.
    header_public_key_pins_report_only: Py<HttpHeaderName>,
    /// The 'range' header.
    header_range: Py<HttpHeaderName>,
    /// The 'referer' header.
    header_referer: Py<HttpHeaderName>,
    /// The 'referrer-policy' header.
    header_referrer_policy: Py<HttpHeaderName>,
    /// The 'refresh' header.
    header_refresh: Py<HttpHeaderName>,
    /// The 'retry-after' header.
    header_retry_after: Py<HttpHeaderName>,
    /// The 'sec-websocket-accept' header.
    header_sec_websocket_accept: Py<HttpHeaderName>,
    /// The 'sec-websocket-extensions' header.
    header_sec_websocket_extensions: Py<HttpHeaderName>,
    /// The 'sec-websocket-key' header.
    header_sec_websocket_key: Py<HttpHeaderName>,
    /// The 'sec-websocket-protocol' header.
    header_sec_websocket_protocol: Py<HttpHeaderName>,
    /// The 'sec-websocket-version' header.
    header_sec_websocket_version: Py<HttpHeaderName>,
    /// The 'server' header.
    header_server: Py<HttpHeaderName>,
    /// The 'set-cookie' header.
    header_set_cookie: Py<HttpHeaderName>,
    /// The 'strict-transport-security' header.
    header_strict_transport_security: Py<HttpHeaderName>,
    /// The 'te' header.
    header_te: Py<HttpHeaderName>,
    /// The 'trailer' header.
    header_trailer: Py<HttpHeaderName>,
    /// The 'transfer-encoding' header.
    header_transfer_encoding: Py<HttpHeaderName>,
    /// The 'user-agent' header.
    header_user_agent: Py<HttpHeaderName>,
    /// The 'upgrade' header.
    header_upgrade: Py<HttpHeaderName>,
    /// The 'upgrade-insecure-requests' header.
    header_upgrade_insecure_requests: Py<HttpHeaderName>,
    /// The 'vary' header.
    header_vary: Py<HttpHeaderName>,
    /// The 'via' header.
    header_via: Py<HttpHeaderName>,
    /// The 'warning' header.
    header_warning: Py<HttpHeaderName>,
    /// The 'www-authenticate' header.
    header_www_authenticate: Py<HttpHeaderName>,
    /// The 'x-content-type-options' header.
    header_x_content_type_options: Py<HttpHeaderName>,
    /// The 'x-dns-prefetch-control' header.
    header_x_dns_prefetch_control: Py<HttpHeaderName>,
    /// The 'x-frame-options' header.
    header_x_frame_options: Py<HttpHeaderName>,
    /// The 'x-xss-protection' header.
    header_x_xss_protection: Py<HttpHeaderName>,
}

static INSTANCE: PyOnceLock<Constants> = PyOnceLock::new();

#[derive(Clone)]
pub(crate) struct Constants {
    inner: Arc<ConstantsInner>,
}

impl Constants {
    pub(crate) fn get(py: Python<'_>) -> PyResult<Self> {
        Ok(INSTANCE.get_or_try_init(py, || Self::new(py))?.clone())
    }

    #[allow(clippy::too_many_lines)]
    fn new(py: Python<'_>) -> PyResult<Self> {
        let glue = py.import("pyqwest._glue")?;
        let contextvars = py.import("contextvars")?;
        let timeout_context_var = contextvars
            .getattr("ContextVar")?
            .call1(("pyqwest_timeout",))?;
        Ok(Self {
            inner: Arc::new(ConstantsInner {
                empty_bytes: PyBytes::new(py, b"").unbind(),
                __aiter__: PyString::new(py, "__aiter__").unbind(),
                aclose: PyString::new(py, "aclose").unbind(),
                add_done_callback: PyString::new(py, "add_done_callback").unbind(),
                cancel: PyString::new(py, "cancel").unbind(),
                close: PyString::new(py, "close").unbind(),
                create_task: PyString::new(py, "create_task").unbind(),
                exception: PyString::new(py, "exception").unbind(),
                execute: PyString::new(py, "execute").unbind(),
                execute_sync: PyString::new(py, "execute_sync").unbind(),

                execute_and_read_full: glue.getattr("execute_and_read_full")?.unbind(),
                forward: glue.getattr("forward")?.unbind(),
                read_content_sync: glue.getattr("read_content_sync")?.unbind(),

                json_loads: py.import("json")?.getattr("loads")?.unbind(),

                timeout_context_var_get: timeout_context_var.getattr("get")?.unbind(),
                timeout_context_var_set: timeout_context_var.getattr("set")?.unbind(),
                timeout_context_var_reset: timeout_context_var.getattr("reset")?.unbind(),

                http_1: get_class_attr::<HTTPVersion>(py, "HTTP1")?,
                http_2: get_class_attr::<HTTPVersion>(py, "HTTP2")?,
                http_3: get_class_attr::<HTTPVersion>(py, "HTTP3")?,

                delete: PyString::new(py, "DELETE").unbind(),
                get: PyString::new(py, "GET").unbind(),
                head: PyString::new(py, "HEAD").unbind(),
                options: PyString::new(py, "OPTIONS").unbind(),
                patch: PyString::new(py, "PATCH").unbind(),
                post: PyString::new(py, "POST").unbind(),
                put: PyString::new(py, "PUT").unbind(),
                trace: PyString::new(py, "TRACE").unbind(),

                status_ok: PyInt::new(py, StatusCode::OK.as_u16()).unbind(),
                status_created: PyInt::new(py, StatusCode::CREATED.as_u16()).unbind(),
                status_accepted: PyInt::new(py, StatusCode::ACCEPTED.as_u16()).unbind(),
                status_non_authoritative_information: PyInt::new(
                    py,
                    StatusCode::NON_AUTHORITATIVE_INFORMATION.as_u16(),
                )
                .unbind(),
                status_no_content: PyInt::new(py, StatusCode::NO_CONTENT.as_u16()).unbind(),
                status_reset_content: PyInt::new(py, StatusCode::RESET_CONTENT.as_u16()).unbind(),
                status_partial_content: PyInt::new(py, StatusCode::PARTIAL_CONTENT.as_u16())
                    .unbind(),
                status_multi_status: PyInt::new(py, StatusCode::MULTI_STATUS.as_u16()).unbind(),
                status_already_reported: PyInt::new(py, StatusCode::ALREADY_REPORTED.as_u16())
                    .unbind(),
                status_im_used: PyInt::new(py, StatusCode::IM_USED.as_u16()).unbind(),
                status_multiple_choices: PyInt::new(py, StatusCode::MULTIPLE_CHOICES.as_u16())
                    .unbind(),
                status_moved_permanently: PyInt::new(py, StatusCode::MOVED_PERMANENTLY.as_u16())
                    .unbind(),
                status_found: PyInt::new(py, StatusCode::FOUND.as_u16()).unbind(),
                status_see_other: PyInt::new(py, StatusCode::SEE_OTHER.as_u16()).unbind(),
                status_not_modified: PyInt::new(py, StatusCode::NOT_MODIFIED.as_u16()).unbind(),
                status_use_proxy: PyInt::new(py, StatusCode::USE_PROXY.as_u16()).unbind(),
                status_temporary_redirect: PyInt::new(py, StatusCode::TEMPORARY_REDIRECT.as_u16())
                    .unbind(),
                status_permanent_redirect: PyInt::new(py, StatusCode::PERMANENT_REDIRECT.as_u16())
                    .unbind(),
                status_bad_request: PyInt::new(py, StatusCode::BAD_REQUEST.as_u16()).unbind(),
                status_unauthorized: PyInt::new(py, StatusCode::UNAUTHORIZED.as_u16()).unbind(),
                status_payment_required: PyInt::new(py, StatusCode::PAYMENT_REQUIRED.as_u16())
                    .unbind(),
                status_forbidden: PyInt::new(py, StatusCode::FORBIDDEN.as_u16()).unbind(),
                status_not_found: PyInt::new(py, StatusCode::NOT_FOUND.as_u16()).unbind(),
                status_method_not_allowed: PyInt::new(py, StatusCode::METHOD_NOT_ALLOWED.as_u16())
                    .unbind(),
                status_not_acceptable: PyInt::new(py, StatusCode::NOT_ACCEPTABLE.as_u16()).unbind(),
                status_proxy_authentication_required: PyInt::new(
                    py,
                    StatusCode::PROXY_AUTHENTICATION_REQUIRED.as_u16(),
                )
                .unbind(),
                status_request_timeout: PyInt::new(py, StatusCode::REQUEST_TIMEOUT.as_u16())
                    .unbind(),
                status_conflict: PyInt::new(py, StatusCode::CONFLICT.as_u16()).unbind(),
                status_gone: PyInt::new(py, StatusCode::GONE.as_u16()).unbind(),
                status_length_required: PyInt::new(py, StatusCode::LENGTH_REQUIRED.as_u16())
                    .unbind(),
                status_precondition_failed: PyInt::new(
                    py,
                    StatusCode::PRECONDITION_FAILED.as_u16(),
                )
                .unbind(),
                status_payload_too_large: PyInt::new(py, StatusCode::PAYLOAD_TOO_LARGE.as_u16())
                    .unbind(),
                status_uri_too_long: PyInt::new(py, StatusCode::URI_TOO_LONG.as_u16()).unbind(),
                status_unsupported_media_type: PyInt::new(
                    py,
                    StatusCode::UNSUPPORTED_MEDIA_TYPE.as_u16(),
                )
                .unbind(),
                status_range_not_satisfiable: PyInt::new(
                    py,
                    StatusCode::RANGE_NOT_SATISFIABLE.as_u16(),
                )
                .unbind(),
                status_expectation_failed: PyInt::new(py, StatusCode::EXPECTATION_FAILED.as_u16())
                    .unbind(),
                status_im_a_teapot: PyInt::new(py, StatusCode::IM_A_TEAPOT.as_u16()).unbind(),
                status_misdirected_request: PyInt::new(
                    py,
                    StatusCode::MISDIRECTED_REQUEST.as_u16(),
                )
                .unbind(),
                status_unprocessable_entity: PyInt::new(
                    py,
                    StatusCode::UNPROCESSABLE_ENTITY.as_u16(),
                )
                .unbind(),
                status_locked: PyInt::new(py, StatusCode::LOCKED.as_u16()).unbind(),
                status_failed_dependency: PyInt::new(py, StatusCode::FAILED_DEPENDENCY.as_u16())
                    .unbind(),
                status_too_early: PyInt::new(py, StatusCode::TOO_EARLY.as_u16()).unbind(),
                status_upgrade_required: PyInt::new(py, StatusCode::UPGRADE_REQUIRED.as_u16())
                    .unbind(),
                status_precondition_required: PyInt::new(
                    py,
                    StatusCode::PRECONDITION_REQUIRED.as_u16(),
                )
                .unbind(),
                status_too_many_requests: PyInt::new(py, StatusCode::TOO_MANY_REQUESTS.as_u16())
                    .unbind(),
                status_request_header_fields_too_large: PyInt::new(
                    py,
                    StatusCode::REQUEST_HEADER_FIELDS_TOO_LARGE.as_u16(),
                )
                .unbind(),
                status_unavailable_for_legal_reasons: PyInt::new(
                    py,
                    StatusCode::UNAVAILABLE_FOR_LEGAL_REASONS.as_u16(),
                )
                .unbind(),
                status_internal_server_error: PyInt::new(
                    py,
                    StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
                )
                .unbind(),
                status_not_implemented: PyInt::new(py, StatusCode::NOT_IMPLEMENTED.as_u16())
                    .unbind(),
                status_bad_gateway: PyInt::new(py, StatusCode::BAD_GATEWAY.as_u16()).unbind(),
                status_service_unavailable: PyInt::new(
                    py,
                    StatusCode::SERVICE_UNAVAILABLE.as_u16(),
                )
                .unbind(),
                status_gateway_timeout: PyInt::new(py, StatusCode::GATEWAY_TIMEOUT.as_u16())
                    .unbind(),
                status_http_version_not_supported: PyInt::new(
                    py,
                    StatusCode::HTTP_VERSION_NOT_SUPPORTED.as_u16(),
                )
                .unbind(),
                status_variant_also_negotiates: PyInt::new(
                    py,
                    StatusCode::VARIANT_ALSO_NEGOTIATES.as_u16(),
                )
                .unbind(),
                status_insufficient_storage: PyInt::new(
                    py,
                    StatusCode::INSUFFICIENT_STORAGE.as_u16(),
                )
                .unbind(),
                status_loop_detected: PyInt::new(py, StatusCode::LOOP_DETECTED.as_u16()).unbind(),
                status_not_extended: PyInt::new(py, StatusCode::NOT_EXTENDED.as_u16()).unbind(),
                status_network_authentication_required: PyInt::new(
                    py,
                    StatusCode::NETWORK_AUTHENTICATION_REQUIRED.as_u16(),
                )
                .unbind(),

                header_accept: get_class_attr::<HttpHeaderName>(py, "ACCEPT")?,
                header_accept_charset: get_class_attr::<HttpHeaderName>(py, "ACCEPT_CHARSET")?,
                header_accept_encoding: get_class_attr::<HttpHeaderName>(py, "ACCEPT_ENCODING")?,
                header_accept_language: get_class_attr::<HttpHeaderName>(py, "ACCEPT_LANGUAGE")?,
                header_accept_ranges: get_class_attr::<HttpHeaderName>(py, "ACCEPT_RANGES")?,
                header_access_control_allow_credentials: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_ALLOW_CREDENTIALS",
                )?,
                header_access_control_allow_headers: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_ALLOW_HEADERS",
                )?,
                header_access_control_allow_methods: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_ALLOW_METHODS",
                )?,
                header_access_control_allow_origin: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_ALLOW_ORIGIN",
                )?,
                header_access_control_expose_headers: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_EXPOSE_HEADERS",
                )?,
                header_access_control_max_age: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_MAX_AGE",
                )?,
                header_access_control_request_headers: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_REQUEST_HEADERS",
                )?,
                header_access_control_request_method: get_class_attr::<HttpHeaderName>(
                    py,
                    "ACCESS_CONTROL_REQUEST_METHOD",
                )?,
                header_age: get_class_attr::<HttpHeaderName>(py, "AGE")?,
                header_allow: get_class_attr::<HttpHeaderName>(py, "ALLOW")?,
                header_alt_svc: get_class_attr::<HttpHeaderName>(py, "ALT_SVC")?,
                header_authorization: get_class_attr::<HttpHeaderName>(py, "AUTHORIZATION")?,
                header_cache_control: get_class_attr::<HttpHeaderName>(py, "CACHE_CONTROL")?,
                header_cache_status: get_class_attr::<HttpHeaderName>(py, "CACHE_STATUS")?,
                header_cdn_cache_control: get_class_attr::<HttpHeaderName>(
                    py,
                    "CDN_CACHE_CONTROL",
                )?,
                header_connection: get_class_attr::<HttpHeaderName>(py, "CONNECTION")?,
                header_content_disposition: get_class_attr::<HttpHeaderName>(
                    py,
                    "CONTENT_DISPOSITION",
                )?,
                header_content_encoding: get_class_attr::<HttpHeaderName>(py, "CONTENT_ENCODING")?,
                header_content_language: get_class_attr::<HttpHeaderName>(py, "CONTENT_LANGUAGE")?,
                header_content_length: get_class_attr::<HttpHeaderName>(py, "CONTENT_LENGTH")?,
                header_content_location: get_class_attr::<HttpHeaderName>(py, "CONTENT_LOCATION")?,
                header_content_range: get_class_attr::<HttpHeaderName>(py, "CONTENT_RANGE")?,
                header_content_security_policy: get_class_attr::<HttpHeaderName>(
                    py,
                    "CONTENT_SECURITY_POLICY",
                )?,
                header_content_security_policy_report_only: get_class_attr::<HttpHeaderName>(
                    py,
                    "CONTENT_SECURITY_POLICY_REPORT_ONLY",
                )?,
                header_content_type: get_class_attr::<HttpHeaderName>(py, "CONTENT_TYPE")?,
                header_cookie: get_class_attr::<HttpHeaderName>(py, "COOKIE")?,
                header_dnt: get_class_attr::<HttpHeaderName>(py, "DNT")?,
                header_date: get_class_attr::<HttpHeaderName>(py, "DATE")?,
                header_etag: get_class_attr::<HttpHeaderName>(py, "ETAG")?,
                header_expect: get_class_attr::<HttpHeaderName>(py, "EXPECT")?,
                header_expires: get_class_attr::<HttpHeaderName>(py, "EXPIRES")?,
                header_forwarded: get_class_attr::<HttpHeaderName>(py, "FORWARDED")?,
                header_from: get_class_attr::<HttpHeaderName>(py, "FROM")?,
                header_host: get_class_attr::<HttpHeaderName>(py, "HOST")?,
                header_if_match: get_class_attr::<HttpHeaderName>(py, "IF_MATCH")?,
                header_if_modified_since: get_class_attr::<HttpHeaderName>(
                    py,
                    "IF_MODIFIED_SINCE",
                )?,
                header_if_none_match: get_class_attr::<HttpHeaderName>(py, "IF_NONE_MATCH")?,
                header_if_range: get_class_attr::<HttpHeaderName>(py, "IF_RANGE")?,
                header_if_unmodified_since: get_class_attr::<HttpHeaderName>(
                    py,
                    "IF_UNMODIFIED_SINCE",
                )?,
                header_last_modified: get_class_attr::<HttpHeaderName>(py, "LAST_MODIFIED")?,
                header_link: get_class_attr::<HttpHeaderName>(py, "LINK")?,
                header_location: get_class_attr::<HttpHeaderName>(py, "LOCATION")?,
                header_max_forwards: get_class_attr::<HttpHeaderName>(py, "MAX_FORWARDS")?,
                header_origin: get_class_attr::<HttpHeaderName>(py, "ORIGIN")?,
                header_pragma: get_class_attr::<HttpHeaderName>(py, "PRAGMA")?,
                header_proxy_authenticate: get_class_attr::<HttpHeaderName>(
                    py,
                    "PROXY_AUTHENTICATE",
                )?,
                header_proxy_authorization: get_class_attr::<HttpHeaderName>(
                    py,
                    "PROXY_AUTHORIZATION",
                )?,
                header_public_key_pins: get_class_attr::<HttpHeaderName>(py, "PUBLIC_KEY_PINS")?,
                header_public_key_pins_report_only: get_class_attr::<HttpHeaderName>(
                    py,
                    "PUBLIC_KEY_PINS_REPORT_ONLY",
                )?,
                header_range: get_class_attr::<HttpHeaderName>(py, "RANGE")?,
                header_referer: get_class_attr::<HttpHeaderName>(py, "REFERER")?,
                header_referrer_policy: get_class_attr::<HttpHeaderName>(py, "REFERRER_POLICY")?,
                header_refresh: get_class_attr::<HttpHeaderName>(py, "REFRESH")?,
                header_retry_after: get_class_attr::<HttpHeaderName>(py, "RETRY_AFTER")?,
                header_sec_websocket_accept: get_class_attr::<HttpHeaderName>(
                    py,
                    "SEC_WEBSOCKET_ACCEPT",
                )?,
                header_sec_websocket_extensions: get_class_attr::<HttpHeaderName>(
                    py,
                    "SEC_WEBSOCKET_EXTENSIONS",
                )?,
                header_sec_websocket_key: get_class_attr::<HttpHeaderName>(
                    py,
                    "SEC_WEBSOCKET_KEY",
                )?,
                header_sec_websocket_protocol: get_class_attr::<HttpHeaderName>(
                    py,
                    "SEC_WEBSOCKET_PROTOCOL",
                )?,
                header_sec_websocket_version: get_class_attr::<HttpHeaderName>(
                    py,
                    "SEC_WEBSOCKET_VERSION",
                )?,
                header_server: get_class_attr::<HttpHeaderName>(py, "SERVER")?,
                header_set_cookie: get_class_attr::<HttpHeaderName>(py, "SET_COOKIE")?,
                header_strict_transport_security: get_class_attr::<HttpHeaderName>(
                    py,
                    "STRICT_TRANSPORT_SECURITY",
                )?,
                header_te: get_class_attr::<HttpHeaderName>(py, "TE")?,
                header_trailer: get_class_attr::<HttpHeaderName>(py, "TRAILER")?,
                header_transfer_encoding: get_class_attr::<HttpHeaderName>(
                    py,
                    "TRANSFER_ENCODING",
                )?,
                header_user_agent: get_class_attr::<HttpHeaderName>(py, "USER_AGENT")?,
                header_upgrade: get_class_attr::<HttpHeaderName>(py, "UPGRADE")?,
                header_upgrade_insecure_requests: get_class_attr::<HttpHeaderName>(
                    py,
                    "UPGRADE_INSECURE_REQUESTS",
                )?,
                header_vary: get_class_attr::<HttpHeaderName>(py, "VARY")?,
                header_via: get_class_attr::<HttpHeaderName>(py, "VIA")?,
                header_warning: get_class_attr::<HttpHeaderName>(py, "WARNING")?,
                header_www_authenticate: get_class_attr::<HttpHeaderName>(py, "WWW_AUTHENTICATE")?,
                header_x_content_type_options: get_class_attr::<HttpHeaderName>(
                    py,
                    "X_CONTENT_TYPE_OPTIONS",
                )?,
                header_x_dns_prefetch_control: get_class_attr::<HttpHeaderName>(
                    py,
                    "X_DNS_PREFETCH_CONTROL",
                )?,
                header_x_frame_options: get_class_attr::<HttpHeaderName>(py, "X_FRAME_OPTIONS")?,
                header_x_xss_protection: get_class_attr::<HttpHeaderName>(py, "X_XSS_PROTECTION")?,
            }),
        })
    }

    pub(crate) fn status_code(&self, py: Python<'_>, code: StatusCode) -> Py<PyInt> {
        match code {
            StatusCode::OK => self.status_ok.clone_ref(py),
            StatusCode::CREATED => self.status_created.clone_ref(py),
            StatusCode::ACCEPTED => self.status_accepted.clone_ref(py),
            StatusCode::NON_AUTHORITATIVE_INFORMATION => {
                self.status_non_authoritative_information.clone_ref(py)
            }
            StatusCode::NO_CONTENT => self.status_no_content.clone_ref(py),
            StatusCode::RESET_CONTENT => self.status_reset_content.clone_ref(py),
            StatusCode::PARTIAL_CONTENT => self.status_partial_content.clone_ref(py),
            StatusCode::MULTI_STATUS => self.status_multi_status.clone_ref(py),
            StatusCode::ALREADY_REPORTED => self.status_already_reported.clone_ref(py),
            StatusCode::IM_USED => self.status_im_used.clone_ref(py),
            StatusCode::MULTIPLE_CHOICES => self.status_multiple_choices.clone_ref(py),
            StatusCode::MOVED_PERMANENTLY => self.status_moved_permanently.clone_ref(py),
            StatusCode::FOUND => self.status_found.clone_ref(py),
            StatusCode::SEE_OTHER => self.status_see_other.clone_ref(py),
            StatusCode::NOT_MODIFIED => self.status_not_modified.clone_ref(py),
            StatusCode::USE_PROXY => self.status_use_proxy.clone_ref(py),
            StatusCode::TEMPORARY_REDIRECT => self.status_temporary_redirect.clone_ref(py),
            StatusCode::PERMANENT_REDIRECT => self.status_permanent_redirect.clone_ref(py),
            StatusCode::BAD_REQUEST => self.status_bad_request.clone_ref(py),
            StatusCode::UNAUTHORIZED => self.status_unauthorized.clone_ref(py),
            StatusCode::PAYMENT_REQUIRED => self.status_payment_required.clone_ref(py),
            StatusCode::FORBIDDEN => self.status_forbidden.clone_ref(py),
            StatusCode::NOT_FOUND => self.status_not_found.clone_ref(py),
            StatusCode::METHOD_NOT_ALLOWED => self.status_method_not_allowed.clone_ref(py),
            StatusCode::NOT_ACCEPTABLE => self.status_not_acceptable.clone_ref(py),
            StatusCode::PROXY_AUTHENTICATION_REQUIRED => {
                self.status_proxy_authentication_required.clone_ref(py)
            }
            StatusCode::REQUEST_TIMEOUT => self.status_request_timeout.clone_ref(py),
            StatusCode::CONFLICT => self.status_conflict.clone_ref(py),
            StatusCode::GONE => self.status_gone.clone_ref(py),
            StatusCode::LENGTH_REQUIRED => self.status_length_required.clone_ref(py),
            StatusCode::PRECONDITION_FAILED => self.status_precondition_failed.clone_ref(py),
            StatusCode::PAYLOAD_TOO_LARGE => self.status_payload_too_large.clone_ref(py),
            StatusCode::URI_TOO_LONG => self.status_uri_too_long.clone_ref(py),
            StatusCode::UNSUPPORTED_MEDIA_TYPE => self.status_unsupported_media_type.clone_ref(py),
            StatusCode::RANGE_NOT_SATISFIABLE => self.status_range_not_satisfiable.clone_ref(py),
            StatusCode::EXPECTATION_FAILED => self.status_expectation_failed.clone_ref(py),
            StatusCode::IM_A_TEAPOT => self.status_im_a_teapot.clone_ref(py),
            StatusCode::MISDIRECTED_REQUEST => self.status_misdirected_request.clone_ref(py),
            StatusCode::UNPROCESSABLE_ENTITY => self.status_unprocessable_entity.clone_ref(py),
            StatusCode::LOCKED => self.status_locked.clone_ref(py),
            StatusCode::FAILED_DEPENDENCY => self.status_failed_dependency.clone_ref(py),
            StatusCode::TOO_EARLY => self.status_too_early.clone_ref(py),
            StatusCode::UPGRADE_REQUIRED => self.status_upgrade_required.clone_ref(py),
            StatusCode::PRECONDITION_REQUIRED => self.status_precondition_required.clone_ref(py),
            StatusCode::TOO_MANY_REQUESTS => self.status_too_many_requests.clone_ref(py),
            StatusCode::REQUEST_HEADER_FIELDS_TOO_LARGE => {
                self.status_request_header_fields_too_large.clone_ref(py)
            }
            StatusCode::UNAVAILABLE_FOR_LEGAL_REASONS => {
                self.status_unavailable_for_legal_reasons.clone_ref(py)
            }
            StatusCode::INTERNAL_SERVER_ERROR => self.status_internal_server_error.clone_ref(py),
            StatusCode::NOT_IMPLEMENTED => self.status_not_implemented.clone_ref(py),
            StatusCode::BAD_GATEWAY => self.status_bad_gateway.clone_ref(py),
            StatusCode::SERVICE_UNAVAILABLE => self.status_service_unavailable.clone_ref(py),
            StatusCode::GATEWAY_TIMEOUT => self.status_gateway_timeout.clone_ref(py),
            StatusCode::HTTP_VERSION_NOT_SUPPORTED => {
                self.status_http_version_not_supported.clone_ref(py)
            }
            StatusCode::VARIANT_ALSO_NEGOTIATES => {
                self.status_variant_also_negotiates.clone_ref(py)
            }
            StatusCode::INSUFFICIENT_STORAGE => self.status_insufficient_storage.clone_ref(py),
            StatusCode::LOOP_DETECTED => self.status_loop_detected.clone_ref(py),
            StatusCode::NOT_EXTENDED => self.status_not_extended.clone_ref(py),
            StatusCode::NETWORK_AUTHENTICATION_REQUIRED => {
                self.status_network_authentication_required.clone_ref(py)
            }
            _ => PyInt::new(py, code.as_u16()).unbind(),
        }
    }

    #[allow(clippy::too_many_lines)]
    pub(crate) fn header_name(&self, py: Python<'_>, name: &HeaderName) -> Py<PyString> {
        match *name {
            header::ACCEPT => self.header_accept.get().as_py(py),
            header::ACCEPT_CHARSET => self.header_accept_charset.get().as_py(py),
            header::ACCEPT_ENCODING => self.header_accept_encoding.get().as_py(py),
            header::ACCEPT_LANGUAGE => self.header_accept_language.get().as_py(py),
            header::ACCEPT_RANGES => self.header_accept_ranges.get().as_py(py),
            header::ACCESS_CONTROL_ALLOW_CREDENTIALS => {
                self.header_access_control_allow_credentials.get().as_py(py)
            }
            header::ACCESS_CONTROL_ALLOW_HEADERS => {
                self.header_access_control_allow_headers.get().as_py(py)
            }
            header::ACCESS_CONTROL_ALLOW_METHODS => {
                self.header_access_control_allow_methods.get().as_py(py)
            }
            header::ACCESS_CONTROL_ALLOW_ORIGIN => {
                self.header_access_control_allow_origin.get().as_py(py)
            }
            header::ACCESS_CONTROL_EXPOSE_HEADERS => {
                self.header_access_control_expose_headers.get().as_py(py)
            }
            header::ACCESS_CONTROL_MAX_AGE => self.header_access_control_max_age.get().as_py(py),
            header::ACCESS_CONTROL_REQUEST_HEADERS => {
                self.header_access_control_request_headers.get().as_py(py)
            }
            header::ACCESS_CONTROL_REQUEST_METHOD => {
                self.header_access_control_request_method.get().as_py(py)
            }
            header::AGE => self.header_age.get().as_py(py),
            header::ALLOW => self.header_allow.get().as_py(py),
            header::ALT_SVC => self.header_alt_svc.get().as_py(py),
            header::AUTHORIZATION => self.header_authorization.get().as_py(py),
            header::CACHE_CONTROL => self.header_cache_control.get().as_py(py),
            header::CACHE_STATUS => self.header_cache_status.get().as_py(py),
            header::CDN_CACHE_CONTROL => self.header_cdn_cache_control.get().as_py(py),
            header::CONNECTION => self.header_connection.get().as_py(py),
            header::CONTENT_DISPOSITION => self.header_content_disposition.get().as_py(py),
            header::CONTENT_ENCODING => self.header_content_encoding.get().as_py(py),
            header::CONTENT_LANGUAGE => self.header_content_language.get().as_py(py),
            header::CONTENT_LENGTH => self.header_content_length.get().as_py(py),
            header::CONTENT_LOCATION => self.header_content_location.get().as_py(py),
            header::CONTENT_RANGE => self.header_content_range.get().as_py(py),
            header::CONTENT_SECURITY_POLICY => self.header_content_security_policy.get().as_py(py),
            header::CONTENT_SECURITY_POLICY_REPORT_ONLY => self
                .header_content_security_policy_report_only
                .get()
                .as_py(py),
            header::CONTENT_TYPE => self.header_content_type.get().as_py(py),
            header::COOKIE => self.header_cookie.get().as_py(py),
            header::DNT => self.header_dnt.get().as_py(py),
            header::DATE => self.header_date.get().as_py(py),
            header::ETAG => self.header_etag.get().as_py(py),
            header::EXPECT => self.header_expect.get().as_py(py),
            header::EXPIRES => self.header_expires.get().as_py(py),
            header::FORWARDED => self.header_forwarded.get().as_py(py),
            header::FROM => self.header_from.get().as_py(py),
            header::HOST => self.header_host.get().as_py(py),
            header::IF_MATCH => self.header_if_match.get().as_py(py),
            header::IF_MODIFIED_SINCE => self.header_if_modified_since.get().as_py(py),
            header::IF_NONE_MATCH => self.header_if_none_match.get().as_py(py),
            header::IF_RANGE => self.header_if_range.get().as_py(py),
            header::IF_UNMODIFIED_SINCE => self.header_if_unmodified_since.get().as_py(py),
            header::LAST_MODIFIED => self.header_last_modified.get().as_py(py),
            header::LINK => self.header_link.get().as_py(py),
            header::LOCATION => self.header_location.get().as_py(py),
            header::MAX_FORWARDS => self.header_max_forwards.get().as_py(py),
            header::ORIGIN => self.header_origin.get().as_py(py),
            header::PRAGMA => self.header_pragma.get().as_py(py),
            header::PROXY_AUTHENTICATE => self.header_proxy_authenticate.get().as_py(py),
            header::PROXY_AUTHORIZATION => self.header_proxy_authorization.get().as_py(py),
            header::PUBLIC_KEY_PINS => self.header_public_key_pins.get().as_py(py),
            header::PUBLIC_KEY_PINS_REPORT_ONLY => {
                self.header_public_key_pins_report_only.get().as_py(py)
            }
            header::RANGE => self.header_range.get().as_py(py),
            header::REFERER => self.header_referer.get().as_py(py),
            header::REFERRER_POLICY => self.header_referrer_policy.get().as_py(py),
            header::REFRESH => self.header_refresh.get().as_py(py),
            header::RETRY_AFTER => self.header_retry_after.get().as_py(py),
            header::SEC_WEBSOCKET_ACCEPT => self.header_sec_websocket_accept.get().as_py(py),
            header::SEC_WEBSOCKET_EXTENSIONS => {
                self.header_sec_websocket_extensions.get().as_py(py)
            }
            header::SEC_WEBSOCKET_KEY => self.header_sec_websocket_key.get().as_py(py),
            header::SEC_WEBSOCKET_PROTOCOL => self.header_sec_websocket_protocol.get().as_py(py),
            header::SEC_WEBSOCKET_VERSION => self.header_sec_websocket_version.get().as_py(py),
            header::SERVER => self.header_server.get().as_py(py),
            header::SET_COOKIE => self.header_set_cookie.get().as_py(py),
            header::STRICT_TRANSPORT_SECURITY => {
                self.header_strict_transport_security.get().as_py(py)
            }
            header::TE => self.header_te.get().as_py(py),
            header::TRAILER => self.header_trailer.get().as_py(py),
            header::TRANSFER_ENCODING => self.header_transfer_encoding.get().as_py(py),
            header::USER_AGENT => self.header_user_agent.get().as_py(py),
            header::UPGRADE => self.header_upgrade.get().as_py(py),
            header::UPGRADE_INSECURE_REQUESTS => {
                self.header_upgrade_insecure_requests.get().as_py(py)
            }
            header::VARY => self.header_vary.get().as_py(py),
            header::VIA => self.header_via.get().as_py(py),
            header::WARNING => self.header_warning.get().as_py(py),
            header::WWW_AUTHENTICATE => self.header_www_authenticate.get().as_py(py),
            header::X_CONTENT_TYPE_OPTIONS => self.header_x_content_type_options.get().as_py(py),
            header::X_DNS_PREFETCH_CONTROL => self.header_x_dns_prefetch_control.get().as_py(py),
            header::X_FRAME_OPTIONS => self.header_x_frame_options.get().as_py(py),
            header::X_XSS_PROTECTION => self.header_x_xss_protection.get().as_py(py),
            _ => PyString::new(py, name.as_str()).unbind(),
        }
    }
}

impl Deref for Constants {
    type Target = ConstantsInner;

    fn deref(&self) -> &Self::Target {
        &self.inner
    }
}

fn get_class_attr<T: PyTypeInfo>(py: Python<'_>, name: &str) -> PyResult<Py<T>> {
    let cls = py.get_type::<T>();
    let attr = cls.getattr(name)?;
    Ok(attr.cast::<T>()?.clone().unbind())
}
