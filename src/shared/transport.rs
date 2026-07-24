use std::time::Duration;

use pyo3::{
    exceptions::{PyRuntimeError, PyTypeError, PyValueError},
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyByteArray, PyBytes, PyString, PyStringMethods as _},
    Bound, PyAny, PyResult, Python,
};
use pyo3_async_runtimes::tokio::get_runtime;

use crate::{
    common::{
        httpversion::HTTPVersion,
        proxy::{proxy_from_url, Proxy},
    },
    shared::validation::validate_timeout,
};

static DEFAULT_REQWEST_CLIENT: PyOnceLock<reqwest::Client> = PyOnceLock::new();

pub(crate) struct ClientParams<'a> {
    pub(crate) tls_ca_cert: Option<&'a [u8]>,
    pub(crate) tls_include_system_certs: bool,
    pub(crate) tls_key: Option<&'a [u8]>,
    pub(crate) tls_cert: Option<&'a [u8]>,
    pub(crate) http_version: Option<Bound<'a, HTTPVersion>>,
    pub(crate) proxy: Option<Bound<'a, PyAny>>,
    pub(crate) timeout: Option<f64>,
    pub(crate) connect_timeout: Option<f64>,
    pub(crate) read_timeout: Option<f64>,
    pub(crate) pool_idle_timeout: Option<f64>,
    pub(crate) pool_max_idle_per_host: Option<usize>,
    pub(crate) tcp_keepalive_interval: Option<f64>,
    pub(crate) enable_gzip: bool,
    pub(crate) enable_brotli: bool,
    pub(crate) enable_zstd: bool,
    pub(crate) use_system_dns: bool,
    pub(crate) enable_cookie_store: bool,
}

pub(crate) fn new_reqwest_client(params: ClientParams) -> PyResult<(reqwest::Client, bool)> {
    let mut builder = reqwest::Client::builder();
    let mut http3 = false;
    if let Some(http_version) = params.http_version {
        let http_version = http_version.get().as_rust();
        match http_version {
            http::version::Version::HTTP_2 => {
                builder = builder.http2_prior_knowledge();
            }
            http::version::Version::HTTP_3 => {
                http3 = true;
                builder = builder.http3_prior_knowledge();
            }
            _ => {
                builder = builder.http1_only();
            }
        }
    }
    if let Some(ca_cert) = params.tls_ca_cert {
        let cert = reqwest::Certificate::from_pem(ca_cert)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to parse CA certificate: {e}")))?;
        if params.tls_include_system_certs {
            builder = builder.tls_certs_merge([cert]);
        } else {
            builder = builder.tls_certs_only([cert]);
        }
    } else if !params.tls_include_system_certs {
        builder = builder.tls_certs_only([]);
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
    if let Some(proxy) = params.proxy {
        for proxy in proxies_from_py(&proxy)? {
            builder = builder.proxy(proxy);
        }
    }

    if let Some(timeout) = validate_timeout(params.timeout)? {
        builder = builder.timeout(Duration::from_secs_f64(timeout));
    }
    if let Some(connect_timeout) = validate_timeout(params.connect_timeout)? {
        builder = builder.connect_timeout(Duration::from_secs_f64(connect_timeout));
    }
    if let Some(read_timeout) = validate_timeout(params.read_timeout)? {
        builder = builder.read_timeout(Duration::from_secs_f64(read_timeout));
    }
    if let Some(idle_connection_timeout) = validate_timeout(params.pool_idle_timeout)? {
        builder = builder.pool_idle_timeout(Duration::from_secs_f64(idle_connection_timeout));
    } else {
        builder = builder.pool_idle_timeout(None);
    }
    if let Some(max_idle_connections_per_host) = params.pool_max_idle_per_host {
        builder = builder.pool_max_idle_per_host(max_idle_connections_per_host);
    }
    if let Some(tcp_keepalive_interval) = validate_timeout(params.tcp_keepalive_interval)? {
        builder = builder.tcp_keepalive_interval(Duration::from_secs_f64(tcp_keepalive_interval));
    }
    builder = builder.gzip(params.enable_gzip);
    builder = builder.brotli(params.enable_brotli);
    builder = builder.zstd(params.enable_zstd);
    builder = builder.hickory_dns(!params.use_system_dns);
    builder = builder.cookie_store(params.enable_cookie_store);

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

const PROXY_TYPE_ERROR: &str = "proxy must be a str, Proxy, or sequence of str | Proxy";

fn proxy_from_item(item: &Bound<'_, PyAny>) -> PyResult<reqwest::Proxy> {
    if let Ok(url) = item.cast::<PyString>() {
        return proxy_from_url(url.to_str()?);
    }
    if let Ok(proxy) = item.cast::<Proxy>() {
        return Ok(proxy.get().as_reqwest());
    }
    Err(PyTypeError::new_err(PROXY_TYPE_ERROR))
}

fn proxies_from_py(proxy: &Bound<'_, PyAny>) -> PyResult<Vec<reqwest::Proxy>> {
    if proxy.cast::<PyString>().is_ok() || proxy.cast::<Proxy>().is_ok() {
        return Ok(vec![proxy_from_item(proxy)?]);
    }
    // Mappings iterate as their keys and byte strings as ints, so reject them
    // before falling through to iteration rather than misreading them as
    // sequences of proxies.
    if proxy.hasattr("keys")?
        || proxy.cast::<PyBytes>().is_ok()
        || proxy.cast::<PyByteArray>().is_ok()
    {
        return Err(PyTypeError::new_err(PROXY_TYPE_ERROR));
    }
    let items = proxy
        .try_iter()
        .map_err(|_| PyTypeError::new_err(PROXY_TYPE_ERROR))?;
    let mut proxies = Vec::new();
    for item in items {
        proxies.push(proxy_from_item(&item?)?);
    }
    Ok(proxies)
}

pub(crate) fn get_default_reqwest_client(py: Python<'_>) -> reqwest::Client {
    DEFAULT_REQWEST_CLIENT
        .get_or_init(py, || {
            let (client, _) = new_reqwest_client(ClientParams {
                tls_ca_cert: None,
                tls_key: None,
                tls_cert: None,
                tls_include_system_certs: true,
                http_version: None,
                proxy: None,
                timeout: None,
                connect_timeout: Some(30.0),
                read_timeout: None,
                pool_idle_timeout: Some(90.0),
                pool_max_idle_per_host: None,
                tcp_keepalive_interval: Some(30.0),
                enable_gzip: true,
                enable_brotli: true,
                enable_zstd: true,
                use_system_dns: false,
                enable_cookie_store: false,
            })
            .unwrap();
            client
        })
        .clone()
}
