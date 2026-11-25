//! Socket pairs that tokio threads write to, without the GIL, to wake Python
//! code waiting on the other end: an asyncio event loop watching it with
//! `add_reader`, or a sync call under gevent waiting on it with `select`.

use std::io::{Read as _, Write as _};

/// tokio threads write to one end of a pair of these sockets to wake Python
/// code watching the other end.
#[cfg(unix)]
pub(crate) type WakerSocket = std::os::unix::net::UnixStream;
/// tokio threads write to one end of a pair of these sockets to wake Python
/// code watching the other end. The standard library has no Unix domain
/// sockets on Windows, so the pair is connected over loopback.
#[cfg(windows)]
pub(crate) type WakerSocket = std::net::TcpStream;

/// Returns a connected `(reader, writer)` pair, both nonblocking.
pub(crate) fn nonblocking_socket_pair() -> std::io::Result<(WakerSocket, WakerSocket)> {
    let (reader, writer) = socket_pair()?;
    reader.set_nonblocking(true)?;
    writer.set_nonblocking(true)?;
    Ok((reader, writer))
}

#[cfg(unix)]
fn socket_pair() -> std::io::Result<(WakerSocket, WakerSocket)> {
    let (reader, writer) = std::os::unix::net::UnixStream::pair()?;

    #[cfg(target_vendor = "apple")]
    socket2::SockRef::from(&writer).set_nosigpipe(true)?;
    Ok((reader, writer))
}

#[cfg(windows)]
fn socket_pair() -> std::io::Result<(WakerSocket, WakerSocket)> {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0))?;
    let writer = std::net::TcpStream::connect(listener.local_addr()?)?;
    let reader = accept_peer(&listener, writer.local_addr()?)?;
    // Each wakeup is a single byte that must be sent right away.
    writer.set_nodelay(true)?;
    Ok((reader, writer))
}

#[cfg(any(windows, test))]
fn accept_peer(
    listener: &std::net::TcpListener,
    peer: std::net::SocketAddr,
) -> std::io::Result<std::net::TcpStream> {
    let (stream, addr) = listener.accept()?;
    if addr != peer {
        return Err(std::io::Error::other(format!(
            "waker socket accepted {addr} instead of {peer}"
        )));
    }
    Ok(stream)
}

/// Returns the descriptor of a socket to pass to `add_reader` or `select`.
#[cfg(unix)]
pub(crate) fn socket_fd(socket: &WakerSocket) -> i64 {
    use std::os::fd::AsRawFd as _;
    i64::from(socket.as_raw_fd())
}

/// Returns the handle of a socket to pass to `add_reader` or `select`.
#[cfg(windows)]
pub(crate) fn socket_fd(socket: &WakerSocket) -> u64 {
    use std::os::windows::io::AsRawSocket as _;
    socket.as_raw_socket()
}

/// Writes the byte that wakes the code watching the reader. A full socket
/// buffer means a wakeup is already pending, and a closed reader means nobody
/// is watching, so callers ignore errors.
pub(crate) fn write_wakeup(writer: &WakerSocket) -> std::io::Result<()> {
    (&*writer).write_all(&[0])
}

/// Reads every pending wakeup from a nonblocking reader, so it is no longer
/// readable until the next one is written.
pub(crate) fn drain_wakeups(reader: &WakerSocket) {
    let mut buf = [0u8; 64];
    while let Ok(n) = (&*reader).read(&mut buf) {
        if n < buf.len() {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accept_peer_returns_peer_connection() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let writer = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let reader = accept_peer(&listener, writer.local_addr().unwrap()).unwrap();
        assert_eq!(reader.peer_addr().unwrap(), writer.local_addr().unwrap());
    }

    #[test]
    fn accept_peer_rejects_other_connection() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let addr = listener.local_addr().unwrap();
        let _other = std::net::TcpStream::connect(addr).unwrap();
        let writer = std::net::TcpStream::connect(addr).unwrap();
        let err = accept_peer(&listener, writer.local_addr().unwrap()).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::Other);
    }

    #[cfg(unix)]
    #[test]
    fn write_wakeup_to_closed_reader_does_not_raise_sigpipe() {
        use std::sync::atomic::{AtomicBool, Ordering};

        static SIGPIPE_RAISED: AtomicBool = AtomicBool::new(false);
        extern "C" fn on_sigpipe(_: libc::c_int) {
            SIGPIPE_RAISED.store(true, Ordering::Relaxed);
        }

        let (reader, writer) = socket_pair().unwrap();
        drop(reader);
        // Rust ignores SIGPIPE, while a Python program may restore its default
        // action, which kills the process. Record it instead.
        let handler = on_sigpipe as extern "C" fn(libc::c_int) as libc::sighandler_t;
        let previous = unsafe { libc::signal(libc::SIGPIPE, handler) };
        let result = write_wakeup(&writer);
        // The signal can arrive after the write returns.
        std::thread::sleep(std::time::Duration::from_millis(100));
        unsafe { libc::signal(libc::SIGPIPE, previous) };

        assert_eq!(result.unwrap_err().kind(), std::io::ErrorKind::BrokenPipe);
        assert!(!SIGPIPE_RAISED.load(Ordering::Relaxed));
    }
}
