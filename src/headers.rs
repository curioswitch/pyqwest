use std::str::FromStr as _;
use std::sync::Mutex;

use http::{header, HeaderMap, HeaderName};
use pyo3::exceptions::{PyKeyError, PyRuntimeError, PyTypeError};
use pyo3::sync::MutexExt as _;
use pyo3::sync::PyOnceLock;
use pyo3::types::{
    PyAnyMethods as _, PyDict, PyIterator, PyList, PyListMethods as _, PyMapping, PyString,
    PyStringMethods as _, PyTuple,
};
use pyo3::{prelude::*, IntoPyObjectExt as _};
use std::fmt::Write as _;

#[pyclass(mapping, frozen)]
pub(crate) struct Headers {
    pub(crate) store: Mutex<Option<HeaderMap<Py<PyString>>>>,
}

impl Headers {
    pub(crate) fn from_response_headers(py: Python<'_>, headers: &HeaderMap) -> Self {
        let store = store_from_http(py, headers);
        Headers {
            store: Mutex::new(Some(store)),
        }
    }

    pub(crate) fn with_store<R>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&mut HeaderMap<Py<PyString>>) -> PyResult<R>,
    ) -> PyResult<R> {
        let mut lock = self.store.lock_py_attached(py).unwrap();
        let Some(store) = &mut *lock else {
            return Err(PyRuntimeError::new_err("Trailers not received yet"));
        };
        f(store)
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
            store: Mutex::new(Some(store)),
        })
    }

    fn __getitem__<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
    ) -> PyResult<Py<PyString>> {
        let key_name = normalize_key(key)?;
        self.with_store(py, |store| {
            if let Some(value) = store.get(&key_name) {
                Ok(value.clone_ref(py))
            } else {
                Err(PyKeyError::new_err(format!(
                    "KeyError: '{}'",
                    key.to_str()?
                )))
            }
        })
    }

    fn __setitem__<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
        value: &Bound<'py, PyString>,
    ) -> PyResult<()> {
        let key = normalize_key(key)?;
        self.with_store(py, |store| {
            store.insert(key, value.clone().unbind());
            Ok(())
        })
    }

    fn __delitem__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyString>) -> PyResult<()> {
        let key_name = normalize_key(key)?;
        let key_str = key.to_str()?.to_string();
        let removed = self.with_store(py, |store| Ok(store.remove(&key_name)))?;
        if removed.is_none() {
            Err(PyKeyError::new_err(format!("KeyError: '{key_str}'")))
        } else {
            Ok(())
        }
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let names = HeaderNames::get(py);
        self.with_store(py, |store| {
            let keys = PyList::new(
                py,
                store.keys().map(|name| names.header_name_to_py(py, name)),
            )?;

            PyIterator::from_object(&keys)
        })
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        self.with_store(py, |store| Ok(store.keys_len()))
    }

    fn __contains__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<bool> {
        let Ok(key) = key.cast::<PyString>() else {
            return Ok(false);
        };
        let key = normalize_key(key)?;
        self.with_store(py, |store| Ok(store.contains_key(&key)))
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        self.with_store(py, |store| {
            if store.is_empty() {
                return Ok("Headers()".to_string());
            }
            let mut res = "Headers(".to_string();
            let mut first = true;
            for (key, value) in store {
                if !first {
                    res.push_str(", ");
                }
                let value_str = value.to_str(py)?;
                let _ = write!(res, "('{}', '{}')", key.as_str(), value_str);
                first = false;
            }
            res.push(')');
            Ok(res)
        })
    }

    fn __eq__<'py>(&self, py: Python<'py>, other: &Bound<'py, PyAny>) -> PyResult<bool> {
        if let Ok(other) = other.cast::<Headers>() {
            let other = other.get();
            if std::ptr::eq(self, &raw const *other) {
                return Ok(true);
            }
            self.with_store(py, |self_store| {
                other.with_store(py, |other_store| stores_equal(py, self_store, other_store))
            })
        } else {
            let other_store = store_from_py(other)?;
            self.with_store(py, |self_store| stores_equal(py, self_store, &other_store))
        }
    }

    #[pyo3(signature = (key, default=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyAny>,
        default: Option<Py<PyAny>>,
    ) -> PyResult<Option<Py<PyAny>>> {
        let Ok(key) = key.cast::<PyString>() else {
            return Ok(default);
        };
        let key = normalize_key(key)?;
        self.with_store(py, |store| {
            if let Some(value) = store.get(&key) {
                Ok(Some(value.clone_ref(py).into_any()))
            } else {
                Ok(default)
            }
        })
    }

    #[pyo3(signature = (key, *args))]
    fn pop<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
        args: &Bound<'py, PyTuple>,
    ) -> PyResult<Py<PyAny>> {
        if args.len() > 1 {
            return Err(PyTypeError::new_err(format!(
                "pop expected at most 2 arguments, got {}",
                1 + args.len()
            )));
        }
        let key = normalize_key(key)?;
        let removed = self.with_store(py, |store| Ok(store.remove(&key)))?;
        if let Some(value) = removed {
            Ok(value.into_any())
        } else if args.len() == 1 {
            let default = args.get_item(0)?;
            Ok(default.clone().unbind())
        } else {
            Err(PyKeyError::new_err(format!("KeyError: '{}'", key.as_str())))
        }
    }

    fn popitem(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.with_store(py, |store| {
            let Some(key) = store.keys().next() else {
                return Err(PyKeyError::new_err("Headers is empty"));
            };
            let key = key.clone();
            let names = HeaderNames::get(py);
            match store.entry(key) {
                header::Entry::Occupied(occ) => {
                    // We only want to pop off the last value, but HeaderMap's implementation means
                    // we remove them all and add back.
                    let (name, mut values) = occ.remove_entry_mult();

                    let mut result = values.next().unwrap();
                    let mut rest: Vec<Py<PyString>> = Vec::new();
                    for value in values {
                        rest.push(result);
                        result = value;
                    }

                    for value in rest {
                        store.append(name.clone(), value);
                    }
                    let key_py = names.header_name_to_py(py, &name);
                    let tuple = PyTuple::new(py, &[key_py, result])?;
                    Ok(tuple.into())
                }
                header::Entry::Vacant(_) => unreachable!(),
            }
        })
    }

    #[pyo3(signature = (key, default=None))]
    fn setdefault<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
        default: Option<&Bound<'py, PyString>>,
    ) -> PyResult<Option<Bound<'py, PyString>>> {
        let key = normalize_key(key)?;
        self.with_store(py, |store| {
            if let Some(value) = store.get(&key) {
                Ok(Some(value.bind(py).clone()))
            } else if let Some(default) = default {
                store.insert(key.clone(), default.clone().unbind());
                Ok(Some(default.clone()))
            } else {
                Ok(None)
            }
        })
    }

    fn add<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
        value: &Bound<'py, PyString>,
    ) -> PyResult<()> {
        let key = normalize_key(key)?;
        self.with_store(py, |store| {
            store.append(key, value.clone().unbind());
            Ok(())
        })
    }

    #[pyo3(signature = (items=None, **kwargs))]
    fn update<'py>(
        &self,
        py: Python<'py>,
        items: Option<Bound<'py, PyAny>>,
        kwargs: Option<&Bound<'py, PyDict>>,
    ) -> PyResult<()> {
        self.with_store(py, |store| {
            if let Some(items) = items {
                if let Ok(mapping) = items.cast::<PyMapping>() {
                    for item in mapping.items()?.iter() {
                        let key_py = item.get_item(0)?;
                        let key = key_py.cast::<PyString>()?;
                        let value_py = item.get_item(1)?;
                        let value = value_py.cast::<PyString>()?;
                        store.insert(normalize_key(key)?, value.clone().unbind());
                    }
                } else {
                    for item in items.try_iter()? {
                        let item = item?;
                        let key_py = item.get_item(0)?;
                        let key = key_py.cast::<PyString>()?;
                        let value_py = item.get_item(1)?;
                        let value = value_py.cast::<PyString>()?;
                        store.insert(normalize_key(key)?, value.clone().unbind());
                    }
                }
            }
            if let Some(kwargs) = kwargs {
                for (key_py, value_py) in kwargs.iter() {
                    let key = key_py.cast::<PyString>()?;
                    let value = value_py.cast::<PyString>()?;
                    store.insert(normalize_key(key)?, value.clone().unbind());
                }
            }
            Ok(())
        })
    }

    fn clear(&self, py: Python<'_>) -> PyResult<()> {
        self.with_store(py, |store| {
            store.clear();
            Ok(())
        })
    }

    fn getall<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
    ) -> PyResult<Bound<'py, PyList>> {
        let key = normalize_key(key)?;
        self.with_store(py, |store| {
            let values = store.get_all(&key);
            let res = PyList::empty(py);
            for value in values {
                res.append(value.clone_ref(py))?;
            }
            Ok(res)
        })
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

