use std::io::{self, Write};

use brotli::DecompressorWriter;
use pyo3::exceptions::PyRuntimeError;
use pyo3::{pyclass, pymethods, types::PyBytes, Py, PyResult, Python};
use zstd::stream::{raw as zstd_raw, zio as zstd_zio};

#[derive(Default)]
struct OutputBuffer {
    buf: Vec<u8>,
}

impl OutputBuffer {
    fn take(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.buf)
    }
}

impl Write for OutputBuffer {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buf.extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[pyclass(module = "_pyqwest.testing", name = "_BrotliDecompressor")]
pub(crate) struct BrotliDecompressor {
    dec: DecompressorWriter<OutputBuffer>,
}

#[pymethods]
impl BrotliDecompressor {
    #[new]
    fn new() -> Self {
        Self {
            dec: DecompressorWriter::new(OutputBuffer::default(), 0),
        }
    }

    fn feed(&mut self, py: Python<'_>, data: &[u8], end: bool) -> PyResult<Py<PyBytes>> {
        self.dec
            .write_all(data)
            .map_err(|e| PyRuntimeError::new_err(format!("Brotli decompression error: {e}")))?;

        if end {
            self.dec
                .close()
                .map_err(|e| PyRuntimeError::new_err(format!("Brotli decompression error: {e}")))?;
        }

        let output = self.dec.get_mut().take();
        Ok(PyBytes::new(py, &output).unbind())
    }
}

#[pyclass(module = "_pyqwest.testing", name = "_ZstdDecompressor")]
pub(crate) struct ZstdDecompressor {
    dec: zstd_zio::Writer<OutputBuffer, zstd_raw::Decoder<'static>>,
}

#[pymethods]
impl ZstdDecompressor {
    #[new]
    fn new() -> Self {
        let decoder = zstd_raw::Decoder::new().unwrap();
        Self {
            dec: zstd_zio::Writer::new(OutputBuffer::default(), decoder),
        }
    }

    fn feed(&mut self, py: Python<'_>, data: &[u8], end: bool) -> PyResult<Py<PyBytes>> {
        self.dec
            .write_all(data)
            .map_err(|e| PyRuntimeError::new_err(format!("Zstd decompression error: {e}")))?;

        if end {
            self.dec
                .finish()
                .map_err(|e| PyRuntimeError::new_err(format!("Zstd decompression error: {e}")))?;
        }

        let output = self.dec.writer_mut().take();

        Ok(PyBytes::new(py, &output).unbind())
    }
}
