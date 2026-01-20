use bytes::Bytes;
use pyo3::{
    exceptions::PyTypeError,
    pybacked::PyBackedBytes,
    pyclass, pyfunction, pymethods,
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyModule, PyString},
    Bound, IntoPyObject as _, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};
use tokio_stream::StreamExt as _;

use crate::{
    asyncio::{
        awaitable::{EmptyAsyncIterator, ValueAsyncIterator},
        stream::into_stream,
    },
    headers::Headers,
    shared::{
        constants::Constants,
        request::{RequestHead, RequestStreamResult},
    },
};

#[pyclass(module = "_pyqwest", frozen)]
pub struct Request {
    head: RequestHead,
    content: Option<Content>,

    constants: Constants,
}

#[pymethods]
impl Request {
    #[new]
    #[pyo3(signature = (method, url, headers=None, content=None))]
    pub(crate) fn py_new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Self> {
        Request::new(py, method, url, headers, content, Constants::get(py)?)
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
            Some(Content::Bytes(bytes)) => ValueAsyncIterator {
                value: Some(bytes.into_py_any(py)?),
            }
            .into_bound_py_any(py),
            Some(Content::AsyncIter(iter)) => Ok(iter.bind(py).clone().into_any()),
            None => EmptyAsyncIterator.into_bound_py_any(py),
        }
    }
}

impl Request {
    pub(super) fn new<'py>(
        py: Python<'py>,
        method: &str,
        url: &str,
        headers: Option<Bound<'py, Headers>>,
        content: Option<Bound<'py, PyAny>>,
        constants: Constants,
    ) -> PyResult<Self> {
        let headers = Headers::from_option(py, headers)?;
        let content: Option<Content> = match content {
            Some(content) => Some(Content::from_py(&content, &constants)?),
            None => None,
        };
        Ok(Self {
            head: RequestHead::new(method, url, headers)?,
            content,
            constants,
        })
    }

    pub(super) fn new_reqwest_builder(
        &self,
        py: Python<'_>,
        client: &reqwest::Client,
        http3: bool,
    ) -> PyResult<(reqwest::RequestBuilder, Option<Py<PyAny>>)> {
        let mut req_builder = self.head.new_request_builder(py, client, http3)?;
        let mut request_iter_task: Option<Py<PyAny>> = None;
        if let (Some(body), task) = self.content_into_reqwest(py)? {
            req_builder = req_builder.body(body);
            request_iter_task = task;
        }
        Ok((req_builder, request_iter_task))
    }

    fn content_into_reqwest(
        &self,
        py: Python<'_>,
    ) -> PyResult<(Option<reqwest::Body>, Option<Py<PyAny>>)> {
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                // TODO: Replace this dance with clone_ref when released.
                // https://github.com/PyO3/pyo3/pull/5654
                // SAFETY: Implementation known never to error, we unwrap to easily
                // switch to clone_ref later.
                let bytes = bytes.into_pyobject(py).unwrap();
                let bytes = PyBackedBytes::from(bytes);
                Ok((Some(reqwest::Body::from(Bytes::from_owner(bytes))), None))
            }
            Some(Content::AsyncIter(iter)) => {
                let iter = wrap_async_iter(py, iter)?;
                let (stream, task) = into_stream(py, iter, &self.constants)?;
                let res = stream.map(bytes_from_chunk);
                Ok((Some(reqwest::Body::wrap_stream(res)), Some(task)))
            }
            None => Ok((None, None)),
        }
    }
}

enum Content {
    Bytes(PyBackedBytes),
    AsyncIter(Py<PyAny>),
}

impl Content {
    fn from_py(obj: &Bound<'_, PyAny>, constants: &Constants) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }

        let aiter = obj.call_method0(&constants.__aiter__).map_err(|_| {
            PyTypeError::new_err("Content must be bytes or an async iterator of bytes")
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
