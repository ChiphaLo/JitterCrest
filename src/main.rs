mod client_info;
mod config;
mod http_timer;
mod limits;
mod rate;
mod rtc;
mod transport;
mod web;

use anyhow::{Context, Result};
use axum::{
    body::Body,
    http::{HeaderValue, Request, Response, StatusCode, header},
    serve::Listener,
};
use bytes::Bytes;
use config::Config;
use futures_util::future::poll_fn;
use http_body::Body as HttpBody;
use http_timer::NodeCheckTimer;
use hyper::{body::Incoming, server::conn::http1, service::service_fn};
use hyper_util::rt::TokioIo;
use rtc::RtcService;
use std::time::Duration;
use std::{
    convert::Infallible,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};
use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;
use tokio::task::JoinSet;
use tokio_util::sync::CancellationToken;
use tower::ServiceExt;
use tracing::{info, warn};
use transport::{CompletionBody, ConnInfo, DeadlineIo, DeadlineListener, RewindIo};

// Incomplete request heads/bodies can wait indefinitely inside Hyper. Give
// in-flight responses a bounded drain window before terminating their tasks.
const HTTP_SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "speedtest_rust=info,warn".into()),
        )
        .init();
    let cfg = Config::from_env()?;
    let listener = TcpListener::bind(cfg.bind_addr()?)
        .await
        .context("bind HTTP listener")?;
    info!(address = %listener.local_addr()?, "HTTP listener started");
    let rtc = RtcService::new(cfg.clone());
    let app = web::router(cfg.clone(), rtc.clone());
    let mut listener = DeadlineListener::new(listener, cfg.http_keep_alive_ms);
    let mut connections = JoinSet::new();
    let shutdown = CancellationToken::new();
    let signal = shutdown_signal();
    tokio::pin!(signal);
    // Node checks expirations on a server-wide 30 s cadence, not at the
    // exact configured deadline. We write Node's 408 after Hyper times out.
    // Node swaps the effective values if properties are set after creation
    // with headersTimeout > requestTimeout (node_http_parser.cc Expired).
    let effective_headers_ms = cfg.http_headers_ms.min(cfg.http_request_ms);
    let effective_request_ms = cfg.http_headers_ms.max(cfg.http_request_ms);
    let header_timeout = Duration::from_millis(effective_headers_ms);
    let timer = NodeCheckTimer::new();
    let max_headers = cfg.http_max_headers_count;
    let max_requests = cfg.http_max_requests_per_socket;

    loop {
        tokio::select! {
            result = &mut signal => {
                if let Err(error) = result {
                    tracing::error!(%error, "shutdown signal handler failed");
                }
                info!("shutdown requested");
                break;
            }
            Some(result) = connections.join_next(), if !connections.is_empty() => {
                if let Err(error) = result {
                    tracing::error!(%error, "HTTP connection task failed");
                }
            }
            (io, conn) = listener.accept() => {
                let app = app.clone();
                let shutdown = shutdown.clone();
                let timer = timer.clone();
                connections.spawn(async move {
                    let probe = conn.clone();
                    let body_timed_out = Arc::new(AtomicBool::new(false));
                    let timeout_flag = Arc::clone(&body_timed_out);
                    let request_timer = timer.clone();
                    let service = service_fn(move |mut request: Request<Incoming>| {
                        let peer = conn.peer;
                        request.extensions_mut().insert(conn.clone());
                        let app = app.clone();
                        let drain_owner = conn.clone();
                        // Most speed-test GETs have no request body: avoid a
                        // completion tracker and timer on that hot path.
                        let (request, completed, drain) = if request.body().is_end_stream() {
                            (request.map(Body::new), None, None)
                        } else {
                            let completed = Arc::new(AtomicBool::new(false));
                            let complete_body = Arc::clone(&completed);
                            let (parts, body) = request.into_parts();
                            let (body, drain) = CompletionBody::new(body, complete_body);
                            (Request::from_parts(parts, Body::new(body)), Some(completed), Some(drain))
                        };
                        let request_timer = request_timer.clone();
                        let timeout_flag = Arc::clone(&timeout_flag);
                        Box::pin(async move {
                            let future = app.oneshot(request);
                            let Some(completed) = completed else { return future.await; };
                            tokio::pin!(future);
                            let deadline = request_timer.request_deadline(std::time::Instant::now(), effective_request_ms);
                            let result = tokio::select! {
                                result = &mut future => result,
                                () = tokio::time::sleep_until(deadline) => {
                                    if completed.load(Ordering::Acquire) {
                                        future.await
                                    } else {
                                        tracing::debug!(%peer, timeout_ms = effective_request_ms,
                                            "HTTP request body timed out");
                                        timeout_flag.store(true, Ordering::Release);
                                        let mut response = Response::new(Body::empty());
                                        *response.status_mut() = StatusCode::REQUEST_TIMEOUT;
                                        response.headers_mut().insert(header::CONNECTION, HeaderValue::from_static("close"));
                                        Ok(response)
                                    }
                                }
                            };
                            let keep_connection = result.as_ref().is_ok_and(|response| {
                                response.headers().get(header::CONNECTION).is_none_or(|value| {
                                    !value.to_str().is_ok_and(|text| text.split(',').any(|token|
                                        token.trim().eq_ignore_ascii_case("close")))
                                })
                            });
                            if keep_connection && !timeout_flag.load(Ordering::Acquire)
                                && !completed.load(Ordering::Acquire)
                                && let Some(drain) = drain
                            {
                                // Hyper otherwise closes immediately when the handler
                                // ignores an incomplete body. Node keeps reading it and
                                // can reuse the socket once it is complete. The socket's
                                // cancellation token owns this bounded drain task.
                                let request_id = drain_owner.request_id();
                                tokio::spawn(async move {
                                    tokio::select! {
                                        () = drain.run() => drain_owner.body_completed(request_id),
                                        () = tokio::time::sleep_until(deadline) => {
                                            tracing::debug!(%peer, "ignored HTTP body timed out");
                                            drain_owner.close.cancel();
                                        }
                                        () = drain_owner.close.cancelled() => {}
                                    }
                                });
                            }
                            result
                        })
                    });
                    let mut builder = http1::Builder::new();
                    builder.timer(timer.clone());
                    builder.header_read_timeout(header_timeout);
                    builder.max_headers(max_headers);
                    let mut connection = builder.serve_connection(TokioIo::new(io), service);
                    let mut header_timed_out = false;
                    let mut normal_completed = false;
                    tokio::select! {
                        result = poll_fn(|cx| connection.poll_without_shutdown(cx)) => {
                            header_timed_out = result.as_ref().is_err_and(hyper::Error::is_timeout);
                            normal_completed = result.is_ok();
                            if let Err(error) = result {
                                tracing::debug!(%error, "HTTP connection closed");
                            }
                        }
                        () = shutdown.cancelled() => {
                            std::pin::Pin::new(&mut connection).graceful_shutdown();
                            if let Err(error) = poll_fn(|cx| connection.poll_without_shutdown(cx)).await {
                                tracing::debug!(%error, "HTTP connection closed during shutdown");
                            }
                        }
                    }
                    if header_timed_out {
                        let mut io = connection.into_parts().io.into_inner();
                        // Hyper reports a header deadline but does not emit the
                        // 408 response that Node sends for an incomplete head.
                        if let Err(error) = io.write_all(b"HTTP/1.1 408 Request Timeout\r\nConnection: close\r\n\r\n").await {
                            tracing::debug!(%error, "write HTTP header timeout response failed");
                        }
                    } else {
                        let parts = connection.into_parts();
                        if normal_completed && !body_timed_out.load(Ordering::Acquire)
                            && probe.requests.load(Ordering::Relaxed) >= max_requests
                            && !shutdown.is_cancelled()
                        {
                            reject_excess_requests(parts.io.into_inner(), parts.read_buf, probe,
                                header_timeout, max_headers, timer, shutdown).await;
                        }
                    }
                });
            }
        }
    }
    rtc.shutdown().await;
    shutdown.cancel();
    let drain = async {
        while let Some(result) = connections.join_next().await {
            if let Err(error) = result {
                tracing::error!(%error, "HTTP connection task failed during shutdown");
            }
        }
    };
    if tokio::time::timeout(HTTP_SHUTDOWN_GRACE, drain)
        .await
        .is_err()
    {
        warn!(
            remaining = connections.len(),
            "HTTP shutdown grace expired; aborting connections"
        );
        connections.abort_all();
        while let Some(result) = connections.join_next().await {
            if let Err(error) = result
                && !error.is_cancelled()
            {
                tracing::error!(%error, "HTTP connection task failed during forced shutdown");
            }
        }
    }
    info!("HTTP listener stopped");
    Ok(())
}