#[pyclass(frozen)]
struct KeysView {
    headers: Py<Headers>,
}

#[pymethods]
impl KeysView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();
        let names = HeaderNames::get(py);
        headers.with_store(py, |store| {
            let list = PyList::new(py, store.keys().map(|key| names.header_name_to_py(py, key)))?;
            PyIterator::from_object(&list)
        })
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        let headers = self.headers.get();
        headers.with_store(py, |store| Ok(store.keys_len()))
    }

    fn __contains__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<bool> {
        let headers = self.headers.get();
        let Ok(key) = key.cast::<PyString>() else {
            return Ok(false);
        };
        let key = normalize_key(key)?;
        headers.with_store(py, |store| Ok(store.contains_key(&key)))
    }
}

#[pyclass(frozen)]
struct ItemsView {
    headers: Py<Headers>,
}

#[pymethods]
impl ItemsView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();

        let names = HeaderNames::get(py);

        headers.with_store(py, |store| {
            let iter = store.iter().map(|(key, value)| {
                let key_py = names.header_name_to_py(py, key);
                // PyTuple::new can't return Err for a known-sized slice with less than 2 billion elements.
                let tuple = PyTuple::new(py, &[key_py, value.clone_ref(py)]).unwrap();
                tuple
            });
            let remaining = store.len();
            let list = PyList::new(
                py,
                ExactIter {
                    inner: iter,
                    remaining,
                },
            )?;

            PyIterator::from_object(&list)
        })
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        let headers = self.headers.get();
        headers.with_store(py, |store| Ok(store.len()))
    }

    fn __contains__<'py>(&self, py: Python<'py>, item: &Bound<'py, PyAny>) -> PyResult<bool> {
        let headers = self.headers.get();
        let tuple = item.cast::<PyTuple>()?;
        if tuple.len() != 2 {
            return Ok(false);
        }
        let key_py = tuple.get_item(0)?;
        let Ok(key) = key_py.cast::<PyString>() else {
            return Ok(false);
        };
        let value_py = tuple.get_item(1)?;
        let Ok(value) = value_py.cast::<PyString>() else {
            return Ok(false);
        };
        let key = normalize_key(key)?;
        headers.with_store(py, |store| {
            for stored_value in store.get_all(&key) {
                let stored_value = stored_value.bind(py).as_any();
                if stored_value.eq(value)? {
                    return Ok(true);
                }
            }
            Ok(false)
        })
    }
}

