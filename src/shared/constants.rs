use std::{ops::Deref, sync::Arc};

use http::StatusCode;
use pyo3::{
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyBytes, PyInt, PyString},
    Py, PyAny, PyResult, PyTypeInfo, Python,
};

use crate::common::httpversion::HTTPVersion;

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

    /// The _glue.py function `execute_and_read_full`.
    pub execute_and_read_full: Py<PyAny>,
    /// The _glue.py function `forward`.
    pub forward: Py<PyAny>,
    /// The _glue.py function `read_content_sync`.
    pub read_content_sync: Py<PyAny>,

    /// The stdlib function `json.loads`.
    pub json_loads: Py<PyAny>,
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

                execute_and_read_full: glue.getattr("execute_and_read_full")?.unbind(),
                forward: glue.getattr("forward")?.unbind(),
                read_content_sync: glue.getattr("read_content_sync")?.unbind(),

                json_loads: py.import("json")?.getattr("loads")?.unbind(),
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
