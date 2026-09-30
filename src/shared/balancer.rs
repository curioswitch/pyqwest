//! Balancing of requests over several identical reqwest clients.
//!
//! hyper's pool keeps one HTTP/2 connection per origin and, once the server's
//! `SETTINGS_MAX_CONCURRENT_STREAMS` is reached, queues further requests on it
//! until a stream ends (hyperium/hyper#3623). Long-lived streams turn that
//! into a per-process concurrency ceiling. A `ClientSet` with balancing
//! enabled works around it at the client layer, like undici's
//! `Agent({ connections })`: every client has its own pool, so its own
//! connection per origin, and a request goes to the client with the fewest
//! requests in flight, adding a client when they are all at the stream limit.

use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc, Mutex, PoisonError,
};

use pyo3::{exceptions::PyValueError, PyResult};

use crate::shared::transport::ClientConfig;

/// The clients a transport sends requests with.
pub(crate) struct ClientSet {
    inner: Inner,
}

enum Inner {
    /// One client, as before balancing existed: no accounting at all.
    Single(reqwest::Client),
    Balanced(Box<Balanced>),
}

struct Balanced {
    config: ClientConfig,
    max_streams_per_connection: usize,
    max_connections: Option<usize>,
    /// The clients in creation order, locked so that choosing one and
    /// counting a request on it is atomic against concurrent requests.
    slots: Mutex<Vec<Arc<Slot>>>,
}

pub(crate) struct Slot {
    client: reqwest::Client,
    in_flight: AtomicUsize,
}

/// A request's hold on a slot while the request is in flight. Whoever owns
/// it, the request future until the response arrives and the response body
/// after, releases the hold by dropping it.
pub(crate) struct InFlight {
    slot: Arc<Slot>,
}

impl Drop for InFlight {
    fn drop(&mut self) {
        self.slot.in_flight.fetch_sub(1, Ordering::AcqRel);
    }
}

impl ClientSet {
    /// A set of one client and no accounting.
    pub(crate) fn single(client: reqwest::Client) -> Self {
        Self {
            inner: Inner::Single(client),
        }
    }

    /// A set built from `config`, balancing when `max_streams_per_connection`
    /// is given. The first client is built now, further ones as needed.
    pub(crate) fn new(
        config: ClientConfig,
        max_streams_per_connection: Option<usize>,
        max_connections: Option<usize>,
    ) -> PyResult<Self> {
        if max_streams_per_connection == Some(0) {
            return Err(PyValueError::new_err(
                "max_streams_per_connection must be positive",
            ));
        }
        if max_connections == Some(0) {
            return Err(PyValueError::new_err("max_connections must be positive"));
        }
        if max_streams_per_connection.is_none() && max_connections.is_some() {
            return Err(PyValueError::new_err(
                "max_connections requires max_streams_per_connection",
            ));
        }
        let client = config.build()?;
        let Some(max_streams_per_connection) = max_streams_per_connection else {
            return Ok(Self::single(client));
        };
        Ok(Self {
            inner: Inner::Balanced(Box::new(Balanced {
                config,
                max_streams_per_connection,
                max_connections,
                slots: Mutex::new(vec![Arc::new(Slot::new(client))]),
            })),
        })
    }

    /// The client to send the next request with, and the request's hold on
    /// it when balancing.
    pub(crate) fn acquire(&self) -> PyResult<(reqwest::Client, Option<InFlight>)> {
        match &self.inner {
            Inner::Single(client) => Ok((client.clone(), None)),
            Inner::Balanced(balanced) => {
                let slot = balanced.acquire()?;
                Ok((slot.client.clone(), Some(InFlight { slot })))
            }
        }
    }

    /// The requests in flight on each client, in creation order, or `None`
    /// when not balancing.
    pub(crate) fn loads(&self) -> Option<Vec<usize>> {
        match &self.inner {
            Inner::Single(_) => None,
            Inner::Balanced(balanced) => Some(
                balanced
                    .slots()
                    .iter()
                    .map(|slot| slot.in_flight.load(Ordering::Acquire))
                    .collect(),
            ),
        }
    }
}

impl Balanced {
    fn slots(&self) -> std::sync::MutexGuard<'_, Vec<Arc<Slot>>> {
        self.slots.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn acquire(&self) -> PyResult<Arc<Slot>> {
        let mut slots = self.slots();
        // The first of the least loaded, so a new client fills up before
        // the set grows again.
        let (least_loaded, load) = slots
            .iter()
            .map(|slot| (slot, slot.in_flight.load(Ordering::Acquire)))
            .min_by_key(|&(_, load)| load)
            .expect("a balanced set always has a client");
        let can_grow = self
            .max_connections
            .is_none_or(|max_connections| slots.len() < max_connections);
        let slot = if load >= self.max_streams_per_connection && can_grow {
            let slot = Arc::new(Slot::new(self.config.build()?));
            slots.push(slot.clone());
            slot
        } else {
            least_loaded.clone()
        };
        slot.in_flight.fetch_add(1, Ordering::AcqRel);
        Ok(slot)
    }
}

impl Slot {
    fn new(client: reqwest::Client) -> Self {
        Self {
            client,
            in_flight: AtomicUsize::new(0),
        }
    }
}
