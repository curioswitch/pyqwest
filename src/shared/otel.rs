use std::{
    str::FromStr as _,
    sync::{Arc, Mutex},
};

use http::{HeaderMap, HeaderName, HeaderValue};
use pyo3::{
    exceptions::PyValueError,
    pyclass, pymethods,
    sync::MutexExt as _,
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyString, PyTypeMethods as _},
    IntoPyObject as _, Py, PyAny, PyErr, PyResult, Python,
};

use crate::shared::{constants::Constants, request::RequestHead};

#[derive(Clone)]
pub(crate) struct Instrumentation {
    constants: Constants,
    tracer: Arc<Py<PyAny>>,
}

impl Instrumentation {
    pub(crate) fn new(py: Python<'_>, constants: &Constants) -> PyResult<Self> {
        let tracer = constants.get_tracer.call1(py, ("pyqwest",))?;
        Ok(Self {
            constants: constants.clone(),
            tracer: Arc::new(tracer),
        })
    }

    pub(crate) fn start(&self, py: Python<'_>, request: &RequestHead) -> PyResult<Operation> {
        let http_method = request.method(py)?;
        let attrs = PyDict::new(py);
        // We require a host to send requests so any validated request will have a host.
        let host = request.parsed_url().host_str().unwrap_or_default();
        attrs.set_item(&self.constants.server_address, host)?;
        let port = request
            .parsed_url()
            .port_or_known_default()
            // We only support schemes with a known default port.
            .unwrap_or_default();
        attrs.set_item(&self.constants.server_port, port)?;
        attrs.set_item(&self.constants.http_request_method, &http_method)?;
        attrs.set_item(&self.constants.url_full, request.url())?;
        attrs.set_item(&self.constants.network_protocol_name, &self.constants.http)?;

        // Because we are wrapping Rust code, we have no use case for setting the current span,
        // which luckily simplifies the asyncio path substantially.
        let span = self.tracer.bind(py).call_method1(
            &self.constants.start_span,
            (
                http_method,
                py.None(),
                &self.constants.span_kind_client,
                attrs,
            ),
        )?;

        Ok(Operation {
            inner: Arc::new(OperationInner {
                span: span.unbind(),
                response_info: Mutex::new(None),
            }),
            constants: self.constants.clone(),
        })
    }
}

struct ResponseInfo {
    status_code: http::StatusCode,
    http_version: http::Version,
}

struct OperationInner {
    span: Py<PyAny>,
    response_info: Mutex<Option<ResponseInfo>>,
}

#[derive(Clone)]
pub(crate) struct Operation {
    inner: Arc<OperationInner>,

    constants: Constants,
}

impl Operation {
    pub(crate) fn inject(&self, py: Python<'_>, request: &mut reqwest::Request) -> PyResult<()> {
        let context = self
            .constants
            .set_span_in_context
            .call1(py, (&self.inner.span,))?;

        // Avoid allocating a new map - we have an exclusive borrow on Request, so we can take the
        // headers out, pass to python, and take them back, which only copies the HeaderMap struct
        // to/from the Python wrapper and not its heap allocations.
        let headers = std::mem::take(request.headers_mut());
        let carrier = Headers(headers).into_pyobject(py)?;
        self.constants
            .inject_context
            .call1(py, (&carrier, &context, &self.constants.headers_setter))?;
        let hdrs = std::mem::take(&mut carrier.borrow_mut().0);
        *request.headers_mut() = hdrs;

        Ok(())
    }

    pub(crate) fn fill_response(&self, response: &reqwest::Response) {
        let mut response_info = self.inner.response_info.lock().unwrap();

        *response_info = Some(ResponseInfo {
            status_code: response.status(),
            http_version: response.version(),
        });
    }

    pub(crate) fn end(&self, py: Python<'_>, err: Option<&PyErr>) -> PyResult<()> {
        let span = self.inner.span.bind(py);

        if let Some(response_info) = self
            .inner
            .response_info
            .lock_py_attached(py)
            .unwrap()
            .take()
        {
            let _ = span.call_method1(
                &self.constants.set_attribute,
                (
                    &self.constants.http_response_status_code,
                    self.constants.status_code(py, response_info.status_code),
                ),
            );
            let _ = span.call_method1(
                &self.constants.set_attribute,
                (
                    &self.constants.network_protocol_version,
                    network_protocol_version(py, response_info.http_version, &self.constants),
                ),
            );
        }

        if let Some(err) = err {
            if let Ok(qualname) = err.get_type(py).qualname() {
                let _ = span.call_method1(
                    &self.constants.set_attribute,
                    (&self.constants.error_type, &qualname),
                );
            }
        }

        span.call_method0(&self.constants.end)?;
        Ok(())
    }
}

fn network_protocol_version(
    py: Python<'_>,
    ver: http::Version,
    constants: &Constants,
) -> Py<PyString> {
    match ver {
        http::Version::HTTP_10 => constants.otel_1_0.clone_ref(py),
        http::Version::HTTP_2 => constants.otel_2.clone_ref(py),
        http::Version::HTTP_3 => constants.otel_3.clone_ref(py),
        _ => constants.otel_1_1.clone_ref(py),
    }
}

#[pyclass(module = "_pyqwest.otel", name = "_Headers")]
struct Headers(HeaderMap);

#[pyclass(module = "_pyqwest.otel", name = "_HeadersSetter", frozen)]
pub(super) struct HeadersSetter;

const HEADER_NAME_BAGGAGE: HeaderName = HeaderName::from_static("baggage");
const HEADER_NAME_TRACEPARENT: HeaderName = HeaderName::from_static("traceparent");
const HEADER_NAME_TRACESTATE: HeaderName = HeaderName::from_static("tracestate");

fn header_name(name: &str) -> PyResult<HeaderName> {
    match name {
        "baggage" => Ok(HEADER_NAME_BAGGAGE),
        "traceparent" => Ok(HEADER_NAME_TRACEPARENT),
        "tracestate" => Ok(HEADER_NAME_TRACESTATE),
        _ => HeaderName::from_str(name)
            .map_err(|_| PyValueError::new_err(format!("Invalid header name '{name}'"))),
    }
}

#[pymethods]
impl HeadersSetter {
    #[allow(clippy::unused_self)]
    fn set(&self, carrier: &mut Headers, key: &str, value: &str) -> PyResult<()> {
        carrier.0.append(
            header_name(key)?,
            HeaderValue::from_str(value)
                .map_err(|_| PyValueError::new_err(format!("Invalid header value '{value}'")))?,
        );

        Ok(())
    }
}
