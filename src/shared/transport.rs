use std::{
    net::{IpAddr, SocketAddr},
    path::PathBuf,
    time::Duration,
};

use hyper_util::client::legacy::{with_pool_options, PoolMetrics, PoolOptions};
use pyo3::{
    exceptions::{PyRuntimeError, PyTypeError, PyValueError},
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyString, PyStringMethods as _},
    Bound, PyAny, PyResult, Python,
};

use crate::{
    common::{
        httpversion::HTTPVersion,
        proxy::{proxy_from_url, Proxy},
    },
    shared::{runtime::get_runtime, validation::validate_timeout},
};

static DEFAULT_REQWEST_CLIENT: PyOnceLock<reqwest::Client> = PyOnceLock::new();

/// The number of redirects followed by default when redirects are enabled.
pub(crate) const DEFAULT_MAX_REDIRECTS: usize = 10;

pub(crate) struct ClientParams<'a> {
    pub(crate) tls_ca_cert: Option<&'a [u8]>,
    pub(crate) tls_include_system_certs: bool,
    pub(crate) tls_key: Option<&'a [u8]>,
    pub(crate) tls_cert: Option<&'a [u8]>,
    pub(crate) http_version: Option<Bound<'a, HTTPVersion>>,
    pub(crate) proxy: Option<Bound<'a, PyAny>>,
    pub(crate) uds: Option<PathBuf>,
    pub(crate) timeout: Option<f64>,
    pub(crate) connect_timeout: Option<f64>,
    pub(crate) read_timeout: Option<f64>,
    pub(crate) pool_idle_timeout: Option<f64>,
    pub(crate) pool_max_idle_per_host: Option<usize>,
    pub(crate) max_connections_per_address: Option<usize>,
    pub(crate) tcp_keepalive_interval: Option<f64>,
    pub(crate) enable_gzip: bool,
    pub(crate) enable_brotli: bool,
    pub(crate) enable_zstd: bool,
    pub(crate) use_system_dns: bool,
    pub(crate) dns_overrides: Option<Bound<'a, PyAny>>,
    pub(crate) enable_cookie_store: bool,
    pub(crate) enable_dns_load_balancing: bool,
    pub(crate) follow_redirects: bool,
    pub(crate) max_redirects: usize,
    /// Reports the client's connection pool metrics.
    pub(crate) pool_metrics: Option<PoolMetrics>,
}