async fn reject_excess_requests(
    socket: DeadlineIo,
    prefix: Bytes,
    conn: ConnInfo,
    header_timeout: Duration,
    max_headers: usize,
    timer: NodeCheckTimer,
    shutdown: CancellationToken,
) {
    let mut io = RewindIo::new(socket, prefix);
    loop {
        let previous_requests = conn.requests.load(Ordering::Relaxed);
        let current = conn.clone();
        let service = service_fn(move |_request: Request<Incoming>| {
            let current = current.clone();
            Box::pin(async move {
                let generation = current.start_request();
                current.requests.fetch_add(1, Ordering::Relaxed);
                let mut response = Response::new(Body::empty());
                *response.status_mut() = StatusCode::SERVICE_UNAVAILABLE;
                response
                    .headers_mut()
                    .insert(header::CONNECTION, HeaderValue::from_static("close"));
                response.headers_mut().insert(
                    header::TRANSFER_ENCODING,
                    HeaderValue::from_static("chunked"),
                );
                Ok::<_, Infallible>(current.wrap_response(response, generation))
            })
        });
        let mut builder = http1::Builder::new();
        builder.timer(timer.clone());
        builder.header_read_timeout(header_timeout);
        builder.max_headers(max_headers);
        let mut connection = builder.serve_connection(TokioIo::new(io), service);
        let result = tokio::select! {
            result = poll_fn(|cx| connection.poll_without_shutdown(cx)) => result,
            () = shutdown.cancelled() => return,
        };
        let parts = connection.into_parts();
        io = parts.io.into_inner();
        io.prepend_prefix(parts.read_buf);
        match result {
            Ok(()) if conn.requests.load(Ordering::Relaxed) == previous_requests => return,
            Ok(()) => {}
            Err(error) if error.is_timeout() => {
                let _ = io
                    .write_all(b"HTTP/1.1 408 Request Timeout\r\nConnection: close\r\n\r\n")
                    .await;
                return;
            }
            Err(error) => {
                tracing::debug!(%error, "excess HTTP request connection closed");
                return;
            }
        }
    }
}

async fn shutdown_signal() -> Result<()> {
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .context("register SIGTERM handler")?;
        tokio::select! {
            signal = tokio::signal::ctrl_c() => signal.context("wait for SIGINT")?,
            _ = term.recv() => {},
        }
    }
    #[cfg(not(unix))]
    tokio::signal::ctrl_c()
        .await
        .context("wait for interrupt")?;
    Ok(())
}
