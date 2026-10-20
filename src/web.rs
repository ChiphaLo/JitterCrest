use crate::{
    client_info::ClientInfo,
    config::Config,
    limits::{Direction, Lease, Limits},
    rtc::RtcService,
    transport::ConnInfo,
};
use async_stream::stream;
use axum::{
    Router,
    body::{Body, Bytes},
    extract::{Extension, State},
    http::{HeaderMap, HeaderValue, Method, Request, Response, StatusCode, Version, header},
    routing::any,
};
use futures_util::StreamExt;
use speedtest_rust::rtc_sdp::{SdpError, sanitize as sanitize_sdp};
use std::{
    net::{IpAddr, SocketAddr},
    sync::atomic::Ordering,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

const PAGE: &str = include_str!(concat!(env!("OUT_DIR"), "/index.html"));
const ICON: &[u8] = include_bytes!("../public/favicon.svg");
const CHUNK_SIZE: usize = 512 * 1024;

#[derive(Clone)]
struct App {
    cfg: Config,
    keep_alive_header: HeaderValue,
    limits: Limits,
    rtc: RtcService,
    payload: Bytes,
    client_info: ClientInfo,
}

pub fn router(cfg: Config, rtc: RtcService) -> Router {
    // Both values are bounded decimal integers, so the formatted header is
    // always ASCII and cannot contain an HTTP control character.
    let keep_alive_header = HeaderValue::from_str(&format!(
        "timeout={}, max={}",
        cfg.http_keep_alive_ms / 1000,
        cfg.http_max_requests_per_socket
    ))
    .expect("bounded keep-alive numeric header must be valid");
    let mut data = vec![0; CHUNK_SIZE];
    // A single random, immutable chunk reused by all download streams.
    if let Err(error) = getrandom::fill(&mut data) {
        tracing::error!(%error, "failed to initialize download payload");
        // Avoid serving a deterministic all-zero test payload on entropy failure.
        std::process::exit(1);
    }
    Router::new().fallback(any(handle)).with_state(App {
        cfg,
        keep_alive_header,
        limits: Limits::default(),
        rtc,
        payload: Bytes::from(data),
        client_info: ClientInfo::new(),
    })
}

fn header_str<'a>(headers: &'a HeaderMap, name: &str) -> &'a str {
    headers
        .get(name)
        .and_then(|h| h.to_str().ok())
        .unwrap_or("")
}

fn ip(remote: SocketAddr, headers: &HeaderMap, cfg: &Config) -> String {
    let peer = remote.ip().to_string();
    if !cfg.trust_proxy || !cfg.trusted_proxy_ips.contains(&peer) {
        return peer;
    }
    let raw = match header_str(headers, "x-real-ip") {
        "" => header_str(headers, "x-forwarded-for"),
        value => value,
    };
    let value = raw
        .split(',')
        .next()
        .unwrap_or("")
        .trim()
        .trim_start_matches("::ffff:");
    if value.parse::<IpAddr>().is_ok() {
        value.to_owned()
    } else {
        peer
    }
}

fn path_is(path: &str, target: &str) -> bool {
    path == target || path == format!("/speed{target}") || (path == "/speed" && target == "/")
}

fn is_speed_data(path: &str) -> bool {
    [
        "/download",
        "/download-stream",
        "/download-100k",
        "/upload",
        "/upload-stream",
    ]
    .iter()
    .any(|target| path_is(path, target))
}

fn common(headers: &HeaderMap, path: &str, cfg: &Config, response: &mut Response<Body>) {
    let h = response.headers_mut();
    h.insert(
        "access-control-allow-methods",
        HeaderValue::from_static("GET,POST,HEAD,OPTIONS"),
    );
    h.insert("access-control-allow-headers", HeaderValue::from_static("Content-Type, Authorization, X-Speedtest-Attempt-Id, X-Speedtest-Page-Instance, X-Speedtest-Session-Id"));
    h.insert("access-control-max-age", HeaderValue::from_static("86400"));
    h.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    h.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    let origin = header_str(headers, "origin");
    if (cfg.allowed_origins.contains(origin)
        || (cfg.public_cors
            && ["/", "/health", "/latency"]
                .iter()
                .any(|p| path_is(path, p))))
        && let Ok(value) = HeaderValue::from_str(origin)
        && !origin.is_empty()
    {
        h.insert("access-control-allow-origin", value);
        h.insert(header::VARY, HeaderValue::from_static("Origin"));
    }
}

