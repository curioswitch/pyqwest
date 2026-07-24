use bytes::Bytes;
use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    intern,
    pybacked::PyBackedBytes,
    pyclass, pymethods,
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyString, PyStringMethods as _},
    Bound, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
};

/// A single part of a multipart form.
#[pyclass(module = "_pyqwest", frozen)]
pub(crate) struct Part {
    pub(crate) content: PartContent,
    pub(crate) filename: Option<String>,
    pub(crate) content_type: Option<String>,
}

pub(crate) enum PartContent {
    Bytes(PyBackedBytes),
    Stream(Py<PyAny>),
}

#[pymethods]
impl Part {
    #[new]
    #[pyo3(signature = (content, *, filename=None, content_type=None))]
    fn py_new(
        content: &Bound<'_, PyAny>,
        filename: Option<String>,
        content_type: Option<String>,
    ) -> PyResult<Self> {
        if let Some(content_type) = &content_type {
            content_type
                .parse::<mime::Mime>()
                .map_err(|e| PyValueError::new_err(format!("Invalid content type: {e}")))?;
        }
        Ok(Self {
            content: PartContent::from_py(content)?,
            filename,
            content_type,
        })
    }

    #[getter]
    fn content<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match &self.content {
            PartContent::Bytes(bytes) => bytes.into_bound_py_any(py),
            PartContent::Stream(stream) => Ok(stream.bind(py).clone()),
        }
    }

    #[getter]
    fn filename(&self) -> Option<&str> {
        self.filename.as_deref()
    }

    #[getter]
    fn content_type(&self) -> Option<&str> {
        self.content_type.as_deref()
    }
}

impl Part {
    /// Builds a reqwest part from an already converted body, applying
    /// the part's metadata.
    pub(crate) fn new_reqwest(&self, body: reqwest::Body) -> PyResult<reqwest::multipart::Part> {
        let mut part = reqwest::multipart::Part::stream(body);
        if let Some(filename) = &self.filename {
            part = part.file_name(filename.clone());
        }
        if let Some(content_type) = &self.content_type {
            part = part
                .mime_str(content_type)
                .map_err(|e| PyValueError::new_err(format!("Invalid content type: {e}")))?;
        }
        Ok(part)
    }
}

impl PartContent {
    fn from_py(obj: &Bound<'_, PyAny>) -> PyResult<Self> {
        if let Ok(bytes) = obj.extract::<PyBackedBytes>() {
            return Ok(Self::Bytes(bytes));
        }
        if let Ok(string) = obj.cast::<PyString>() {
            return Ok(Self::Bytes(string.encode_utf8()?.extract()?));
        }
        let py = obj.py();
        if obj.hasattr(intern!(py, "__iter__"))? || obj.hasattr(intern!(py, "__aiter__"))? {
            return Ok(Self::Stream(obj.clone().unbind()));
        }
        Err(PyTypeError::new_err(
            "Part content must be bytes, str, or an iterator of bytes",
        ))
    }
}

/// A multipart form request content.
#[pyclass(module = "_pyqwest", frozen)]
pub(crate) struct Multipart {
    pub(crate) parts: Vec<(String, Py<Part>)>,
}

#[pymethods]
impl Multipart {
    #[new]
    fn py_new(py: Python<'_>, parts: &Bound<'_, PyAny>) -> PyResult<Self> {
        let mut converted: Vec<(String, Py<Part>)> = Vec::new();
        if let Ok(parts_dict) = parts.cast::<PyDict>() {
            for (name, part) in parts_dict.iter() {
                converted.push(convert_part(py, &name, &part)?);
            }
        } else if parts.hasattr(intern!(py, "items"))? {
            // Non-dict mappings, which iterate over keys rather than pairs.
            convert_pairs(
                py,
                &parts.call_method0(intern!(py, "items"))?,
                &mut converted,
            )?;
        } else {
            convert_pairs(py, parts, &mut converted)?;
        }
        Ok(Self { parts: converted })
    }

    #[getter]
    fn parts(&self, py: Python<'_>) -> Vec<(String, Py<Part>)> {
        self.parts
            .iter()
            .map(|(name, part)| (name.clone(), part.clone_ref(py)))
            .collect()
    }
}

impl Multipart {
    /// Builds a reqwest multipart form from the parts, converting stream
    /// content to a body with the given closure.
    pub(crate) fn build_form(
        &self,
        py: Python<'_>,
        mut stream_into_body: impl FnMut(Python<'_>, &Py<PyAny>) -> PyResult<reqwest::Body>,
    ) -> PyResult<reqwest::multipart::Form> {
        let mut form = reqwest::multipart::Form::new();
        for (name, part) in &self.parts {
            let part = part.get();
            let body = match &part.content {
                PartContent::Bytes(bytes) => {
                    reqwest::Body::from(Bytes::from_owner(bytes.clone_ref(py)))
                }
                PartContent::Stream(stream) => stream_into_body(py, stream)?,
            };
            form = form.part(name.clone(), part.new_reqwest(body)?);
        }
        Ok(form)
    }
}

fn convert_pairs(
    py: Python<'_>,
    pairs: &Bound<'_, PyAny>,
    converted: &mut Vec<(String, Py<Part>)>,
) -> PyResult<()> {
    for item in pairs.try_iter()? {
        let item = item?;
        let name = item.get_item(0)?;
        let part = item.get_item(1)?;
        converted.push(convert_part(py, &name, &part)?);
    }
    Ok(())
}

fn convert_part(
    py: Python<'_>,
    name: &Bound<'_, PyAny>,
    part: &Bound<'_, PyAny>,
) -> PyResult<(String, Py<Part>)> {
    let name = name.cast::<PyString>()?.to_str()?.to_owned();
    let part = if let Ok(part) = part.cast::<Part>() {
        part.clone().unbind()
    } else {
        Py::new(
            py,
            Part {
                content: PartContent::from_py(part)?,
                filename: None,
                content_type: None,
            },
        )?
    };
    Ok((name, part))
}
