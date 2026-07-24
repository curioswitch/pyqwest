use std::sync::Arc;

use arc_swap::ArcSwapOption;
use bytes::Bytes;
use pyo3::{
    exceptions::PyTypeError,
    pybacked::PyBackedBytes,
    pyclass, pyfunction, pymethods,
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyModule, PyString},
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use tokio_stream::StreamExt as _;

use crate::{
    asyncio::stream::{combine_tasks, into_stream},
    common::multipart::Multipart,
    headers::Headers,
    shared::{
        constants::Constants,
        request::{
            maybe_encode_json_content, multipart_content_type, RequestHead, RequestStreamResult,
        },
    },
};

#[pyclass(module = "_pyqwest", frozen)]
pub struct Request {
    pub(super) head: RequestHead,
    content: Option<Content>,

    constants: Constants,
}

#[pymethods]
impl Request {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None, *, params=None))]
    pub(crate) fn py_new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
        params: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        Request::new(
            py,
            method,
            url,
            headers,
            content,
            params,
            Constants::get(py)?,
        )
    }

    #[getter]
    fn method(&self, py: Python<'_>) -> PyResult<Py<PyString>> {
        self.head.method(py)
    }

    #[getter]
    fn url(&self) -> &str {
        self.head.url()
    }

    #[getter]
    fn headers(&self, py: Python<'_>) -> Py<Headers> {
        self.head.headers(py)
    }

    #[getter]
    fn content<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match &self.content {
            Some(Content::Bytes(bytes)) => bytes.into_bound_py_any(py),
            Some(Content::AsyncIter(iter)) => Ok(iter.bind(py).clone().into_any()),
            Some(Content::Multipart(multipart)) => Ok(multipart.bind(py).clone().into_any()),
            None => Ok(self.constants.empty_bytes.bind(py).clone().into_any()),
        }
    }

    #[getter]
    fn _json(&self) -> bool {
        self.head.json()
    }
}

impl Request {
    pub(super) fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
        params: Option<Bound<'py, PyAny>>,
        constants: Constants,
    ) -> PyResult<Self> {
        let headers = Headers::from_option(py, headers)?;
        let (content, json) =
            if let Some(content) = maybe_encode_json_content(py, content.as_ref(), &constants)? {
                (Some(content), true)
            } else {
                (content, false)
            };
        let content: Option<Content> = match content {
            Some(content) => Some(Content::from_py(&content, &constants)?),
            None => None,
        };
        Ok(Self {
            head: RequestHead::new(method, url, headers, params, json)?,
            content,
            constants,
        })
    }

    pub(super) fn new_reqwest(
        &self,
        py: Python<'_>,
        http3: bool,
    ) -> PyResult<(reqwest::Request, Arc<ArcSwapOption<Py<PyAny>>>)> {
        let mut req = self.head.new_reqwest(py, http3)?;
        let request_iter_task: Arc<ArcSwapOption<Py<PyAny>>> = Arc::new(ArcSwapOption::empty());
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                *req.body_mut() = Some(reqwest::Body::from(Bytes::from_owner(bytes.clone_ref(py))));
            }
            Some(Content::AsyncIter(iter)) => {
                let (body, task) = self.aiter_into_body(py, iter)?;
                *req.body_mut() = Some(body);
                request_iter_task.store(Some(Arc::new(task)));
            }
            Some(Content::Multipart(multipart)) => {
                let mut tasks: Vec<Py<PyAny>> = Vec::new();
                let form = multipart.get().build_form(py, |py, stream| {
                    let aiter = stream
                        .bind(py)
                        .call_method0(&self.constants.__aiter__)
                        .map_err(|_| {
                            PyTypeError::new_err(
                                "Part content must be bytes, str, or an async iterator of bytes",
                            )
                        })?;
                    let (body, task) = self.aiter_into_body(py, &aiter.unbind())?;
                    tasks.push(task);
                    Ok(body)
                });
                let form = match form {
                    Ok(form) => form,
                    Err(e) => {
                        // Cancel the tasks already spawned for earlier stream
                        // parts, which would otherwise keep consuming their
                        // iterators after the error.
                        for task in &tasks {
                            let _ = task.call_method0(py, &self.constants.cancel);
                        }
                        return Err(e);
                    }
                };
                req.headers_mut().insert(
                    http::header::CONTENT_TYPE,
                    multipart_content_type(form.boundary())?,
                );
                *req.body_mut() = Some(reqwest::Body::wrap_stream(form.into_stream()));
                if let Some(task) = combine_tasks(py, tasks, &self.constants)? {
                    request_iter_task.store(Some(Arc::new(task)));
                }
            }
            None => {}
        }
        Ok((req, request_iter_task))
    }

    fn aiter_into_body(
        &self,
        py: Python<'_>,
        iter: &Py<PyAny>,
    ) -> PyResult<(reqwest::Body, Py<PyAny>)> {
        let iter = wrap_async_iter(py, iter)?;
        let (stream, task) = into_stream(py, iter, &self.constants)?;
        let res = stream.map(bytes_from_chunk);
        Ok((reqwest::Body::wrap_stream(res), task))
    }
}

enum Content {
    Bytes(PyBackedBytes),
    AsyncIter(Py<PyAny>),
    Multipart(Py<Multipart>),
}

impl Content {
    fn from_py(obj: &Bound<'_, PyAny>, constants: &Constants) -> PyResult<Self> {
        if let Ok(multipart) = obj.cast::<Multipart>() {
            return Ok(Self::Multipart(multipart.clone().unbind()));
        }
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }

        let aiter = obj.call_method0(&constants.__aiter__).map_err(|_| {
            PyTypeError::new_err("Content must be bytes, an async iterator of bytes, or Multipart")
        })?;
        Ok(Self::AsyncIter(aiter.unbind()))
    }
}

fn wrap_async_iter<'py>(py: Python<'py>, iter: &Py<PyAny>) -> PyResult<Bound<'py, PyAny>> {
    static WRAP_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
    static GEN_FN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

    let wrap_fn = WRAP_FN
        .get_or_try_init(py, || {
            pyo3::wrap_pyfunction!(wrap_body_chunk, py).map(|func| func.unbind().into())
        })?
        .bind(py);

    let gen_fn = GEN_FN
        .get_or_try_init(py, || {
            let module = PyModule::import(py, "pyqwest._glue")?;
            module.getattr("wrap_body_gen").map(Bound::unbind)
        })?
        .bind(py);

    gen_fn.call1((iter, wrap_fn))
}

#[pyclass(module = "_pyqwest.async", frozen)]
struct BodyChunk {
    bytes: Bytes,
}

#[pyfunction]
fn wrap_body_chunk(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<BodyChunk>> {
    let bytes = data.extract::<Bytes>()?;
    Py::new(py, BodyChunk { bytes })
}

fn bytes_from_chunk(item: RequestStreamResult<Py<PyAny>>) -> RequestStreamResult<Bytes> {
    match item {
        Ok(item) => {
            // SAFETY: items originate from wrap_body_gen, which yields BodyChunk instances.
            let chunk: Py<BodyChunk> = unsafe { std::mem::transmute(item) };
            Ok(chunk.get().bytes.clone())
        }
        Err(e) => Err(e),
    }
}