#[pyclass(frozen)]
struct ValuesView {
    headers: Py<Headers>,
}

#[pymethods]
impl ValuesView {
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        let headers = self.headers.get();
        headers.with_store(py, |store| {
            let iter = store.values();
            let remaining = store.len();
            let list = PyList::new(
                py,
                ExactIter {
                    inner: iter,
                    remaining,
                },
            )?;
            PyIterator::from_object(&list)
        })
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        let headers = self.headers.get();
        headers.with_store(py, |store| Ok(store.len()))
    }

    fn __contains__<'py>(&self, py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<bool> {
        let headers = self.headers.get();
        headers.with_store(py, |store| {
            for stored_value in store.values() {
                let stored_value = stored_value.bind(py).as_any();
                if stored_value.eq(value)? {
                    return Ok(true);
                }
            }
            Ok(false)
        })
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

fn store_from_http(py: Python<'_>, headers: &HeaderMap) -> HeaderMap<Py<PyString>> {
    let mut store: HeaderMap<Py<PyString>> = HeaderMap::with_capacity(headers.len());
    for (key, value) in headers {
        if let Ok(value_str) = value.to_str() {
            store.append(key.clone(), PyString::new(py, value_str).unbind());
        }
    }
    store
}

fn store_from_py(items: &Bound<'_, PyAny>) -> PyResult<HeaderMap<Py<PyString>>> {
    let mut store: HeaderMap<Py<PyString>> = HeaderMap::default();
    if let Ok(mapping) = items.cast::<PyMapping>() {
        for item in mapping.items()?.iter() {
            let key_py = item.get_item(0)?;
            let key = key_py.cast::<PyString>()?;
            let value_py = item.get_item(1)?;
            let value = value_py.cast::<PyString>()?;
            store.insert(normalize_key(key)?, value.clone().unbind());
        }
    } else {
        for item in items.try_iter()? {
            let item = item?;
            let key_py = item.get_item(0)?;
            let key = key_py.cast::<PyString>()?;
            let value_py = item.get_item(1)?;
            let value = value_py.cast::<PyString>()?;
            store.append(normalize_key(key)?, value.clone().unbind());
        }
    }
    Ok(store)
}

