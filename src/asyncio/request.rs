use bytes::Bytes;
use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    intern, pyclass, pyfunction, pymethods,
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyModule},
    Borrowed, Bound, FromPyObject, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::{tokio::into_stream_with_locals_v2, TaskLocals};
use tokio_stream::StreamExt;

use crate::headers::Headers;

#[pyclass]
pub struct Request {
    pub(crate) method: http::Method,
    pub(crate) url: reqwest::Url,
    pub(crate) headers: Option<Py<Headers>>,
    content: Option<Content>,
}

#[pymethods]
impl Request {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None))]
    pub(crate) fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, PyAny>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        let method = http::Method::try_from(method)
            .map_err(|e| PyValueError::new_err(format!("Invalid HTTP method: {}", e)))?;
        let url = reqwest::Url::parse(url)
            .map_err(|e| PyValueError::new_err(format!("Invalid URL: {}", e)))?;
        let headers = if let Some(headers) = headers {
            if let Ok(hdrs) = headers.cast::<Headers>() {
                Some(hdrs.clone().unbind())
            } else {
                Some(Py::new(py, Headers::py_new(Some(headers))?)?)
            }
        } else {
            None
        };
        let content: Option<Content> = match content {
            Some(content) => Some(content.extract()?),
            None => None,
        };
        Ok(Self {
            method,
            url,
            headers,
            content,
        })
    }
}

impl Request {
    pub(crate) fn content_into_reqwest<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<Option<reqwest::Body>> {
        match self.content.take() {
            Some(Content::Bytes(bytes)) => Ok(Some(reqwest::Body::from(bytes))),
            Some(Content::AsyncIter { iter, locals }) => {
                let res = into_stream_with_locals_v2(locals, iter.into_bound(py))?
                    .map(|item| Ok::<_, PyErr>(bytes_from_chunk(item)));
                Ok(Some(reqwest::Body::wrap_stream(res)))
            }
            None => Ok(None),
        }
    }
}

enum Content {
    Bytes(Bytes),
    AsyncIter { iter: Py<PyAny>, locals: TaskLocals },
}

impl FromPyObject<'_, '_> for Content {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<Bytes>() {
            return Ok(Self::Bytes(bytes));
        }

        let py = obj.py();
        let aiter = obj.call_method0(intern!(py, "__aiter__")).map_err(|_| {
            PyTypeError::new_err("Content must be bytes or an async iterator of bytes")
        })?;
        let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;
        let wrapped = wrap_async_iter(py, aiter.unbind())?;
        Ok(Self::AsyncIter {
            iter: wrapped,
            locals,
        })
    }
}

fn wrap_async_iter(py: Python<'_>, iter: Py<PyAny>) -> PyResult<Py<PyAny>> {
    static WRAP_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
    static GEN_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

    let wrap_fn = WRAP_FN
        .get_or_try_init(py, || {
            pyo3::wrap_pyfunction!(wrap_body_chunk, py).map(|func| func.unbind().into())
        })?
        .clone_ref(py);

    let gen_fn = GEN_FN
        .get_or_try_init(py, || {
            let module = PyModule::import(py, "pyqwest._glue")?;
            module.getattr("wrap_body_gen").map(Bound::unbind)
        })?
        .clone_ref(py);

    gen_fn.call1(py, (iter, wrap_fn))
}

#[pyclass(frozen)]
struct BodyChunk {
    bytes: Bytes,
}

#[pyfunction]
fn wrap_body_chunk(py: Python<'_>, data: Bound<'_, PyAny>) -> PyResult<Py<BodyChunk>> {
    let bytes = data.extract::<Bytes>()?;
    Py::new(py, BodyChunk { bytes })
}

fn bytes_from_chunk(item: Py<PyAny>) -> Bytes {
    // SAFETY: items originate from wrap_body_gen, which yields BodyChunk instances.
    let chunk: Py<BodyChunk> = unsafe { std::mem::transmute(item) };
    chunk.get().bytes.clone()
}
