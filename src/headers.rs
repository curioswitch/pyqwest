use std::str::FromStr as _;
use std::sync::Mutex;

use http::{header, HeaderMap, HeaderName, HeaderValue};
use mime::Mime;
use pyo3::exceptions::{PyKeyError, PyTypeError, PyValueError};
use pyo3::sync::MutexExt as _;
use pyo3::types::{
    PyAnyMethods as _, PyDict, PyIterator, PyList, PyListMethods as _, PyMapping, PyString,
    PyStringMethods as _, PyTuple,
};
use pyo3::{prelude::*, IntoPyObjectExt as _};
use std::fmt::Write as _;

use crate::common::headername::HttpHeaderName;
use crate::shared::constants::Constants;

#[pyclass(module = "pyqwest", mapping, frozen)]
pub(crate) struct Headers {
    pub(crate) store: Mutex<HeaderMap<PyHeaderValue>>,
}

impl Headers {
    pub(crate) fn empty() -> Self {
        Headers {
            store: Mutex::new(HeaderMap::default()),
        }
    }

    pub(crate) fn from_option(
        py: Python<'_>,
        hdrs: Option<Bound<'_, Headers>>,
    ) -> PyResult<Py<Self>> {
        if let Some(hdrs) = hdrs {
            Ok(hdrs.unbind())
        } else {
            Py::new(py, Headers::empty())
        }
    }

    pub(crate) fn fill(&self, headers: HeaderMap) {
        let mut store = self.store.lock().unwrap();
        store.reserve(headers.len());
        let mut current_key: Option<HeaderName> = None;
        for (key, value) in headers {
            if let Some(key) = key {
                current_key = Some(key);
            }

            store.append(
                // SAFETY: A key is guaranteed to be present on the first iteration.
                current_key.as_ref().unwrap(),
                PyHeaderValue::from_http(value),
            );
        }
    }
}

#[pymethods]
impl Headers {
    #[new]
    #[pyo3(signature = (items=None))]
    pub(crate) fn py_new(items: Option<Bound<'_, PyAny>>) -> PyResult<Self> {
        let store = match items {
            Some(items) => store_from_py(&items)?,
            None => HeaderMap::default(),
        };
        Ok(Headers {
            store: Mutex::new(store),
        })
    }

    fn __getitem__<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyString>> {
        let key = normalize_key(key)?;
        if let Some(value) = self.store.lock_py_attached(py).unwrap().get_mut(&key) {
            Ok(value.as_py(py))
        } else {
            Err(PyKeyError::new_err(format!("KeyError: '{key}'")))
        }
    }