pub(crate) fn new_reqwest_client(params: ClientParams) -> PyResult<(reqwest::Client, bool)> {
    let (mut builder, pool_options) = configure_pool(reqwest::Client::builder(), &params)?;
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
        let certs = reqwest::Certificate::from_pem_bundle(ca_cert)
            .map_err(|e| PyValueError::new_err(format!("Failed to parse CA certificate: {e}")))?;
        if certs.is_empty() {
            return Err(PyValueError::new_err(
                "tls_ca_cert did not contain any PEM certificates",
            ));
        }
        if params.tls_include_system_certs {
            builder = builder.tls_certs_merge(certs);
        } else {
            builder = builder.tls_certs_only(certs);
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
    let mut has_proxy = false;
    if let Some(proxy) = params.proxy {
        for proxy in proxies_from_py(&proxy)? {
            has_proxy = true;
            builder = builder.proxy(proxy);
        }
    }
    if let Some(uds) = params.uds {
        builder = configure_uds(builder, uds, http3, has_proxy)?;
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
    if let Some(tcp_keepalive_interval) = validate_timeout(params.tcp_keepalive_interval)? {
        builder = builder.tcp_keepalive_interval(Duration::from_secs_f64(tcp_keepalive_interval));
    }
    builder = builder.gzip(params.enable_gzip);
    builder = builder.brotli(params.enable_brotli);
    builder = builder.zstd(params.enable_zstd);
    builder = builder.hickory_dns(!params.use_system_dns);
    builder = builder.cookie_store(params.enable_cookie_store);
    builder = builder.redirect(if params.follow_redirects {
        reqwest::redirect::Policy::limited(params.max_redirects)
    } else {
        reqwest::redirect::Policy::none()
    });

    // reqwest builds hyper-util's client inside `build()`, on this thread;
    // the forked pool reads these options from there.
    let client = with_pool_options(pool_options, || {
        if http3 {
            // Workaround https://github.com/seanmonstar/reqwest/issues/2910
            let _guard = get_runtime().enter();
            builder.build()
        } else {
            builder.build()
        }
    })
    .map_err(|e| {
        PyRuntimeError::new_err(format!("Failed to create client: {:+}", errors::fmt(&e)))
    })?;
    Ok((client, http3))
}

/// Applies the options that decide how connections are kept, counted, and
/// spread over the addresses a host resolves to. The ones reqwest has no
/// builder method for come back as `PoolOptions` for the forked pool.
fn configure_pool(
    mut builder: reqwest::ClientBuilder,
    params: &ClientParams,
) -> PyResult<(reqwest::ClientBuilder, PoolOptions)> {
    if let Some(idle_connection_timeout) = validate_timeout(params.pool_idle_timeout)? {
        builder = builder.pool_idle_timeout(Duration::from_secs_f64(idle_connection_timeout));
    } else {
        builder = builder.pool_idle_timeout(None);
    }
    if let Some(max_idle_connections_per_host) = params.pool_max_idle_per_host {
        builder = builder.pool_max_idle_per_host(max_idle_connections_per_host);
    }
    if params.max_connections_per_address == Some(0) {
        return Err(PyValueError::new_err(
            "max_connections_per_address must be positive",
        ));
    }
    if let Some(dns_overrides) = &params.dns_overrides {
        for (host, addrs) in dns_overrides_from_py(dns_overrides)? {
            builder = builder.resolve_to_addrs(&host, &addrs);
        }
    }
    let pool_options = PoolOptions {
        max_connections_per_address: params.max_connections_per_address,
        dns_load_balancing: Some(params.enable_dns_load_balancing),
        metrics: params.pool_metrics.clone(),
    };
    Ok((builder, pool_options))
}

/// Connects every request over the Unix socket at `uds`, which replaces DNS
/// resolution, TCP, and proxies.
fn configure_uds(
    builder: reqwest::ClientBuilder,
    uds: PathBuf,
    http3: bool,
    has_proxy: bool,
) -> PyResult<reqwest::ClientBuilder> {
    if http3 {
        return Err(PyValueError::new_err("uds cannot be used with HTTP/3"));
    }
    if has_proxy {
        return Err(PyValueError::new_err("uds cannot be used with proxy"));
    }
    #[cfg(unix)]
    {
        Ok(builder.unix_socket(uds))
    }
    #[cfg(not(unix))]
    {
        _ = (builder, uds);
        Err(PyValueError::new_err("uds is only supported on Unix"))
    }
}

fn dns_override_addr(addr: &str) -> PyResult<SocketAddr> {
    if let Ok(addr) = addr.parse::<SocketAddr>() {
        return Ok(addr);
    }
    // Without a port, the port of each request's URL is used.
    addr.parse::<IpAddr>()
        .map(|ip| SocketAddr::new(ip, 0))
        .map_err(|_| PyValueError::new_err(format!("Invalid dns_overrides address: {addr}")))
}

fn dns_overrides_from_py(overrides: &Bound<'_, PyAny>) -> PyResult<Vec<(String, Vec<SocketAddr>)>> {
    let mut result = Vec::new();
    for (host, addrs) in overrides.cast::<PyDict>()?.iter() {
        let mut parsed = Vec::new();
        for addr in addrs.try_iter()? {
            parsed.push(dns_override_addr(&addr?.extract::<String>()?)?);
        }
        result.push((host.extract::<String>()?, parsed));
    }
    Ok(result)
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
    let mut proxies = Vec::new();
    for item in proxy.try_iter()? {
        proxies.push(proxy_from_item(&item?)?);
    }
    Ok(proxies)
}

/// The reqwest client shared by the default transports. It is built once, by
/// whichever default transport is created first.
pub(crate) fn get_default_reqwest_client(
    py: Python<'_>,
    pool_metrics: Option<PoolMetrics>,
) -> reqwest::Client {
    DEFAULT_REQWEST_CLIENT
        .get_or_init(py, || {
            let (client, _) = new_reqwest_client(ClientParams {
                tls_ca_cert: None,
                tls_key: None,
                tls_cert: None,
                tls_include_system_certs: true,
                http_version: None,
                proxy: None,
                uds: None,
                timeout: None,
                connect_timeout: Some(30.0),
                read_timeout: None,
                pool_idle_timeout: Some(90.0),
                pool_max_idle_per_host: None,
                max_connections_per_address: None,
                tcp_keepalive_interval: Some(30.0),
                enable_gzip: true,
                enable_brotli: true,
                enable_zstd: true,
                use_system_dns: false,
                dns_overrides: None,
                enable_cookie_store: false,
                enable_dns_load_balancing: false,
                follow_redirects: true,
                max_redirects: DEFAULT_MAX_REDIRECTS,
                pool_metrics,
            })
            .unwrap();
            client
        })
        .clone()
}
