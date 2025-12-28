use std::sync::Arc;

use pyo3::{
    exceptions::{PyRuntimeError, PyStopAsyncIteration},
    pyclass, pymethods, Bound, Py, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::future_into_py;
use tokio::sync::Mutex;

use crate::{common::HTTPVersion, headers::Headers};

#[pyclass]
pub(crate) struct Response {
    /// The HTTP status code of the response.
    status: u16,

    /// The HTTP version of the response.
    http_version: HTTPVersion,

    /// The response headers. We convert from Rust to Python lazily mainly to make sure
    /// it happens on a Python thread instead of Tokio.
    headers: Option<Py<Headers>>,

    /// The response trailers. Will only be present after consuming the response content.
    /// If after consumption, it is still None, it means there were no trailers.
    trailers: Option<Py<Headers>>,

    /// The underlying reqwest response.
    response: Arc<Mutex<reqwest::Response>>,
}

impl Response {
    pub(crate) fn new(response: reqwest::Response) -> Response {
        let status = response.status().as_u16();
        let http_version = match response.version() {
            reqwest::Version::HTTP_09 => HTTPVersion::HTTP1,
            reqwest::Version::HTTP_10 => HTTPVersion::HTTP1,
            reqwest::Version::HTTP_11 => HTTPVersion::HTTP1,
            reqwest::Version::HTTP_2 => HTTPVersion::HTTP2,
            reqwest::Version::HTTP_3 => HTTPVersion::HTTP3,
            _ => HTTPVersion::HTTP1,
        };
        Response {
            status,
            http_version,
            headers: None,
            trailers: None,
            response: Arc::new(Mutex::new(response)),
        }
    }
}

#[pymethods]
impl Response {
    #[getter]
    fn status(&self) -> u16 {
        self.status
    }

    #[getter]
    fn http_version(&self) -> HTTPVersion {
        self.http_version.clone()
    }

    #[getter]
    fn headers<'py>(&mut self, py: Python<'py>) -> PyResult<Py<Headers>> {
        if let Some(headers) = &self.headers {
            Ok(headers.clone_ref(py))
        } else {
            let headers =
                Headers::from_response_headers(py, self.response.blocking_lock().headers());
            let headers = Py::new(py, headers)?;
            self.headers = Some(headers.clone_ref(py));
            Ok(headers)
        }
    }

    #[getter]
    fn trailers<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Py<Headers>>> {
        if let Some(trailers) = &self.trailers {
            Ok(Some(trailers.clone_ref(py)))
        } else {
            let mut response = self.response.blocking_lock();
            if let Some(trailers_map) = response.trailers() {
                let trailers = Headers::from_response_headers(py, trailers_map);
                let trailers = Py::new(py, trailers)?;
                self.trailers = Some(trailers.clone_ref(py));
                Ok(Some(trailers))
            } else {
                Ok(None)
            }
        }
    }

    #[getter]
    fn content<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, ContentGenerator>> {
        Bound::new(
            py,
            ContentGenerator {
                response: self.response.clone(),
            },
        )
    }
}

#[pyclass]
struct ContentGenerator {
    response: Arc<Mutex<reqwest::Response>>,
}

#[pymethods]
impl ContentGenerator {
    fn __aiter__(slf: Py<ContentGenerator>) -> Py<ContentGenerator> {
        slf
    }

    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let response = self.response.clone();
        future_into_py(py, async move {
            let mut response = response.lock().await;
            let chunk = response
                .chunk()
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("Error reading chunk: {}", e)))?;
            if let Some(bytes) = chunk {
                Ok(bytes)
            } else {
                Err(PyStopAsyncIteration::new_err(()))
            }
        })
    }
}
