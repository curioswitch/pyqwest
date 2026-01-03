use bytes::Bytes;
use pyo3::{
    exceptions::PyTypeError,
    intern,
    pybacked::PyBackedBytes,
    pyclass, pyfunction, pymethods,
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyModule},
    Borrowed, Bound, FromPyObject, IntoPyObject as _, Py, PyAny, PyErr, PyResult, Python,
};
use pyo3_async_runtimes::tokio::into_stream_v2;
use tokio_stream::StreamExt as _;

use crate::shared::request::RequestHead;

#[pyclass(frozen)]
pub struct Request {
    head: RequestHead,
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
        let head = RequestHead::new(py, method, url, headers)?;
        let content: Option<Content> = match content {
            Some(content) => Some(content.extract()?),
            None => None,
        };
        Ok(Self { head, content })
    }
}

impl Request {
    pub(crate) fn as_reqwest_builder(
        &self,
        py: Python<'_>,
        client: &reqwest::Client,
        http3: bool,
    ) -> PyResult<reqwest::RequestBuilder> {
        let mut req_builder = self.head.new_request_builder(py, client, http3);
        if let Some(body) = self.content_into_reqwest(py)? {
            req_builder = req_builder.body(body);
        }
        Ok(req_builder)
    }

    fn content_into_reqwest(&self, py: Python<'_>) -> PyResult<Option<reqwest::Body>> {
        match &self.content {
            Some(Content::Bytes(bytes)) => {
                // TODO: Replace this dance with clone_ref when released.
                // https://github.com/PyO3/pyo3/pull/5654
                // SAFETY: Implementation known never to error, we unwrap to easily
                // switch to clone_ref later.
                let bytes = bytes.into_pyobject(py).unwrap();
                let bytes = PyBackedBytes::from(bytes);
                Ok(Some(reqwest::Body::from(Bytes::from_owner(bytes))))
            }
            Some(Content::AsyncIter(iter)) => {
                let res = into_stream_v2(iter.bind(py).clone())?
                    .map(|item| Ok::<_, PyErr>(bytes_from_chunk(item)));
                Ok(Some(reqwest::Body::wrap_stream(res)))
            }
            None => Ok(None),
        }
    }
}

enum Content {
    Bytes(PyBackedBytes),
    AsyncIter(Py<PyAny>),
}

impl FromPyObject<'_, '_> for Content {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }

        let py = obj.py();
        let aiter = obj.call_method0(intern!(py, "__aiter__")).map_err(|_| {
            PyTypeError::new_err("Content must be bytes or an async iterator of bytes")
        })?;
        let wrapped = wrap_async_iter(py, aiter.unbind())?;
        Ok(Self::AsyncIter(wrapped))
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
fn wrap_body_chunk(py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<Py<BodyChunk>> {
    let bytes = data.extract::<Bytes>()?;
    Py::new(py, BodyChunk { bytes })
}

fn bytes_from_chunk(item: Py<PyAny>) -> Bytes {
    // SAFETY: items originate from wrap_body_gen, which yields BodyChunk instances.
    let chunk: Py<BodyChunk> = unsafe { std::mem::transmute(item) };
    chunk.get().bytes.clone()
}
