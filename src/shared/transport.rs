use std::{sync::Arc, time::Duration};

use pyo3::{
    exceptions::{PyRuntimeError, PyTypeError, PyValueError},
    sync::PyOnceLock,
    types::{PyAnyMethods as _, PyString, PyStringMethods as _},
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
    pub(crate) follow_redirects: bool,
    pub(crate) max_redirects: usize,
}

/// The validated, owned form of `ClientParams`: everything needed to build a
/// reqwest client. A transport keeps it so that balancing over several
/// connections can build more clients identical to the first.
pub(crate) struct ClientConfig {
    http_version: Option<http::Version>,
    tls_certs: Option<Vec<reqwest::Certificate>>,
    tls_include_system_certs: bool,
    identity: Option<reqwest::Identity>,
    proxies: Vec<reqwest::Proxy>,
    timeout: Option<Duration>,
    connect_timeout: Option<Duration>,
    read_timeout: Option<Duration>,
    pool_idle_timeout: Option<Duration>,
    pool_max_idle_per_host: Option<usize>,
    tcp_keepalive_interval: Option<Duration>,
    enable_gzip: bool,
    enable_brotli: bool,
    enable_zstd: bool,
    use_system_dns: bool,
    /// One jar for every client built from this config, so cookies stored
    /// through one connection are sent on all of them.
    cookie_jar: Option<Arc<reqwest::cookie::Jar>>,
    /// The redirect limit, or `None` to return redirects as-is.
    max_redirects: Option<usize>,
}

impl ClientConfig {
    pub(crate) fn new(params: ClientParams) -> PyResult<Self> {
        let http_version = params
            .http_version
            .map(|http_version| http_version.get().as_rust());
        let tls_certs = match params.tls_ca_cert {
            Some(ca_cert) => {
                let certs = reqwest::Certificate::from_pem_bundle(ca_cert).map_err(|e| {
                    PyValueError::new_err(format!("Failed to parse CA certificate: {e}"))
                })?;
                if certs.is_empty() {
                    return Err(PyValueError::new_err(
                        "tls_ca_cert did not contain any PEM certificates",
                    ));
                }
                Some(certs)
            }
            None => None,
        };
        let identity = match (params.tls_cert, params.tls_key) {
            (Some(cert), Some(key)) => {
                let pem = [cert, key].concat();
                Some(reqwest::Identity::from_pem(&pem).map_err(|e| {
                    PyValueError::new_err(format!("Failed to parse tls_cert/key: {e}"))
                })?)
            }
            (None, None) => None,
            _ => {
                return Err(PyValueError::new_err(
                    "Both tls_key and tls_cert must be provided",
                ));
            }
        };
        let proxies = match params.proxy {
            Some(proxy) => proxies_from_py(&proxy)?,
            None => Vec::new(),
        };
        let duration = |secs: Option<f64>| -> PyResult<Option<Duration>> {
            Ok(validate_timeout(secs)?.map(Duration::from_secs_f64))
        };
        Ok(Self {
            http_version,
            tls_certs,
            tls_include_system_certs: params.tls_include_system_certs,
            identity,
            proxies,
            timeout: duration(params.timeout)?,
            connect_timeout: duration(params.connect_timeout)?,
            read_timeout: duration(params.read_timeout)?,
            pool_idle_timeout: duration(params.pool_idle_timeout)?,
            pool_max_idle_per_host: params.pool_max_idle_per_host,
            tcp_keepalive_interval: duration(params.tcp_keepalive_interval)?,
            enable_gzip: params.enable_gzip,
            enable_brotli: params.enable_brotli,
            enable_zstd: params.enable_zstd,
            use_system_dns: params.use_system_dns,
            cookie_jar: params
                .enable_cookie_store
                .then(|| Arc::new(reqwest::cookie::Jar::default())),
            max_redirects: params.follow_redirects.then_some(params.max_redirects),
        })
    }

    /// Whether clients built from this config speak HTTP/3.
    pub(crate) fn http3(&self) -> bool {
        self.http_version == Some(http::version::Version::HTTP_3)
    }

    /// Builds a client. Every call returns a client with its own connection
    /// pool, sharing only the cookie store.
    pub(crate) fn build(&self) -> PyResult<reqwest::Client> {
        let mut builder = reqwest::Client::builder();
        match self.http_version {
            Some(http::version::Version::HTTP_2) => {
                builder = builder.http2_prior_knowledge();
            }
            Some(http::version::Version::HTTP_3) => {
                builder = builder.http3_prior_knowledge();
            }
            Some(_) => {
                builder = builder.http1_only();
            }
            None => (),
        }
        match (&self.tls_certs, self.tls_include_system_certs) {
            (Some(certs), true) => builder = builder.tls_certs_merge(certs.iter().cloned()),
            (Some(certs), false) => builder = builder.tls_certs_only(certs.iter().cloned()),
            (None, true) => (),
            (None, false) => builder = builder.tls_certs_only([]),
        }
        if let Some(identity) = &self.identity {
            builder = builder.identity(identity.clone());
        }
        for proxy in &self.proxies {
            builder = builder.proxy(proxy.clone());
        }
        if let Some(timeout) = self.timeout {
            builder = builder.timeout(timeout);
        }
        if let Some(connect_timeout) = self.connect_timeout {
            builder = builder.connect_timeout(connect_timeout);
        }
        if let Some(read_timeout) = self.read_timeout {
            builder = builder.read_timeout(read_timeout);
        }
        builder = builder.pool_idle_timeout(self.pool_idle_timeout);
        if let Some(max_idle_connections_per_host) = self.pool_max_idle_per_host {
            builder = builder.pool_max_idle_per_host(max_idle_connections_per_host);
        }
        if let Some(tcp_keepalive_interval) = self.tcp_keepalive_interval {
            builder = builder.tcp_keepalive_interval(tcp_keepalive_interval);
        }
        builder = builder.gzip(self.enable_gzip);
        builder = builder.brotli(self.enable_brotli);
        builder = builder.zstd(self.enable_zstd);
        builder = builder.hickory_dns(!self.use_system_dns);
        builder = match &self.cookie_jar {
            Some(jar) => builder.cookie_provider(jar.clone()),
            None => builder.cookie_store(false),
        };
        builder = builder.redirect(match self.max_redirects {
            Some(max_redirects) => reqwest::redirect::Policy::limited(max_redirects),
            None => reqwest::redirect::Policy::none(),
        });

        if self.http3() {
            // Workaround https://github.com/seanmonstar/reqwest/issues/2910
            let _guard = get_runtime().enter();
            builder.build()
        } else {
            builder.build()
        }
        .map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to create client: {:+}", errors::fmt(&e)))
        })
    }
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

pub(crate) fn get_default_reqwest_client(py: Python<'_>) -> reqwest::Client {
    DEFAULT_REQWEST_CLIENT
        .get_or_init(py, || {
            ClientConfig::new(ClientParams {
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
                follow_redirects: true,
                max_redirects: DEFAULT_MAX_REDIRECTS,
            })
            .and_then(|config| config.build())
            .unwrap()
        })
        .clone()
}