// We need to redefine equality since the values are Py<PyString> which can't be compared without
// binding.
fn stores_equal(
    py: Python<'_>,
    a: &HeaderMap<Py<PyString>>,
    b: &HeaderMap<Py<PyString>>,
) -> PyResult<bool> {
    if a.len() != b.len() {
        return Ok(false);
    }
    for key in a.keys() {
        let a_values = a.get_all(key).iter();
        let mut b_values = b.get_all(key).iter();

        for a in a_values {
            let Some(b) = b_values.next() else {
                return Ok(false);
            };
            if a.to_str(py)? != b.to_str(py)? {
                return Ok(false);
            }
        }
        if b_values.next().is_some() {
            return Ok(false);
        }
    }
    Ok(true)
}

fn normalize_key(key: &Bound<'_, PyString>) -> PyResult<HeaderName> {
    let key_str = key.to_str()?;
    HeaderName::from_str(key.to_str()?).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err(format!("Invalid header name: '{key_str}'"))
    })
}

static HEADER_NAMES: PyOnceLock<HeaderNames> = PyOnceLock::new();

#[pyclass]
struct HeaderNames {
    /// The string "accept"
    accept: Py<PyString>,
    /// The string "accept-charset"
    accept_charset: Py<PyString>,
    /// The string "accept-encoding"
    accept_encoding: Py<PyString>,
    /// The string "accept-language"
    accept_language: Py<PyString>,
    /// The string "accept-ranges"
    accept_ranges: Py<PyString>,
    /// The string "access-control-allow-credentials"
    access_control_allow_credentials: Py<PyString>,
    /// The string "access-control-allow-headers"
    access_control_allow_headers: Py<PyString>,
    /// The string "access-control-allow-methods"
    access_control_allow_methods: Py<PyString>,
    /// The string "access-control-allow-origin"
    access_control_allow_origin: Py<PyString>,
    /// The string "access-control-expose-headers"
    access_control_expose_headers: Py<PyString>,
    /// The string "access-control-max-age"
    access_control_max_age: Py<PyString>,
    /// The string "access-control-request-headers"
    access_control_request_headers: Py<PyString>,
    /// The string "access-control-request-method"
    access_control_request_method: Py<PyString>,
    /// The string "age"
    age: Py<PyString>,
    /// The string "allow"
    allow: Py<PyString>,
    /// The string "alt-svc"
    alt_svc: Py<PyString>,
    /// The string "authorization"
    authorization: Py<PyString>,
    /// The string "cache-control"
    cache_control: Py<PyString>,
    /// The string "cache-status"
    cache_status: Py<PyString>,
    /// The string "cdn-cache-control"
    cdn_cache_control: Py<PyString>,
    /// The string "connection"
    connection: Py<PyString>,
    /// The string "content-disposition"
    content_disposition: Py<PyString>,
    /// The string "content-encoding"
    content_encoding: Py<PyString>,
    /// The string "content-language"
    content_language: Py<PyString>,
    /// The string "content-length"
    content_length: Py<PyString>,
    /// The string "content-location"
    content_location: Py<PyString>,
    /// The string "content-range"
    content_range: Py<PyString>,
    /// The string "content-security-policy"
    content_security_policy: Py<PyString>,
    /// The string "content-security-policy-report-only"
    content_security_policy_report_only: Py<PyString>,
    /// The string "content-type"
    content_type: Py<PyString>,
    /// The string "cookie"
    cookie: Py<PyString>,
    /// The string "dnt"
    dnt: Py<PyString>,
    /// The string "date"
    date: Py<PyString>,
    /// The string "etag"
    etag: Py<PyString>,
    /// The string "expect"
    expect: Py<PyString>,
    /// The string "expires"
    expires: Py<PyString>,
    /// The string "forwarded"
    forwarded: Py<PyString>,
    /// The string "from"
    from: Py<PyString>,
    /// The string "host"
    host: Py<PyString>,
    /// The string "if-match"
    if_match: Py<PyString>,
    /// The string "if-modified-since"
    if_modified_since: Py<PyString>,
    /// The string "if-none-match"
    if_none_match: Py<PyString>,
    /// The string "if-range"
    if_range: Py<PyString>,
    /// The string "if-unmodified-since"
    if_unmodified_since: Py<PyString>,
    /// The string "last-modified"
    last_modified: Py<PyString>,
    /// The string "link"
    link: Py<PyString>,
    /// The string "location"
    location: Py<PyString>,
    /// The string "max-forwards"
    max_forwards: Py<PyString>,
    /// The string "origin"
    origin: Py<PyString>,
    /// The string "pragma"
    pragma: Py<PyString>,
    /// The string "proxy-authenticate"
    proxy_authenticate: Py<PyString>,
    /// The string "proxy-authorization"
    proxy_authorization: Py<PyString>,
    /// The string "public-key-pins"
    public_key_pins: Py<PyString>,
    /// The string "public-key-pins-report-only"
    public_key_pins_report_only: Py<PyString>,
    /// The string "range"
    range: Py<PyString>,
    /// The string "referer"
    referer: Py<PyString>,
    /// The string "referrer-policy"
    referrer_policy: Py<PyString>,
    /// The string "refresh"
    refresh: Py<PyString>,
    /// The string "retry-after"
    retry_after: Py<PyString>,
    /// The string "sec-websocket-accept"
    sec_websocket_accept: Py<PyString>,
    /// The string "sec-websocket-extensions"
    sec_websocket_extensions: Py<PyString>,
    /// The string "sec-websocket-key"
    sec_websocket_key: Py<PyString>,
    /// The string "sec-websocket-protocol"
    sec_websocket_protocol: Py<PyString>,
    /// The string "sec-websocket-version"
    sec_websocket_version: Py<PyString>,
    /// The string "server"
    server: Py<PyString>,
    /// The string "set-cookie"
    set_cookie: Py<PyString>,
    /// The string "strict-transport-security"
    strict_transport_security: Py<PyString>,
    /// The string "te"
    te: Py<PyString>,
    /// The string "trailer"
    trailer: Py<PyString>,
    /// The string "transfer-encoding"
    transfer_encoding: Py<PyString>,
    /// The string "user-agent"
    user_agent: Py<PyString>,
    /// The string "upgrade"
    upgrade: Py<PyString>,
    /// The string "upgrade-insecure-requests"
    upgrade_insecure_requests: Py<PyString>,
    /// The string "vary"
    vary: Py<PyString>,
    /// The string "via"
    via: Py<PyString>,
    /// The string "warning"
    warning: Py<PyString>,
    /// The string "www-authenticate"
    www_authenticate: Py<PyString>,
    /// The string "x-content-type-options"
    x_content_type_options: Py<PyString>,
    /// The string "x-dns-prefetch-control"
    x_dns_prefetch_control: Py<PyString>,
    /// The string "x-frame-options"
    x_frame_options: Py<PyString>,
    /// The string "x-xss-protection"
    x_xss_protection: Py<PyString>,
}

