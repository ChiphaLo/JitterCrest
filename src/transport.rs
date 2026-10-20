//! HTTP/1 connection cancellation for fixed-download deadlines.
//!
//! A body stream cannot enforce a wall-clock deadline while Hyper is waiting
//! for a slow peer's socket to become writable: it is not polled then. A
//! per-connection cancellation signal wakes pending socket I/O instead.
use axum::{
    body::Body,
    extract::connect_info::Connected,
    serve::{IncomingStream, Listener},
};
use bytes::{Buf, Bytes};
use http_body::{Body as HttpBody, Frame, SizeHint};
use hyper::body::Incoming;
use std::{
    collections::VecDeque,
    future::Future,
    future::poll_fn,
    io,
    net::SocketAddr,
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Debug)]
pub struct ConnInfo {
    pub peer: SocketAddr,
    pub close: CancellationToken,
    pub requests: Arc<AtomicUsize>,
    idle: Arc<IdleTracker>,
}

// Node 24 adds this to keepAliveTimeout before closing an idle socket. It is
// not a response header and must not cut off active downloads or uploads.
const NODE_KEEP_ALIVE_BUFFER_MS: u64 = 1000;

#[derive(Debug)]
struct IdleTracker {
    close: CancellationToken,
    timeout: Duration,
    generation: AtomicUsize,
    request_id: AtomicUsize,
    state: Mutex<IdleState>,
}

#[derive(Default, Debug)]
struct IdleState {
    timer: Option<JoinHandle<()>>,
    finished_request: Option<usize>,
}

impl IdleTracker {
    fn new(close: CancellationToken, timeout_ms: u64) -> Self {
        Self {
            close,
            timeout: Duration::from_millis(timeout_ms + NODE_KEEP_ALIVE_BUFFER_MS),
            generation: AtomicUsize::new(0),
            request_id: AtomicUsize::new(0),
            state: Mutex::new(IdleState::default()),
        }
    }

    fn cancel_timer(&self) {
        if let Some(timer) = self
            .state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .timer
            .take()
        {
            timer.abort();
        }
    }

    fn start_request(&self) -> usize {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let generation = self.generation.fetch_add(1, Ordering::Relaxed) + 1;
        self.request_id.fetch_add(1, Ordering::Relaxed);
        state.finished_request = None;
        if let Some(timer) = state.timer.take() {
            timer.abort();
        }
        generation
    }

    fn on_read(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(timer) = state.timer.take() {
            self.generation.fetch_add(1, Ordering::Relaxed);
            timer.abort();
        }
    }

    fn response_finished(self: &Arc<Self>, generation: usize) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if self.generation.load(Ordering::Relaxed) != generation {
            return;
        }
        state.finished_request = Some(self.request_id.load(Ordering::Relaxed));
        self.start_timer(&mut state);
    }

    fn body_completed(self: &Arc<Self>, request_id: usize) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        tracing::debug!(request_id, current_request = self.request_id.load(Ordering::Relaxed),
            finished_request = ?state.finished_request, "ignored HTTP body completed");
        if state.finished_request != Some(request_id)
            || self.request_id.load(Ordering::Relaxed) != request_id
            || self.close.is_cancelled()
        {
            return;
        }
        // If body bytes arrived after an early response, restart Node's idle
        // window from completion; a newer request supersedes this generation.
        self.start_timer(&mut state);
    }

    fn start_timer(self: &Arc<Self>, state: &mut IdleState) {
        if let Some(old) = state.timer.take() {
            old.abort();
        }
        // A response body can also be dropped while the runtime is tearing
        // down; there is then no live socket that needs an idle deadline.
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            return;
        };
        let idle = Arc::clone(self);
        let generation = self.generation.load(Ordering::Relaxed);
        state.timer = Some(runtime.spawn(async move {
            tokio::time::sleep(idle.timeout).await;
            if idle.generation.load(Ordering::Relaxed) == generation {
                idle.close.cancel();
            }
        }));
    }
}

