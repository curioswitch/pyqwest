use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    Bound, PyResult,
};
use pyo3_async_runtimes::tokio::get_runtime;

use crate::common::HTTPVersion;

pub(crate) struct ClientParams<'a> {
    pub(crate) tls_ca_cert: Option<&'a [u8]>,
    pub(crate) tls_key: Option<&'a [u8]>,
    pub(crate) tls_cert: Option<&'a [u8]>,
    pub(crate) http_version: Option<Bound<'a, HTTPVersion>>,
}

pub(crate) fn new_reqwest_client(params: ClientParams) -> PyResult<(reqwest::Client, bool)> {
    let mut builder = reqwest::Client::builder();
    let mut http3 = false;
    if let Some(http_version) = params.http_version {
        let http_version = http_version.get();
        match http_version {
            HTTPVersion::HTTP1 => {
                builder = builder.http1_only();
            }
            HTTPVersion::HTTP2 => {
                builder = builder.http2_prior_knowledge();
            }
            HTTPVersion::HTTP3 => {
                http3 = true;
                builder = builder.http3_prior_knowledge();
            }
        }
    }
    if let Some(ca_cert) = params.tls_ca_cert {
        let cert = reqwest::Certificate::from_pem(ca_cert)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to parse CA certificate: {e}")))?;
        builder = builder.tls_certs_only([cert]);
    }
    if let (Some(cert), Some(key)) = (params.tls_cert, params.tls_key) {
        let pem = [cert, key].concat();
        let identity = reqwest::Identity::from_pem(&pem)
            .map_err(|e| PyValueError::new_err(format!("Failed to parse tls_cert/key: {e}")))?;
        builder = builder.identity(identity);
    } else if params.tls_cert.is_some() || params.tls_key.is_some() {
        return Err(PyValueError::new_err(
            "Both tls_key and tls_cert must be provided",
        ));
    }

    let client = if http3 {
        // Workaround https://github.com/seanmonstar/reqwest/issues/2910
        let _guard = get_runtime().enter();
        builder.build()
    } else {
        builder.build()
    }
    .map_err(|e| {
        PyRuntimeError::new_err(format!("Failed to create client: {:+}", errors::fmt(&e)))
    })?;
    Ok((client, http3))
}