impl HeaderNames {
    fn new(py: Python<'_>) -> Self {
        Self {
            accept: PyString::new(py, "accept").unbind(),
            accept_charset: PyString::new(py, "accept-charset").unbind(),
            accept_encoding: PyString::new(py, "accept-encoding").unbind(),
            accept_language: PyString::new(py, "accept-language").unbind(),
            accept_ranges: PyString::new(py, "accept-ranges").unbind(),
            access_control_allow_credentials: PyString::new(py, "access-control-allow-credentials")
                .unbind(),
            access_control_allow_headers: PyString::new(py, "access-control-allow-headers")
                .unbind(),
            access_control_allow_methods: PyString::new(py, "access-control-allow-methods")
                .unbind(),
            access_control_allow_origin: PyString::new(py, "access-control-allow-origin").unbind(),
            access_control_expose_headers: PyString::new(py, "access-control-expose-headers")
                .unbind(),
            access_control_max_age: PyString::new(py, "access-control-max-age").unbind(),
            access_control_request_headers: PyString::new(py, "access-control-request-headers")
                .unbind(),
            access_control_request_method: PyString::new(py, "access-control-request-method")
                .unbind(),
            age: PyString::new(py, "age").unbind(),
            allow: PyString::new(py, "allow").unbind(),
            alt_svc: PyString::new(py, "alt-svc").unbind(),
            authorization: PyString::new(py, "authorization").unbind(),
            cache_control: PyString::new(py, "cache-control").unbind(),
            cache_status: PyString::new(py, "cache-status").unbind(),
            cdn_cache_control: PyString::new(py, "cdn-cache-control").unbind(),
            connection: PyString::new(py, "connection").unbind(),
            content_disposition: PyString::new(py, "content-disposition").unbind(),
            content_encoding: PyString::new(py, "content-encoding").unbind(),
            content_language: PyString::new(py, "content-language").unbind(),
            content_length: PyString::new(py, "content-length").unbind(),
            content_location: PyString::new(py, "content-location").unbind(),
            content_range: PyString::new(py, "content-range").unbind(),
            content_security_policy: PyString::new(py, "content-security-policy").unbind(),
            content_security_policy_report_only: PyString::new(
                py,
                "content-security-policy-report-only",
            )
            .unbind(),
            content_type: PyString::new(py, "content-type").unbind(),
            cookie: PyString::new(py, "cookie").unbind(),
            dnt: PyString::new(py, "dnt").unbind(),
            date: PyString::new(py, "date").unbind(),
            etag: PyString::new(py, "etag").unbind(),
            expect: PyString::new(py, "expect").unbind(),
            expires: PyString::new(py, "expires").unbind(),
            forwarded: PyString::new(py, "forwarded").unbind(),
            from: PyString::new(py, "from").unbind(),
            host: PyString::new(py, "host").unbind(),
            if_match: PyString::new(py, "if-match").unbind(),
            if_modified_since: PyString::new(py, "if-modified-since").unbind(),
            if_none_match: PyString::new(py, "if-none-match").unbind(),
            if_range: PyString::new(py, "if-range").unbind(),
            if_unmodified_since: PyString::new(py, "if-unmodified-since").unbind(),
            last_modified: PyString::new(py, "last-modified").unbind(),
            link: PyString::new(py, "link").unbind(),
            location: PyString::new(py, "location").unbind(),
            max_forwards: PyString::new(py, "max-forwards").unbind(),
            origin: PyString::new(py, "origin").unbind(),
            pragma: PyString::new(py, "pragma").unbind(),
            proxy_authenticate: PyString::new(py, "proxy-authenticate").unbind(),
            proxy_authorization: PyString::new(py, "proxy-authorization").unbind(),
            public_key_pins: PyString::new(py, "public-key-pins").unbind(),
            public_key_pins_report_only: PyString::new(py, "public-key-pins-report-only").unbind(),
            range: PyString::new(py, "range").unbind(),
            referer: PyString::new(py, "referer").unbind(),
            referrer_policy: PyString::new(py, "referrer-policy").unbind(),
            refresh: PyString::new(py, "refresh").unbind(),
            retry_after: PyString::new(py, "retry-after").unbind(),
            sec_websocket_accept: PyString::new(py, "sec-websocket-accept").unbind(),
            sec_websocket_extensions: PyString::new(py, "sec-websocket-extensions").unbind(),
            sec_websocket_key: PyString::new(py, "sec-websocket-key").unbind(),
            sec_websocket_protocol: PyString::new(py, "sec-websocket-protocol").unbind(),
            sec_websocket_version: PyString::new(py, "sec-websocket-version").unbind(),
            server: PyString::new(py, "server").unbind(),
            set_cookie: PyString::new(py, "set-cookie").unbind(),
            strict_transport_security: PyString::new(py, "strict-transport-security").unbind(),
            te: PyString::new(py, "te").unbind(),
            trailer: PyString::new(py, "trailer").unbind(),
            transfer_encoding: PyString::new(py, "transfer-encoding").unbind(),
            user_agent: PyString::new(py, "user-agent").unbind(),
            upgrade_insecure_requests: PyString::new(py, "upgrade-insecure-requests").unbind(),
            upgrade: PyString::new(py, "upgrade").unbind(),
            vary: PyString::new(py, "vary").unbind(),
            via: PyString::new(py, "via").unbind(),
            warning: PyString::new(py, "warning").unbind(),
            www_authenticate: PyString::new(py, "www-authenticate").unbind(),
            x_content_type_options: PyString::new(py, "x-content-type-options").unbind(),
            x_dns_prefetch_control: PyString::new(py, "x-dns-prefetch-control").unbind(),
            x_frame_options: PyString::new(py, "x-frame-options").unbind(),
            x_xss_protection: PyString::new(py, "x-xss-protection").unbind(),
        }
    }