struct IdleBody {
    body: Body,
    idle: Arc<IdleTracker>,
    generation: usize,
}

/// Indicates when Hyper has finished delivering the entire request body.
/// A request deadline must not interrupt slow downstream work after a fully
/// received body (for example WebRTC negotiation).
pub struct CompletionBody {
    body: Arc<Mutex<Incoming>>,
    completed: Arc<AtomicBool>,
}

/// A connection-owned consumer for a request body ignored by the handler.
/// Keeping Incoming alive permits Hyper to parse subsequent requests on the
/// same HTTP/1 connection after this body's remaining bytes arrive.
pub struct CompletionDrain {
    body: Arc<Mutex<Incoming>>,
    completed: Arc<AtomicBool>,
}

impl CompletionBody {
    pub fn new(body: Incoming, completed: Arc<AtomicBool>) -> (Self, CompletionDrain) {
        if body.is_end_stream() {
            completed.store(true, Ordering::Release);
        }
        let body = Arc::new(Mutex::new(body));
        (
            Self {
                body: Arc::clone(&body),
                completed: Arc::clone(&completed),
            },
            CompletionDrain { body, completed },
        )
    }
}

impl CompletionDrain {
    pub async fn run(self) {
        while !self.completed.load(Ordering::Acquire) {
            let next = poll_fn(|cx| {
                let mut body = self.body.lock().unwrap_or_else(|e| e.into_inner());
                Pin::new(&mut *body).poll_frame(cx)
            })
            .await;
            let at_end = self
                .body
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .is_end_stream();
            if next.is_none() || next.is_some_and(|frame| frame.is_err()) || at_end {
                self.completed.store(true, Ordering::Release);
                break;
            }
        }
    }
}

impl HttpBody for CompletionBody {
    type Data = Bytes;
    type Error = hyper::Error;

    fn poll_frame(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        let this = self.get_mut();
        let mut body = this.body.lock().unwrap_or_else(|e| e.into_inner());
        let result = Pin::new(&mut *body).poll_frame(cx);
        if matches!(&result, Poll::Ready(None) | Poll::Ready(Some(Err(_)))) {
            this.completed.store(true, Ordering::Release);
        }
        result
    }

    fn is_end_stream(&self) -> bool {
        self.body
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_end_stream()
    }

    fn size_hint(&self) -> SizeHint {
        self.body
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .size_hint()
    }
}

impl Drop for IdleBody {
    fn drop(&mut self) {
        self.idle.response_finished(self.generation);
    }
}

impl HttpBody for IdleBody {
    type Data = Bytes;
    type Error = axum::Error;

    fn poll_frame(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        Pin::new(&mut self.get_mut().body).poll_frame(cx)
    }

    fn is_end_stream(&self) -> bool {
        self.body.is_end_stream()
    }

    fn size_hint(&self) -> SizeHint {
        self.body.size_hint()
    }
}

impl ConnInfo {
    pub fn start_request(&self) -> usize {
        self.idle.start_request()
    }

    pub fn request_id(&self) -> usize {
        self.idle.request_id.load(Ordering::Relaxed)
    }

    pub fn body_completed(&self, request_id: usize) {
        self.idle.body_completed(request_id);
    }

    pub fn wrap_response(
        &self,
        response: axum::response::Response,
        generation: usize,
    ) -> axum::response::Response {
        response.map(|body| {
            Body::new(IdleBody {
                body,
                idle: Arc::clone(&self.idle),
                generation,
            })
        })
    }
}

pub struct DeadlineListener {
    listener: TcpListener,
    keep_alive_ms: u64,
}

impl DeadlineListener {
    pub fn new(listener: TcpListener, keep_alive_ms: u64) -> Self {
        Self {
            listener,
            keep_alive_ms,
        }
    }
}

impl Listener for DeadlineListener {
    type Io = DeadlineIo;
    type Addr = ConnInfo;