fn response(
    status: StatusCode,
    body: impl Into<Body>,
    content_type: Option<&'static str>,
    cache: Option<&'static str>,
) -> Response<Body> {
    let mut out = Response::new(if status == StatusCode::NO_CONTENT {
        Body::from_stream(futures_util::stream::empty::<Result<Bytes, std::io::Error>>())
    } else {
        body.into()
    });
    *out.status_mut() = status;
    if let Some(value) = content_type {
        out.headers_mut()
            .insert(header::CONTENT_TYPE, HeaderValue::from_static(value));
    }
    if let Some(value) = cache {
        out.headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static(value));
    }
    out
}

fn unknown_length_text(value: &'static str) -> Body {
    Body::from_stream(futures_util::stream::once(async move {
        Ok::<Bytes, std::io::Error>(Bytes::from_static(value.as_bytes()))
    }))
}

fn json(value: serde_json::Value) -> Response<Body> {
    let data = value.to_string();
    let mut out = response(
        StatusCode::OK,
        Body::from(data.clone()),
        Some("application/json"),
        Some("no-store"),
    );
    if let Ok(length) = HeaderValue::from_str(&data.len().to_string()) {
        out.headers_mut().insert(header::CONTENT_LENGTH, length);
    }
    out
}

fn error(status: StatusCode, message: &'static str) -> Response<Body> {
    let mut out = json(serde_json::json!({"error": message}));
    *out.status_mut() = status;
    out
}

/// Node answers these with `sendError(...)` followed by `req.destroy()`:
/// the response must reach the client and the socket must close immediately
/// afterwards. Marking the response `Connection: close` lets Hyper write the
/// full response and then close, instead of parking the connection in its
/// idle window like a reusable keep-alive socket.
fn error_destroy(status: StatusCode, message: &'static str) -> Response<Body> {
    let mut out = error(status, message);
    out.headers_mut()
        .insert(header::CONNECTION, HeaderValue::from_static("close"));
    out
}