    fn get(py: Python<'_>) -> &HeaderNames {
        HEADER_NAMES.get_or_init(py, || HeaderNames::new(py))
    }

    fn header_name_to_py(&self, py: Python<'_>, name: &HeaderName) -> Py<PyString> {
        match *name {
            header::ACCEPT => self.accept.clone_ref(py),
            header::ACCEPT_CHARSET => self.accept_charset.clone_ref(py),
            header::ACCEPT_ENCODING => self.accept_encoding.clone_ref(py),
            header::ACCEPT_LANGUAGE => self.accept_language.clone_ref(py),
            header::ACCEPT_RANGES => self.accept_ranges.clone_ref(py),
            header::ACCESS_CONTROL_ALLOW_CREDENTIALS => {
                self.access_control_allow_credentials.clone_ref(py)
            }
            header::ACCESS_CONTROL_ALLOW_HEADERS => self.access_control_allow_headers.clone_ref(py),
            header::ACCESS_CONTROL_ALLOW_METHODS => self.access_control_allow_methods.clone_ref(py),
            header::ACCESS_CONTROL_ALLOW_ORIGIN => self.access_control_allow_origin.clone_ref(py),
            header::ACCESS_CONTROL_EXPOSE_HEADERS => {
                self.access_control_expose_headers.clone_ref(py)
            }
            header::ACCESS_CONTROL_MAX_AGE => self.access_control_max_age.clone_ref(py),
            header::ACCESS_CONTROL_REQUEST_HEADERS => {
                self.access_control_request_headers.clone_ref(py)
            }
            header::ACCESS_CONTROL_REQUEST_METHOD => {
                self.access_control_request_method.clone_ref(py)
            }
            header::AGE => self.age.clone_ref(py),
            header::ALLOW => self.allow.clone_ref(py),
            header::ALT_SVC => self.alt_svc.clone_ref(py),
            header::AUTHORIZATION => self.authorization.clone_ref(py),
            header::CACHE_CONTROL => self.cache_control.clone_ref(py),
            header::CACHE_STATUS => self.cache_status.clone_ref(py),
            header::CDN_CACHE_CONTROL => self.cdn_cache_control.clone_ref(py),
            header::CONNECTION => self.connection.clone_ref(py),
            header::CONTENT_DISPOSITION => self.content_disposition.clone_ref(py),
            header::CONTENT_ENCODING => self.content_encoding.clone_ref(py),
            header::CONTENT_LANGUAGE => self.content_language.clone_ref(py),
            header::CONTENT_LENGTH => self.content_length.clone_ref(py),
            header::CONTENT_LOCATION => self.content_location.clone_ref(py),
            header::CONTENT_RANGE => self.content_range.clone_ref(py),
            header::CONTENT_SECURITY_POLICY => self.content_security_policy.clone_ref(py),
            header::CONTENT_SECURITY_POLICY_REPORT_ONLY => {
                self.content_security_policy_report_only.clone_ref(py)
            }
            header::CONTENT_TYPE => self.content_type.clone_ref(py),
            header::COOKIE => self.cookie.clone_ref(py),
            header::DNT => self.dnt.clone_ref(py),
            header::DATE => self.date.clone_ref(py),
            header::ETAG => self.etag.clone_ref(py),
            header::EXPECT => self.expect.clone_ref(py),
            header::EXPIRES => self.expires.clone_ref(py),
            header::FORWARDED => self.forwarded.clone_ref(py),
            header::FROM => self.from.clone_ref(py),
            header::HOST => self.host.clone_ref(py),
            header::IF_MATCH => self.if_match.clone_ref(py),
            header::IF_MODIFIED_SINCE => self.if_modified_since.clone_ref(py),
            header::IF_NONE_MATCH => self.if_none_match.clone_ref(py),
            header::IF_RANGE => self.if_range.clone_ref(py),
            header::IF_UNMODIFIED_SINCE => self.if_unmodified_since.clone_ref(py),
            header::LAST_MODIFIED => self.last_modified.clone_ref(py),
            header::LINK => self.link.clone_ref(py),
            header::LOCATION => self.location.clone_ref(py),
            header::MAX_FORWARDS => self.max_forwards.clone_ref(py),
            header::ORIGIN => self.origin.clone_ref(py),
            header::PRAGMA => self.pragma.clone_ref(py),
            header::PROXY_AUTHENTICATE => self.proxy_authenticate.clone_ref(py),
            header::PROXY_AUTHORIZATION => self.proxy_authorization.clone_ref(py),
            header::PUBLIC_KEY_PINS => self.public_key_pins.clone_ref(py),
            header::PUBLIC_KEY_PINS_REPORT_ONLY => self.public_key_pins_report_only.clone_ref(py),
            header::RANGE => self.range.clone_ref(py),
            header::REFERER => self.referer.clone_ref(py),
            header::REFERRER_POLICY => self.referrer_policy.clone_ref(py),
            header::REFRESH => self.refresh.clone_ref(py),
            header::RETRY_AFTER => self.retry_after.clone_ref(py),
            header::SEC_WEBSOCKET_ACCEPT => self.sec_websocket_accept.clone_ref(py),
            header::SEC_WEBSOCKET_EXTENSIONS => self.sec_websocket_extensions.clone_ref(py),
            header::SEC_WEBSOCKET_KEY => self.sec_websocket_key.clone_ref(py),
            header::SEC_WEBSOCKET_PROTOCOL => self.sec_websocket_protocol.clone_ref(py),
            header::SEC_WEBSOCKET_VERSION => self.sec_websocket_version.clone_ref(py),
            header::SERVER => self.server.clone_ref(py),
            header::SET_COOKIE => self.set_cookie.clone_ref(py),
            header::STRICT_TRANSPORT_SECURITY => self.strict_transport_security.clone_ref(py),
            header::TE => self.te.clone_ref(py),
            header::TRAILER => self.trailer.clone_ref(py),
            header::TRANSFER_ENCODING => self.transfer_encoding.clone_ref(py),
            header::USER_AGENT => self.user_agent.clone_ref(py),
            header::UPGRADE => self.upgrade.clone_ref(py),
            header::UPGRADE_INSECURE_REQUESTS => self.upgrade_insecure_requests.clone_ref(py),
            header::VARY => self.vary.clone_ref(py),
            header::VIA => self.via.clone_ref(py),
            header::WARNING => self.warning.clone_ref(py),
            header::WWW_AUTHENTICATE => self.www_authenticate.clone_ref(py),
            header::X_CONTENT_TYPE_OPTIONS => self.x_content_type_options.clone_ref(py),
            header::X_DNS_PREFETCH_CONTROL => self.x_dns_prefetch_control.clone_ref(py),
            header::X_FRAME_OPTIONS => self.x_frame_options.clone_ref(py),
            header::X_XSS_PROTECTION => self.x_xss_protection.clone_ref(py),
            _ => PyString::new(py, name.as_str()).unbind(),
        }
    }
}
