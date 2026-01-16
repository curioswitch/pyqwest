// Includes work from:
//
// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

use std::ffi::c_int;

use bytes::Bytes;
use pyo3::{
    ffi, pyclass, pymethods, types::PyMemoryView, Bound, IntoPyObject, IntoPyObjectExt as _, PyErr,
    PyRef, PyResult, Python,
};

/// A type that can be created without the GIL that will be converted to
/// a memoryview of a buffer wrapping the `Bytes` when passed to Python.
pub struct BytesMemoryView {
    data: Bytes,
}

impl BytesMemoryView {
    pub(crate) fn new(data: Bytes) -> Self {
        Self { data }
    }
}

impl<'py> IntoPyObject<'py> for BytesMemoryView {
    type Target = PyMemoryView;
    type Output = Bound<'py, PyMemoryView>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let buffer = Buffer::new(self.data).into_bound_py_any(py)?;
        PyMemoryView::from(&buffer)
    }
}

// Mostly same as https://github.com/apache/opendal/blob/d001321b0f9834bc1e2e7d463bcfdc3683e968c9/bindings/python/src/utils.rs#L51-L72

#[pyclass(module = "_pyqwest.shared", frozen)]
struct Buffer {
    data: Bytes,
}

impl Buffer {
    pub(crate) fn new(data: Bytes) -> Self {
        Self { data }
    }
}

#[pymethods]
impl Buffer {
    #[allow(clippy::needless_pass_by_value)]
    unsafe fn __getbuffer__(
        slf: PyRef<Self>,
        view: *mut ffi::Py_buffer,
        flags: c_int,
    ) -> PyResult<()> {
        let bytes = &slf.data;
        let ret = ffi::PyBuffer_FillInfo(
            view,
            slf.as_ptr().cast(),
            bytes.as_ptr() as *mut _,
            bytes.len().try_into().unwrap(),
            1, // read only
            flags,
        );
        if ret == -1 {
            return Err(PyErr::fetch(slf.py()));
        }
        Ok(())
    }
}