    async fn accept(&mut self) -> (Self::Io, Self::Addr) {
        loop {
            match self.listener.accept().await {
                Ok((socket, peer)) => {
                    let close = CancellationToken::new();
                    let idle = Arc::new(IdleTracker::new(close.clone(), self.keep_alive_ms));
                    return (
                        DeadlineIo {
                            socket,
                            cancelled: Box::pin(close.clone().cancelled_owned()),
                            idle: Arc::clone(&idle),
                        },
                        ConnInfo {
                            peer,
                            close,
                            requests: Arc::new(AtomicUsize::new(0)),
                            idle,
                        },
                    );
                }
                Err(error) => {
                    if matches!(
                        error.kind(),
                        io::ErrorKind::ConnectionRefused
                            | io::ErrorKind::ConnectionAborted
                            | io::ErrorKind::ConnectionReset
                    ) {
                        continue;
                    }
                    tracing::error!(%error, "HTTP accept failed");
                    tokio::time::sleep(Duration::from_secs(1)).await;
                }
            }
        }
    }

    fn local_addr(&self) -> io::Result<Self::Addr> {
        Ok(ConnInfo {
            peer: self.listener.local_addr()?,
            close: CancellationToken::new(),
            requests: Arc::new(AtomicUsize::new(0)),
            idle: Arc::new(IdleTracker::new(
                CancellationToken::new(),
                self.keep_alive_ms,
            )),
        })
    }
}

impl Connected<IncomingStream<'_, DeadlineListener>> for ConnInfo {
    fn connect_info(stream: IncomingStream<'_, DeadlineListener>) -> Self {
        stream.remote_addr().clone()
    }
}

pub struct DeadlineIo {
    socket: TcpStream,
    cancelled: Pin<Box<dyn Future<Output = ()> + Send>>,
    idle: Arc<IdleTracker>,
}

/// Replays bytes Hyper already read beyond a `Connection: close` response.
/// Node keeps parsing such pipelined requests to answer 503 after its
/// per-socket request limit, whereas Hyper stops parsing at that response.
pub struct RewindIo {
    socket: DeadlineIo,
    prefix: VecDeque<Bytes>,
}

impl RewindIo {
    pub fn new(socket: DeadlineIo, prefix: Bytes) -> Self {
        let mut chunks = VecDeque::new();
        if !prefix.is_empty() {
            chunks.push_back(prefix);
        }
        Self {
            socket,
            prefix: chunks,
        }
    }

    pub fn prepend_prefix(&mut self, prefix: Bytes) {
        if !prefix.is_empty() {
            self.prefix.push_front(prefix);
        }
    }
}

impl AsyncRead for RewindIo {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if buf.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        if let Some(prefix) = this.prefix.front_mut() {
            let len = prefix.len().min(buf.remaining());
            buf.put_slice(&prefix[..len]);
            prefix.advance(len);
            if prefix.is_empty() {
                this.prefix.pop_front();
            }
            Poll::Ready(Ok(()))
        } else {
            Pin::new(&mut this.socket).poll_read(cx, buf)
        }
    }
}

impl AsyncWrite for RewindIo {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.get_mut().socket).poll_write(cx, buf)
    }

    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().socket).poll_flush(cx)
    }

    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().socket).poll_shutdown(cx)
    }
}

impl Drop for DeadlineIo {
    fn drop(&mut self) {
        self.idle.cancel_timer();
        // The socket owns any bounded background body-drain task. Ending the
        // connection wakes that task even when Incoming never yields again.
        self.idle.close.cancel();
    }
}

impl DeadlineIo {
    fn check(&mut self, cx: &mut Context<'_>) -> io::Result<()> {
        if self.cancelled.as_mut().poll(cx).is_ready() {
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "connection deadline",
            ))
        } else {
            Ok(())
        }
    }
}

impl AsyncRead for DeadlineIo {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if let Err(error) = this.check(cx) {
            return Poll::Ready(Err(error));
        }
        let before = buf.filled().len();
        let result = Pin::new(&mut this.socket).poll_read(cx, buf);
        if matches!(&result, Poll::Ready(Ok(()))) && buf.filled().len() > before {
            this.idle.on_read();
        }
        result
    }
}

