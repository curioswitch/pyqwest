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
            inner: Arc::new(Operationinner {
                span: span.unbind(),
                response_info: Mutex::new(None),
                constants: self.constants.clone(),
            }),
        })
    }
}

struct ResponseInfo {
    status_code: http::StatusCode,
    http_version: http::Version,
}

struct Operationinner {
    span: Py<PyAny>,
    response_info: Mutex<Option<ResponseInfo>>,

    constants: Constants,
}

#[derive(Clone)]
pub(crate) struct Operation {
    inner: Arc<Operationinner>,
}

impl Operation {
    pub(crate) fn inject(&self, py: Python<'_>, request: &mut reqwest::Request) -> PyResult<()> {
        let inner = &self.inner;
        let context = inner
            .constants
            .set_span_in_context
            .call1(py, (&inner.span,))?;

        let headers = std::mem::take(request.headers_mut());
        let carrier = Headers(Some(headers)).into_pyobject(py)?;
        inner
            .constants
            .inject_context
            .call1(py, (&carrier, &context, &inner.constants.headers_setter))?;
        // SAFETY: This is only called in inject as an implementation detail, where we know
        // we set the headers, call inject, then retrieve them, in order in a single function.
        let hdrs = carrier.borrow_mut().0.take().unwrap();
        let _ = std::mem::replace(request.headers_mut(), hdrs);

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
        let inner = &self.inner;

        if let Some(response_info) = inner.response_info.lock_py_attached(py).unwrap().take() {
            let span = inner.span.bind(py);
            let _ = span.call_method1(
                &inner.constants.set_attribute,
                (
                    &inner.constants.http_response_status_code,
                    inner.constants.status_code(py, response_info.status_code),
                ),
            );
            let _ = span.call_method1(
                &inner.constants.set_attribute,
                (
                    &inner.constants.network_protocol_version,
                    network_protocol_version(py, response_info.http_version, &inner.constants),
                ),
            );
        }

        if let Some(err) = err {
            let span = inner.span.bind(py);
            if let Ok(qualname) = err.get_type(py).qualname() {
                let _ = span.call_method1(
                    &inner.constants.set_attribute,
                    (&inner.constants.error_type, &qualname),
                );
            }
        }

        inner.span.call_method0(py, &inner.constants.end)?;
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
struct Headers(Option<HeaderMap>);

#[pyclass(module = "_pyqwest.otel", name = "_HeadersSetter", frozen)]
pub(super) struct HeadersSetter;

#[pymethods]
impl HeadersSetter {
    #[allow(clippy::unused_self)]
    fn set(&self, carrier: &mut Headers, key: &str, value: &str) -> PyResult<()> {
        // SAFETY: This is only called in inject as an implementation detail, where we know
        // we set the headers, call inject, then retrieve them, in order.
        let carrier = carrier.0.as_mut().unwrap();
        carrier.append(
            HeaderName::from_str(key)
                .map_err(|_| PyValueError::new_err(format!("Invalid header name '{key}'")))?,
            HeaderValue::from_str(value)
                .map_err(|_| PyValueError::new_err(format!("Invalid header value '{value}'")))?,
        );

        Ok(())
    }
}