fn bounded(raw: Option<&str>, default: u64, min: u64, max: u64) -> u64 {
    raw.and_then(|s| s.parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .map_or(default, |value| {
            (value.floor().max(min as f64).min(max as f64)) as u64
        })
}

fn query(uri: &axum::http::Uri, key: &str) -> Option<String> {
    url::form_urlencoded::parse(uri.query()?.as_bytes())
        .find(|(name, _)| name == key)
        .map(|(_, value)| value.into_owned())
}

fn page(cfg: &Config, head: bool) -> Response<Body> {
    let mut nonce = [0_u8; 18];
    if getrandom::fill(&mut nonce).is_err() {
        return error(StatusCode::INTERNAL_SERVER_ERROR, "internal server error");
    }
    let nonce: String = nonce.iter().map(|value| format!("{value:02x}")).collect();
    let csp = format!(
        "default-src 'none'; script-src 'nonce-{nonce}'; script-src-attr 'none'; style-src-elem 'nonce-{nonce}'; style-src-attr 'unsafe-inline'; connect-src 'self' https://speed.example.com https://speed.example.com:8443 http://192.0.2.10; worker-src blob:; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    );
    let body = if head {
        Body::empty()
    } else {
        let nodes = cfg
            .nodes
            .to_string()
            .replace('<', "\\u003c")
            .replace('>', "\\u003e")
            .replace('&', "\\u0026");
        Body::from(
            PAGE.replace("__NODE_CONFIG__", &nodes)
                .replace("__CSP_NONCE__", &nonce)
                .replace(
                    "__RTC_RESULT_GRACE_MS__",
                    &cfg.rtc_result_grace_ms.to_string(),
                )
                .replace(
                    "__RTC_CONTROL_SINGLE_MESSAGE_BYTES__",
                    &cfg.control_single_bytes.to_string(),
                )
                .replace(
                    "__RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES__",
                    &cfg.control_fragment_bytes.to_string(),
                )
                .replace(
                    "__MAX_RTC_CONTROL_FRAGMENT_COUNT__",
                    &cfg.control_fragment_count.to_string(),
                )
                .replace(
                    "__MAX_RTC_REASSEMBLED_CONTROL_BYTES__",
                    &cfg.control_reassembled_bytes.to_string(),
                ),
        )
    };
    let mut out = response(
        StatusCode::OK,
        body,
        Some("text/html; charset=utf-8"),
        Some("no-store"),
    );
    if let Ok(value) = HeaderValue::from_str(&csp) {
        out.headers_mut().insert("content-security-policy", value);
    }
    for (name, value) in [
        ("x-frame-options", "DENY"),
        ("cross-origin-opener-policy", "same-origin"),
        ("cross-origin-resource-policy", "same-origin"),
        (
            "permissions-policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        ),
    ] {
        out.headers_mut()
            .insert(name, HeaderValue::from_static(value));
    }
    out
}

struct Reservation {
    limits: Limits,
    ip: String,
    bucket_at: u64,
    window_ms: u64,
    remaining: u64,
    timeout: tokio::task::JoinHandle<()>,
}
impl Drop for Reservation {
    fn drop(&mut self) {
        self.timeout.abort();
        self.limits
            .refund(&self.ip, self.bucket_at, self.remaining, self.window_ms);
    }
}

fn fixed_download(
    app: &App,
    ip: &str,
    size: u64,
    lease: Lease,
    close: CancellationToken,
) -> Response<Body> {
    let Some(bucket_at) = app.limits.reserve_download(ip, size, &app.cfg) else {
        return error(
            StatusCode::TOO_MANY_REQUESTS,
            "download byte budget exceeded",
        );
    };
    let payload = app.payload.clone();
    let deadline = Instant::now() + Duration::from_millis(app.cfg.fixed_download_ms);
    let timeout = tokio::spawn(async move {
        tokio::time::sleep_until(deadline.into()).await;
        warn!("fixed download timed out; closing HTTP connection");
        close.cancel();
    });
    let reservation = Reservation {
        limits: app.limits.clone(),
        ip: ip.to_owned(),
        bucket_at,
        window_ms: app.cfg.budget_window_ms,
        remaining: size,
        timeout,
    };
    let stream = stream! {
        let _lease = lease;
        // Move the RAII owner into the lazy stream. Without this binding the
        // stream macro may borrow only `remaining`, dropping the reservation
        // before the body is polled and refunding the entire download.
        let mut reservation = reservation;
        while reservation.remaining > 0 && Instant::now() < deadline {
            let n = usize::try_from(reservation.remaining.min(CHUNK_SIZE as u64)).unwrap_or(CHUNK_SIZE);
            reservation.remaining -= n as u64;
            yield Ok::<_, std::io::Error>(payload.slice(..n));
        }
    };
    let mut out = response(
        StatusCode::OK,
        Body::from_stream(stream),
        Some("application/octet-stream"),
        Some("no-store, no-transform"),
    );
    out.headers_mut().insert(
        header::CONTENT_ENCODING,
        HeaderValue::from_static("identity"),
    );
    out.headers_mut().insert(
        header::CONTENT_LENGTH,
        HeaderValue::from_str(&size.to_string()).unwrap_or(HeaderValue::from_static("0")),
    );
    out.headers_mut()
        .insert("x-accel-buffering", HeaderValue::from_static("no"));
    out.headers_mut()
        .insert("surrogate-control", HeaderValue::from_static("no-store"));
    out
}

fn stream_download(app: &App, ip: &str, millis: u64, lease: Lease) -> Response<Body> {
    let payload = app.payload.clone();
    let limits = app.limits.clone();
    let cfg = app.cfg.clone();
    let ip = ip.to_owned();
    let stream = stream! {
        let _lease = lease;
        let deadline = tokio::time::Instant::now() + Duration::from_millis(millis);
        loop {
            if tokio::time::Instant::now() >= deadline { break; }
            if !limits.consume(&ip, CHUNK_SIZE as u64, Direction::Download, &cfg) {
                warn!("download byte budget exceeded");
                break;
            }
            yield Ok::<_, std::io::Error>(payload.clone());
        }
    };
    let mut out = response(
        StatusCode::OK,
        Body::from_stream(stream),
        Some("application/octet-stream"),
        Some("no-store, no-transform"),
    );
    out.headers_mut().insert(
        header::CONTENT_ENCODING,
        HeaderValue::from_static("identity"),
    );
    out.headers_mut()
        .insert("x-accel-buffering", HeaderValue::from_static("no"));
    out.headers_mut()
        .insert("surrogate-control", HeaderValue::from_static("no-store"));
    out
}

async fn upload(
    app: &App,
    ip: &str,
    body: Body,
    streaming: bool,
    warmup: u64,
    _lease: Lease,
) -> Response<Body> {
    let started = Instant::now();
    let mut stream = body.into_data_stream();
    let mut bytes = 0_u64;
    let mut measured = 0_u64;
    let mut first = None::<Instant>;
    let mut measured_start = None::<Instant>;
    let max = if streaming {
        app.cfg.max_stream_upload
    } else {
        app.cfg.max_upload
    };
    let deadline = Duration::from_millis(app.cfg.upload_ms);
    loop {
        let next =
            match tokio::time::timeout(deadline.saturating_sub(started.elapsed()), stream.next())
                .await
            {
                Ok(chunk) => chunk,
                Err(_) => {
                    return error_destroy(
                        StatusCode::REQUEST_TIMEOUT,
                        if streaming {
                            "upload stream timeout"
                        } else {
                            "upload timeout"
                        },
                    );
                }
            };
        let Some(chunk) = next else {
            break;
        };
        let chunk = match chunk {
            Ok(value) => value,
            Err(_) => {
                return error(
                    StatusCode::BAD_REQUEST,
                    if streaming {
                        "upload stream failed"
                    } else {
                        "upload failed"
                    },
                );
            }
        };
        if !app
            .limits
            .consume(ip, chunk.len() as u64, Direction::Upload, &app.cfg)
        {
            return error_destroy(StatusCode::TOO_MANY_REQUESTS, "upload byte budget exceeded");
        }
        let now = Instant::now();
        let first_byte = *first.get_or_insert(now);
        bytes = bytes.saturating_add(chunk.len() as u64);
        if bytes > max {
            return error_destroy(
                StatusCode::PAYLOAD_TOO_LARGE,
                if streaming {
                    "upload stream too large"
                } else {
                    "upload too large"
                },
            );
        }
        if now.duration_since(first_byte).as_millis() as u64 >= warmup {
            measured_start.get_or_insert(now);
            measured += chunk.len() as u64;
        }
    }
    let ended = Instant::now();
    if streaming {
        json(serde_json::json!({
            "bytes":bytes, "elapsedMs":first.map_or(0., |t| ended.duration_since(t).as_secs_f64()*1000.),
            "measuredBytes":measured, "measuredElapsedMs":measured_start.map_or(0., |t| ended.duration_since(t).as_secs_f64()*1000.),
            "requestElapsedMs":ended.duration_since(started).as_secs_f64()*1000., "warmupMs":warmup
        }))
    } else {
        json(
            serde_json::json!({"bytes": bytes, "elapsedMs":ended.duration_since(started).as_secs_f64()*1000.}),
        )
    }
}

fn is_private(ip: &str) -> bool {
    match ip.parse::<IpAddr>() {
        Ok(IpAddr::V4(a)) => {
            let [a, b, c, _] = a.octets();
            a == 0
                || a == 10
                || a == 127
                || a >= 224
                || (a == 100 && (64..=127).contains(&b))
                || (a == 169 && b == 254)
                || (a == 172 && (16..=31).contains(&b))
                || (a == 192 && b == 168)
                || (a == 192 && b == 0 && c == 0)
                || (a == 192 && b == 0 && c == 2)
                || (a == 192 && b == 31 && c == 196)
                || (a == 192 && b == 52 && c == 193)
                || (a == 192 && b == 88 && c == 99)
                || (a == 198 && (b == 18 || b == 19))
                || (a == 198 && b == 51 && c == 100)
                || (a == 192 && b == 175 && c == 48)
                || (a == 203 && b == 0 && c == 113)
        }
        Ok(IpAddr::V6(a)) => {
            let segments = a.segments();
            (segments[0] & 0xe000) != 0x2000 || (segments[0] == 0x2001 && segments[1] == 0x0db8)
        }
        Err(_) => true,
    }
}

async fn client_info(app: &App, ip: &str) -> Response<Body> {
    if is_private(ip) {
        return json(serde_json::json!({"ip":ip,"private":true}));
    }
    json(app.client_info.lookup(ip).await)
}

async fn handle(
    State(app): State<App>,
    Extension(conn): Extension<ConnInfo>,
    req: Request<Body>,
) -> Response<Body> {
    let idle_generation = conn.start_request();
    let (parts, body) = req.into_parts();
    let close_after_response = parts.version == Version::HTTP_11
        && conn.requests.fetch_add(1, Ordering::Relaxed) + 1
            >= app.cfg.http_max_requests_per_socket;
    let requested_close = header_str(&parts.headers, "connection")
        .split(',')
        .any(|token| token.trim().eq_ignore_ascii_case("close"));
    let path = parts.uri.path();
    let client_ip = ip(conn.peer, &parts.headers, &app.cfg);
    let origin = header_str(&parts.headers, "origin");
    let probe_only = header_str(&parts.headers, "x-speedtest-node") == "probe-only"
        || header_str(&parts.headers, "host").split(':').next() == Some("192.0.2.10");
    let mut result = if probe_only && is_speed_data(path) {
        response(
            StatusCode::NOT_FOUND,
            Body::from("not found"),
            Some("text/plain; charset=utf-8"),
            None,
        )
    } else if is_speed_data(path)
        && (header_str(&parts.headers, "sec-fetch-site").eq_ignore_ascii_case("cross-site")
            || (!origin.is_empty() && !app.cfg.allowed_origins.contains(origin)))
    {
        error(
            StatusCode::FORBIDDEN,
            if header_str(&parts.headers, "sec-fetch-site").eq_ignore_ascii_case("cross-site") {
                "cross-site speed data requests are not allowed"
            } else {
                "speed data request origin is not allowed"
            },
        )
    } else if parts.method == Method::OPTIONS {
        response(
            StatusCode::NO_CONTENT,
            Body::empty(),
            None,
            Some("no-store"),
        )
    } else if (parts.method == Method::GET || parts.method == Method::HEAD)
        && path_is(path, "/favicon.svg")
    {
        let mut out = response(
            StatusCode::OK,
            if parts.method == Method::HEAD {
                Body::empty()
            } else {
                Body::from(ICON)
            },
            Some("image/svg+xml; charset=utf-8"),
            Some("public, max-age=86400"),
        );
        out.headers_mut().insert(
            header::CONTENT_LENGTH,
            HeaderValue::from_str(&ICON.len().to_string()).unwrap_or(HeaderValue::from_static("0")),
        );
        out
    } else if (parts.method == Method::GET || parts.method == Method::HEAD) && path_is(path, "/") {
        page(&app.cfg, parts.method == Method::HEAD)
    } else if (parts.method == Method::GET || parts.method == Method::HEAD)
        && path_is(path, "/latency")
    {
        response(
            StatusCode::NO_CONTENT,
            Body::empty(),
            None,
            Some("no-store"),
        )
    } else if parts.method == Method::GET
        && (path_is(path, "/download")
            || path_is(path, "/download-100k")
            || path_is(path, "/download-stream"))
    {
        if !app.limits.rate(&client_ip, true, &app.cfg) {
            error(
                StatusCode::TOO_MANY_REQUESTS,
                "speed test rate limit exceeded",
            )
        } else {
            match app.limits.active(&client_ip, &app.cfg) {
                Err(reason) => error(StatusCode::TOO_MANY_REQUESTS, reason),
                Ok(lease) if path_is(path, "/download-stream") => {
                    let ms = bounded(
                        query(&parts.uri, "maxMs").as_deref(),
                        app.cfg.stream_download_ms,
                        1000,
                        30_000,
                    );
                    stream_download(&app, &client_ip, ms, lease)
                }
                Ok(lease) => {
                    let small = path_is(path, "/download-100k");
                    let size = bounded(
                        query(&parts.uri, "size").as_deref(),
                        if small { 100 * 1024 } else { 96 << 20 },
                        1024,
                        if small { 1 << 20 } else { app.cfg.max_download },
                    );
                    fixed_download(&app, &client_ip, size, lease, conn.close.clone())
                }
            }
        }
    } else if parts.method == Method::POST
        && (path_is(path, "/upload") || path_is(path, "/upload-stream"))
    {
        if !app.limits.rate(&client_ip, true, &app.cfg) {
            error(
                StatusCode::TOO_MANY_REQUESTS,
                "speed test rate limit exceeded",
            )
        } else {
            match app.limits.active(&client_ip, &app.cfg) {
                Err(reason) => error(StatusCode::TOO_MANY_REQUESTS, reason),
                Ok(lease) => {
                    let streaming = path_is(path, "/upload-stream");
                    let warmup = if streaming {
                        bounded(query(&parts.uri, "warmupMs").as_deref(), 2000, 0, 5000)
                    } else {
                        0
                    };
                    upload(&app, &client_ip, body, streaming, warmup, lease).await
                }
            }
        }
    } else if parts.method == Method::GET && path_is(path, "/client-info") {
        if !app.limits.rate(&client_ip, false, &app.cfg) {
            error(StatusCode::TOO_MANY_REQUESTS, "rate limit exceeded")
        } else {
            client_info(&app, &client_ip).await
        }
    } else if parts.method == Method::GET && path_is(path, "/health") {
        json(serde_json::json!({"ok":true,"name":app.cfg.name}))
    } else if parts.method == Method::POST && path_is(path, "/webrtc/offer") {
        if header_str(&parts.headers, "sec-fetch-site").eq_ignore_ascii_case("cross-site") {
            error(
                StatusCode::FORBIDDEN,
                "cross-site WebRTC requests are not allowed",
            )
        } else if !origin.is_empty() && !app.cfg.allowed_origins.contains(origin) {
            error(
                StatusCode::FORBIDDEN,
                "WebRTC request origin is not allowed",
            )
        } else if !header_str(&parts.headers, "content-type")
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .eq_ignore_ascii_case("application/json")
        {
            error(
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
                "WebRTC requests require application/json",
            )
        } else if !app.limits.rate(&client_ip, false, &app.cfg) {
            error(StatusCode::TOO_MANY_REQUESTS, "rate limit exceeded")
        } else {
            let attempt = header_str(&parts.headers, "x-speedtest-attempt-id");
            let declared = header_str(&parts.headers, "content-length");
            if !declared.is_empty() && declared.parse::<usize>().is_err() {
                error(StatusCode::BAD_REQUEST, "invalid content length")
            } else if declared
                .parse::<usize>()
                .is_ok_and(|n| n > app.cfg.rtc_offer_body_max)
            {
                error(StatusCode::PAYLOAD_TOO_LARGE, "WebRTC offer body too large")
            } else {
                match axum::body::to_bytes(body, app.cfg.rtc_offer_body_max).await {
                    Err(_) => error(StatusCode::PAYLOAD_TOO_LARGE, "WebRTC offer body too large"),
                    Ok(bytes) => match serde_json::from_slice::<serde_json::Value>(&bytes) {
                        Err(_) => error(StatusCode::BAD_REQUEST, "invalid JSON body"),
                        Ok(data) => {
                            let body_attempt = data["attemptId"].as_str().unwrap_or("");
                            let valid = |id: &str| {
                                id.len() <= 128
                                    && !id.is_empty()
                                    && id.bytes().enumerate().all(|(i, byte)| {
                                        byte.is_ascii_alphanumeric()
                                            || (i > 0 && b"._:-".contains(&byte))
                                    })
                            };
                            if !valid(attempt) || !valid(body_attempt) {
                                error(StatusCode::BAD_REQUEST, "invalid attemptId")
                            } else if attempt != body_attempt {
                                error(StatusCode::BAD_REQUEST, "attemptId header/body mismatch")
                            } else if data["action"] != "cancel"
                                && (data["type"] != "offer" || !data["sdp"].is_string())
                            {
                                error(StatusCode::BAD_REQUEST, "invalid SDP offer")
                            } else if data["action"] == "cancel" && app.cfg.rtc_experimental {
                                match app.rtc.cancel(&client_ip, attempt).await {
                                    Ok(()) => response(
                                        StatusCode::NO_CONTENT,
                                        Body::empty(),
                                        None,
                                        Some("no-store"),
                                    ),
                                    Err(reason) => error(reason.status, reason.message),
                                }
                            } else if let Some(sdp) = data["sdp"].as_str() {
                                match sanitize_sdp(sdp) {
                                    Err(SdpError::Limit) => error(
                                        StatusCode::PAYLOAD_TOO_LARGE,
                                        "WebRTC SDP exceeds limits",
                                    ),
                                    Err(SdpError::Candidate) => {
                                        error(StatusCode::BAD_REQUEST, "invalid SDP offer")
                                    }
                                    Ok(filtered) if app.cfg.rtc_experimental => {
                                        // Only a loopback-bound integration run may retain
                                        // private ICE candidates. Public mode always filters.
                                        let offer_sdp = if app.cfg.rtc_local_test {
                                            sdp.to_owned()
                                        } else {
                                            filtered.sdp
                                        };
                                        match app
                                            .rtc
                                            .offer(&client_ip, attempt, offer_sdp, probe_only)
                                            .await
                                        {
                                            Ok(answer) => json(answer),
                                            Err(reason) => error(reason.status, reason.message),
                                        }
                                    }
                                    Ok(_) => error(
                                        StatusCode::SERVICE_UNAVAILABLE,
                                        "WebRTC server dependency is unavailable",
                                    ),
                                }
                            } else {
                                // Fail closed until SCTP/DTLS transport is migrated and verified.
                                // Never switch the production reverse proxy to this intermediate binary.
                                error(
                                    StatusCode::SERVICE_UNAVAILABLE,
                                    "WebRTC server dependency is unavailable",
                                )
                            }
                        }
                    },
                }
            }
        }
    } else {
        response(
            StatusCode::NOT_FOUND,
            unknown_length_text("not found"),
            Some("text/plain; charset=utf-8"),
            None,
        )
    };
    debug!(method = %parts.method, path, status = result.status().as_u16(), "HTTP request");
    common(&parts.headers, path, &app.cfg, &mut result);
    let handler_close = result
        .headers()
        .get(header::CONNECTION)
        .is_some_and(|value| {
            value.to_str().is_ok_and(|text| {
                text.split(',')
                    .any(|token| token.trim().eq_ignore_ascii_case("close"))
            })
        });
    if close_after_response || requested_close {
        result
            .headers_mut()
            .insert(header::CONNECTION, HeaderValue::from_static("close"));
    } else if handler_close {
        // Upload timeout/budget/oversize responses already asked Hyper to
        // close after writing, matching Node's sendError + req.destroy().
    } else if parts.version == Version::HTTP_11 {
        result
            .headers_mut()
            .insert(header::CONNECTION, HeaderValue::from_static("keep-alive"));
        result.headers_mut().insert(
            axum::http::HeaderName::from_static("keep-alive"),
            app.keep_alive_header.clone(),
        );
    }
    conn.wrap_response(result, idle_generation)
}