    fn __setitem__<'py>(
        &self,
        py: Python<'_>,
        key: &Bound<'py, PyAny>,
        value: &Bound<'py, PyString>,
    ) -> PyResult<()> {
        self.store
            .lock_py_attached(py)
            .unwrap()
            .insert(normalize_key(key)?, PyHeaderValue::from_py(value)?);
        Ok(())
    }

    fn __delitem__(&self, py: Python<'_>, key: &Bound<'_, PyAny>) -> PyResult<()> {
        let key = normalize_key(key)?;
        if self
            .store
            .lock_py_attached(py)
            .unwrap()
            .remove(&key)
            .is_none()
        {
            Err(PyKeyError::new_err(format!("KeyError: '{key}'")))
        } else {
            Ok(())
        }
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let constants = Constants::get(py)?;
        let keys = PyList::new(
            py,
            self.store
                .lock_py_attached(py)
                .unwrap()
                .keys()
                .map(|name| constants.header_name(py, name)),
        )?;

        PyIterator::from_object(&keys)
    }

    fn __len__(&self, py: Python<'_>) -> usize {
        self.store.lock_py_attached(py).unwrap().keys_len()
    }

    fn __contains__(&self, py: Python<'_>, key: &Bound<'_, PyAny>) -> bool {
        let Ok(key) = normalize_key(key) else {
            return false;
        };
        self.store.lock_py_attached(py).unwrap().contains_key(key)
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let store = self.store.lock_py_attached(py).unwrap();
        if store.is_empty() {
            return "Headers()".to_string();
        }
        let mut res = "Headers(".to_string();
        let mut first = true;
        for (key, value) in store.iter() {
            if !first {
                res.push_str(", ");
            }
            let value_str = match &value.kind {
                PyHeaderValueKind::Http(http) => http.to_str().unwrap_or_default(),
                PyHeaderValueKind::Py(py_str) => py_str.bind(py).to_str().unwrap_or_default(),
            };
            let _ = write!(res, "('{}', '{}')", key.as_str(), value_str);
            first = false;
        }
        res.push(')');
        res
    }

    fn __eq__<'py>(&self, py: Python<'py>, other: &Bound<'py, PyAny>) -> PyResult<bool> {
        if let Ok(other) = other.cast::<Headers>() {
            let other = other.get();
            if std::ptr::eq(self, &raw const *other) {
                return Ok(true);
            }
            let store = self.store.lock_py_attached(py).unwrap();
            let other_store = other.store.lock_py_attached(py).unwrap();
            Ok(stores_equal(py, &store, &other_store))
        } else {
            let store = self.store.lock_py_attached(py).unwrap();
            let other_store = store_from_py(other)?;
            Ok(stores_equal(py, &store, &other_store))
        }
    }

    #[pyo3(signature = (key, default=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
        default: Option<Bound<'py, PyAny>>,
    ) -> Option<Bound<'py, PyAny>> {
        let Ok(key) = normalize_key(key) else {
            return default;
        };
        if let Some(value) = self.store.lock_py_attached(py).unwrap().get_mut(key) {
            Some(value.as_py(py).into_any())
        } else {
            default
        }
    }

    #[pyo3(signature = (key, *args))]
    fn pop<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<PyAny>,
        args: &Bound<'py, PyTuple>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if args.len() > 1 {
            return Err(PyTypeError::new_err(format!(
                "pop expected at most 2 arguments, got {}",
                1 + args.len()
            )));
        }
        let key = normalize_key(key)?;
        if let Some(mut value) = self.store.lock_py_attached(py).unwrap().remove(&key) {
            Ok(value.as_py(py).into_any())
        } else if args.len() == 1 {
            let default = args.get_item(0)?;
            Ok(default.clone())
        } else {
            Err(PyKeyError::new_err(format!("KeyError: '{}'", key.as_str())))
        }
    }

    fn popitem(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut store = self.store.lock_py_attached(py).unwrap();
        let Some(key) = store.keys().next() else {
            return Err(PyKeyError::new_err("Headers is empty"));
        };
        let key = key.clone();
        let constants = Constants::get(py)?;
        match store.entry(key) {
            header::Entry::Occupied(occ) => {
                // We only want to pop off the last value, but HeaderMap's implementation means
                // we remove them all and add back.
                let (name, mut values) = occ.remove_entry_mult();

                let mut result = values.next().unwrap();
                let mut rest: Vec<PyHeaderValue> = Vec::new();
                for value in values {
                    rest.push(result);
                    result = value;
                }

                for value in rest {
                    store.append(name.clone(), value);
                }
                let key_py = constants.header_name(py, &name);
                let tuple = PyTuple::new(py, [key_py.bind(py), &result.as_py(py)])?;
                Ok(tuple.into())
            }
            header::Entry::Vacant(_) => unreachable!(),
        }
    }

    #[pyo3(signature = (key, default=None))]
    fn setdefault<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
        default: Option<&Bound<'py, PyString>>,
    ) -> PyResult<Option<Bound<'py, PyString>>> {
        let key = normalize_key(key)?;
        let mut store = self.store.lock_py_attached(py).unwrap();
        if let Some(value) = store.get_mut(&key) {
            Ok(Some(value.as_py(py)))
        } else if let Some(default) = default {
            store.insert(key, PyHeaderValue::from_py(default)?);
            Ok(Some(default.clone()))
        } else {
            Ok(None)
        }
    }

    fn add<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
        value: &Bound<'py, PyString>,
    ) -> PyResult<()> {
        self.store
            .lock_py_attached(py)
            .unwrap()
            .append(normalize_key(key)?, PyHeaderValue::from_py(value)?);
        Ok(())
    }

    #[pyo3(signature = (items=None, **kwargs))]
    fn update<'py>(
        &self,
        py: Python<'py>,
        items: Option<Bound<'py, PyAny>>,
        kwargs: Option<&Bound<'py, PyDict>>,
    ) -> PyResult<()> {
        let mut store = self.store.lock_py_attached(py).unwrap();
        if let Some(items) = items {
            if let Ok(mapping) = items.cast::<PyMapping>() {
                for item in mapping.items()?.iter() {
                    let key_py = item.get_item(0)?;
                    let key = normalize_key(&key_py)?;
                    let value_py = item.get_item(1)?;
                    let value = value_py.cast::<PyString>()?;
                    store.insert(key, PyHeaderValue::from_py(value)?);
                }
            } else {
                for item in items.try_iter()? {
                    let item = item?;
                    let key_py = item.get_item(0)?;
                    let key = normalize_key(&key_py)?;
                    let value_py = item.get_item(1)?;
                    let value = value_py.cast::<PyString>()?;
                    store.insert(key, PyHeaderValue::from_py(value)?);
                }
            }
        }
        if let Some(kwargs) = kwargs {
            for (key_py, value_py) in kwargs.iter() {
                let key = normalize_key(&key_py)?;
                let value = value_py.cast::<PyString>()?;
                store.insert(key, PyHeaderValue::from_py(value)?);
            }
        }
        Ok(())
    }

    fn clear(&self, py: Python<'_>) {
        self.store.lock_py_attached(py).unwrap().clear();
    }

    fn getall<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyList>> {
        let mut store = self.store.lock_py_attached(py).unwrap();
        let entry = store.entry(normalize_key(key)?);

        let res = PyList::empty(py);
        match entry {
            header::Entry::Vacant(_) => Ok(res),
            header::Entry::Occupied(mut entry) => {
                for value in entry.iter_mut() {
                    res.append(value.as_py(py))?;
                }
                Ok(res)
            }
        }
    }

    fn items<'py>(slf: PyRef<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        ItemsView {
            headers: slf.into_pyobject(py)?.unbind(),
        }
        .into_bound_py_any(py)
    }

    fn keys<'py>(slf: PyRef<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        KeysView {
            headers: slf.into_pyobject(py)?.unbind(),
        }
        .into_bound_py_any(py)
    }

    fn values<'py>(slf: PyRef<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        ValuesView {
            headers: slf.into_pyobject(py)?.unbind(),
        }
        .into_bound_py_any(py)
    }
}