impl AsyncWrite for DeadlineIo {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        if let Err(error) = this.check(cx) {
            return Poll::Ready(Err(error));
        }
        Pin::new(&mut this.socket).poll_write(cx, buf)
    }

    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if let Err(error) = this.check(cx) {
            return Poll::Ready(Err(error));
        }
        Pin::new(&mut this.socket).poll_flush(cx)
    }

    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().socket).poll_shutdown(cx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn rewind_preserves_prefetched_bytes_before_socket_data() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap())
            .await
            .unwrap();
        let (socket, _) = listener.accept().await.unwrap();
        let close = CancellationToken::new();
        let idle = Arc::new(IdleTracker::new(close.clone(), 1000));
        let socket = DeadlineIo {
            socket,
            cancelled: Box::pin(close.cancelled_owned()),
            idle,
        };
        let mut io = RewindIo::new(socket, Bytes::from_static(b"tail"));
        io.prepend_prefix(Bytes::from_static(b"head"));
        client.write_all(b"wire").await.unwrap();
        let mut data = [0_u8; 12];
        io.read_exact(&mut data).await.unwrap();
        assert_eq!(&data, b"headtailwire");
    }

    #[test]
    fn dropping_body_after_runtime_shutdown_does_not_spawn() {
        let idle = Arc::new(IdleTracker::new(CancellationToken::new(), 1000));
        let generation = idle.start_request();
        drop(Body::new(IdleBody {
            body: Body::empty(),
            idle: Arc::clone(&idle),
            generation,
        }));
        assert!(
            idle.state
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .timer
                .is_none()
        );
    }

    #[tokio::test]
    async fn idle_deadline_starts_after_body_drop_and_is_generation_scoped() {
        let close = CancellationToken::new();
        let idle = Arc::new(IdleTracker {
            close: close.clone(),
            timeout: Duration::from_millis(20),
            generation: AtomicUsize::new(0),
            request_id: AtomicUsize::new(0),
            state: Mutex::new(IdleState::default()),
        });
        let first = idle.start_request();
        let response = Body::new(IdleBody {
            body: Body::empty(),
            idle: Arc::clone(&idle),
            generation: first,
        });
        tokio::time::sleep(Duration::from_millis(35)).await;
        assert!(!close.is_cancelled(), "active response has no idle timer");
        drop(response);

        let second = idle.start_request();
        idle.response_finished(first); // stale pipelined response cannot close a newer request
        tokio::time::sleep(Duration::from_millis(35)).await;
        assert!(
            !close.is_cancelled(),
            "new request cancels old idle deadline"
        );

        idle.response_finished(second);
        tokio::time::sleep(Duration::from_millis(35)).await;
        assert!(
            close.is_cancelled(),
            "completed response starts idle deadline"
        );
        idle.cancel_timer();
    }

    #[tokio::test]
    async fn ignored_body_completion_restarts_idle_only_for_its_own_request() {
        let close = CancellationToken::new();
        let idle = Arc::new(IdleTracker {
            close: close.clone(),
            timeout: Duration::from_millis(80),
            generation: AtomicUsize::new(0),
            request_id: AtomicUsize::new(0),
            state: Mutex::new(IdleState::default()),
        });
        let first = idle.start_request();
        let first_id = idle.request_id.load(Ordering::Relaxed);
        idle.response_finished(first);
        tokio::time::sleep(Duration::from_millis(40)).await;
        idle.on_read();
        idle.body_completed(first_id);
        tokio::time::sleep(Duration::from_millis(55)).await;
        assert!(!close.is_cancelled(), "body completion resets idle window");

        let second = idle.start_request();
        idle.body_completed(first_id);
        assert!(
            idle.state
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .timer
                .is_none()
        );
        idle.response_finished(second);
        // Windows rounds both deadlines into the same coarse runtime timer
        // tick, so wait for the cancellation instead of racing a fixed sleep.
        for _ in 0..50 {
            if close.is_cancelled() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(
            close.is_cancelled(),
            "current response still closes on idle"
        );
    }
}