#[pyclass(module = "pyqwest._headers", frozen)]
struct KeysView {
    headers: Py<Headers>,
}

#[pymethods]
impl KeysView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();
        let constants = Constants::get(py)?;
        let list = PyList::new(
            py,
            headers
                .store
                .lock_py_attached(py)
                .unwrap()
                .keys()
                .map(|key| constants.header_name(py, key)),
        )?;
        PyIterator::from_object(&list)
    }

    fn __len__(&self, py: Python<'_>) -> usize {
        let headers = self.headers.get();
        headers.store.lock_py_attached(py).unwrap().keys_len()
    }

    fn __contains__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyAny>) -> bool {
        let headers = self.headers.get();
        let Ok(key) = normalize_key(key) else {
            return false;
        };
        headers
            .store
            .lock_py_attached(py)
            .unwrap()
            .contains_key(&key)
    }
}

#[pyclass(module = "pyqwest._headers", frozen)]
struct ItemsView {
    headers: Py<Headers>,
}

#[pymethods]
impl ItemsView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();

        let constants = Constants::get(py)?;

        let mut store = headers.store.lock_py_attached(py).unwrap();

        let remaining = store.len();
        let iter = store.iter_mut().map(|(key, value)| {
            let key_py = constants.header_name(py, key);
            // PyTuple::new can't return Err for a known-sized slice with less than 2 billion elements.
            let tuple = PyTuple::new(py, [key_py.bind(py), &value.as_py(py)]).unwrap();
            tuple
        });
        let list = PyList::new(
            py,
            ExactIter {
                inner: iter,
                remaining,
            },
        )?;

        PyIterator::from_object(&list)
    }

    fn __len__(&self, py: Python<'_>) -> usize {
        let headers = self.headers.get();
        headers.store.lock_py_attached(py).unwrap().len()
    }

    fn __contains__<'py>(&self, py: Python<'py>, item: &Bound<'py, PyAny>) -> PyResult<bool> {
        let headers = self.headers.get();
        let tuple = item.cast::<PyTuple>()?;
        if tuple.len() != 2 {
            return Ok(false);
        }
        let key_py = tuple.get_item(0)?;
        let Ok(key) = normalize_key(&key_py) else {
            return Ok(false);
        };
        let value_py = tuple.get_item(1)?;
        let Ok(value) = value_py.cast::<PyString>() else {
            return Ok(false);
        };
        for stored_value in headers.store.lock_py_attached(py).unwrap().get_all(&key) {
            if stored_value.eq_str(py, value.to_str()?)? {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

#[pyclass(module = "pyqwest._headers", frozen)]
struct ValuesView {
    headers: Py<Headers>,
}

#[pymethods]
impl ValuesView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();
        let mut store = headers.store.lock_py_attached(py).unwrap();
        let remaining = store.len();
        let iter = store.values_mut().map(|value| value.as_py(py));
        let list = PyList::new(
            py,
            ExactIter {
                inner: iter,
                remaining,
            },
        )?;
        PyIterator::from_object(&list)
    }

    fn __len__(&self, py: Python<'_>) -> usize {
        let headers = self.headers.get();
        headers.store.lock_py_attached(py).unwrap().len()
    }

    fn __contains__<'py>(&self, py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<bool> {
        let Ok(value_str) = value.cast::<PyString>() else {
            return Ok(false);
        };
        let headers = self.headers.get();
        for stored_value in headers.store.lock_py_attached(py).unwrap().values() {
            if stored_value.eq_str(py, value_str.to_str()?)? {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

struct ExactIter<I> {
    inner: I,
    remaining: usize,
}

impl<I: Iterator> Iterator for ExactIter<I> {
    type Item = I::Item;

    fn next(&mut self) -> Option<Self::Item> {
        let item = self.inner.next();
        if item.is_some() {
            self.remaining -= 1;
        }
        item
    }
}

impl<I: Iterator> ExactSizeIterator for ExactIter<I> {
    fn len(&self) -> usize {
        self.remaining
    }
}

fn store_from_py(items: &Bound<'_, PyAny>) -> PyResult<HeaderMap<PyHeaderValue>> {
    let mut store: HeaderMap<PyHeaderValue> = HeaderMap::default();
    if let Ok(mapping) = items.cast::<PyMapping>() {
        for item in mapping.items()?.iter() {
            let key_py = item.get_item(0)?;
            let key = normalize_key(&key_py)?;
            let value_py = item.get_item(1)?;
            let value = value_py.cast::<PyString>()?;
            store.insert(key, PyHeaderValue::from_py(value)?);
        }
    } else {
        for item in items.try_iter()? {
            let item = item?;
            let key_py = item.get_item(0)?;
            let key = normalize_key(&key_py)?;
            let value_py = item.get_item(1)?;
            let value = value_py.cast::<PyString>()?;
            store.append(key, PyHeaderValue::from_py(value)?);
        }
    }
    Ok(store)
}

// We need to redefine equality since the values are Py<PyString> which can't be compared without
// binding.
fn stores_equal(
    py: Python<'_>,
    a: &HeaderMap<PyHeaderValue>,
    b: &HeaderMap<PyHeaderValue>,
) -> bool {
    if a.len() != b.len() {
        return false;
    }
    for key in a.keys() {
        let a_values = a.get_all(key).iter();
        let mut b_values = b.get_all(key).iter();

        for a in a_values {
            let Some(b) = b_values.next() else {
                return false;
            };
            if !a.eq(py, b) {
                return false;
            }
        }
        if b_values.next().is_some() {
            return false;
        }
    }
    true
}

enum PyHeaderValueKind {
    Py(Py<PyString>),
    Http(HeaderValue),
}

/// The string value type for headers. We know there are two sources of values,
/// the user for request headers or the HTTP response for response headers.
///
/// For request headers, we know we only convert to HTTP once when sending the request,
/// so we can store as Python from the start and never store the HTTP representation.
///
/// For response headers, we want to allow setting response headers from HTTP threads
/// but need to return them as Python strings to the user when the GIL is available.
/// We know we won't need the HTTP representation after this, so we convert once on read
/// and replace the stored value.
pub(crate) struct PyHeaderValue {
    kind: PyHeaderValueKind,
}

impl PyHeaderValue {
    fn from_http(http: HeaderValue) -> Self {
        Self {
            kind: PyHeaderValueKind::Http(http),
        }
    }

    fn from_py(s: &Bound<'_, PyString>) -> PyResult<Self> {
        // Validation copied from HeaderValue
        let s_str = s.to_str()?;
        if s_str
            .as_bytes()
            .iter()
            .any(|&b| b != b'\t' && (b < 32 || b == 127))
        {
            return Err(PyValueError::new_err(format!(
                "Invalid header value '{s_str}')"
            )));
        }
        Ok(Self {
            kind: PyHeaderValueKind::Py(s.clone().unbind()),
        })
    }

    fn as_py<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyString> {
        match &mut self.kind {
            PyHeaderValueKind::Py(py_str) => py_str.bind(py).clone(),
            PyHeaderValueKind::Http(http) => {
                let s = http.to_str().unwrap_or_default();
                let py_str = PyString::new(py, s);
                self.kind = PyHeaderValueKind::Py(py_str.clone().unbind());
                py_str
            }
        }
    }

    pub(crate) fn as_mime(&self, py: Python<'_>) -> Option<Mime> {
        match &self.kind {
            PyHeaderValueKind::Http(http) => http.to_str().unwrap_or_default().parse().ok(),
            PyHeaderValueKind::Py(py_str) => py_str.bind(py).to_str().ok()?.parse().ok(),
        }
    }

    pub(crate) fn as_http(&self, py: Python<'_>) -> PyResult<HeaderValue> {
        match &self.kind {
            PyHeaderValueKind::Http(http) => Ok(http.clone()),
            PyHeaderValueKind::Py(py_str) => {
                let s = py_str.bind(py);
                let s_str = s.to_str()?;
                let http = HeaderValue::from_str(s_str).map_err(|e| {
                    PyValueError::new_err(format!("Invalid header value '{s_str}': {e}"))
                })?;
                Ok(http)
            }
        }
    }

    fn eq_str(&self, py: Python<'_>, other: &str) -> PyResult<bool> {
        match &self.kind {
            PyHeaderValueKind::Http(http) => Ok(http.to_str().unwrap_or_default() == other),
            PyHeaderValueKind::Py(py_str) => Ok(py_str.bind(py).to_str()? == other),
        }
    }

    fn eq(&self, py: Python<'_>, other: &Self) -> bool {
        let self_str = match &self.kind {
            PyHeaderValueKind::Http(http) => http.to_str().unwrap_or_default(),
            PyHeaderValueKind::Py(py_str) => py_str.bind(py).to_str().unwrap_or_default(),
        };
        let other_str = match &other.kind {
            PyHeaderValueKind::Http(http) => http.to_str().unwrap_or_default(),
            PyHeaderValueKind::Py(py_str) => py_str.bind(py).to_str().unwrap_or_default(),
        };
        self_str == other_str
    }
}

fn normalize_key(key: &Bound<'_, PyAny>) -> PyResult<HeaderName> {
    if let Ok(header_name) = key.cast::<HttpHeaderName>() {
        return Ok(header_name.get().as_rust().clone());
    }
    let key = key.cast::<PyString>()?;
    let key_str = key.to_str()?;
    HeaderName::from_str(key.to_str()?).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid header name: '{key_str}'"))
    })
}
