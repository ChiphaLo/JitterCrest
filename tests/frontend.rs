use anyhow::{Context, Result, bail};
use reqwest::{Client, Method, StatusCode};
use std::{
    net::TcpListener,
    process::{Child, Command, Stdio},
    time::Duration,
};

#[cfg(feature = "web-ui")]
const PAGE: &str = include_str!("../public/index.html");

#[cfg(feature = "web-ui")]
#[test]
fn standalone_page_contains_browser_contracts_and_live_speed_charts() {
    for marker in [
        "root.SpeedtestContracts = contracts;",
        "id=\"downloadSpeedChart\"",
        "id=\"uploadSpeedChart\"",
        "function drawSpeedCharts()",
        "addSpeedSample(kind, msg)",
        "finishSpeedChart(kind, msg)",
        "requestAnimationFrame",
        "Time (s)",
        "stroke-dasharray=\"6 4\"",
        "v.line.dataset.resultMbps",
        "new Path2D()",
        "function createServerUploadSampler(",
    ] {
        assert!(PAGE.contains(marker), "missing browser feature: {marker}");
    }
    for obsolete in ["${CONTRACT_INLINE_SOURCE}", "module.exports", "require("] {
        assert!(
            !PAGE.contains(obsolete),
            "obsolete Node dependency: {obsolete}"
        );
    }
}

#[cfg(feature = "web-ui")]
#[test]
fn speed_timestamps_and_runtime_placeholders_are_preserved() {
    for marker in [
        "concurrency, now - started)",
        "at - measurementStartAt",
        "function createUploadSampler(",
        "function validateUploadAccounting(",
        "__NODE_CONFIG__",
        "__CSP_NONCE__",
        "__RTC_RESULT_GRACE_MS__",
        "__RTC_CONTROL_SINGLE_MESSAGE_BYTES__",
        "__RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES__",
        "__MAX_RTC_CONTROL_FRAGMENT_COUNT__",
        "__MAX_RTC_REASSEMBLED_CONTROL_BYTES__",
    ] {
        assert!(PAGE.contains(marker), "missing page contract: {marker}");
    }
}

struct RunningServer(Child);

impl Drop for RunningServer {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

async fn start_server(extra_env: &[(&str, &str)]) -> Result<(RunningServer, String, Client)> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    // Cross-compiled integration tests can run under local Linux/WSL with the
    // matching Linux service artifact rather than an embedded Windows path.
    let executable = std::env::var_os("SPEEDTEST_TEST_BINARY")
        .unwrap_or_else(|| env!("CARGO_BIN_EXE_speedtest-rust").into());
    let child = Command::new(executable)
        // An unrelated cwd proves that neither Node nor frontend source is read at runtime.
        .current_dir(std::env::temp_dir())
        .env("SPEEDTEST_HOST", "127.0.0.1")
        .env("PORT", port.to_string())
        .env("TRUST_PROXY_HEADERS", "0")
        .env("SPEEDTEST_RUST_RTC_EXPERIMENTAL", "0")
        .env(
            "SPEEDTEST_NODES",
            r#"[{"id":"local","label":"Local server","base":""}]"#,
        )
        .envs(extra_env.iter().copied())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("start compiled Rust server")?;
    let mut server = RunningServer(child);
    let base = format!("http://127.0.0.1:{port}");
    let client = Client::builder().timeout(Duration::from_secs(3)).build()?;
    let mut ready = false;
    for _ in 0..100 {
        if let Some(status) = server.0.try_wait()? {
            bail!("Rust server exited before startup: {status}");
        }
        if let Ok(response) = client.get(format!("{base}/health")).send().await
            && response.status() == StatusCode::OK
        {
            ready = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert!(ready, "Rust server did not become ready");
    Ok((server, base, client))
}

#[tokio::test]
async fn rust_binary_serves_embedded_page_and_http_without_source_files() -> Result<()> {
    let (_server, base, client) = start_server(&[]).await?;
    let response = client.get(format!("{base}/")).send().await?;
    if cfg!(feature = "web-ui") {
        assert_eq!(response.status(), StatusCode::OK);
        assert!(response.headers().contains_key("content-security-policy"));
        let page = response.text().await?;
        assert!(page.contains("function drawSpeedCharts()"));
        assert!(page.contains("root.SpeedtestContracts = contracts;"));
        assert!(!page.contains("__NODE_CONFIG__") && !page.contains("__CSP_NONCE__"));
        assert!(!page.contains("__RTC_") && !page.contains("__MAX_RTC_"));
    } else {
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert!(!response.text().await?.contains("<html"));
    }
    let ui_status = if cfg!(feature = "web-ui") {
        StatusCode::OK
    } else {
        StatusCode::NOT_FOUND
    };
    for (method, uri, status, length) in [
        (Method::HEAD, "/", ui_status, Some(0)),
        (Method::GET, "/speed", ui_status, None),
        (Method::GET, "/speed/", ui_status, None),
        (Method::GET, "/latency", StatusCode::NO_CONTENT, Some(0)),
        (Method::GET, "/favicon.svg", ui_status, None),
        (Method::GET, "/speed/favicon.svg", ui_status, None),
        (
            Method::GET,
            "/download?size=10240",
            StatusCode::OK,
            Some(10240),
        ),
        (
            Method::GET,
            "/download-100k?size=102400",
            StatusCode::OK,
            Some(102400),
        ),
        (
            Method::GET,
            "/download-100k?size=1048576",
            StatusCode::OK,
            Some(1048576),
        ),
        (Method::GET, "/no-such-route", StatusCode::NOT_FOUND, None),
    ] {
        let response = client
            .request(method, format!("{base}{uri}"))
            .send()
            .await?;
        assert_eq!(response.status(), status, "{uri}");
        let bytes = response.bytes().await?;
        if let Some(length) = length {
            assert_eq!(bytes.len(), length, "{uri}");
        }
    }
    let response = client
        .post(format!("{base}/upload"))
        .body(vec![7_u8; 10240])
        .send()
        .await?;
    assert_eq!(response.status(), StatusCode::OK);
    let result: serde_json::Value = serde_json::from_slice(&response.bytes().await?)?;
    assert_eq!(result["bytes"], 10240);
    Ok(())
}

#[tokio::test]
async fn download_budget_exhaustion_is_a_failed_body_not_successful_eof() -> Result<()> {
    let (_server, base, client) =
        start_server(&[("DOWNLOAD_BYTE_BUDGET_PER_IP", "1048576")]).await?;
    // A post-header limit must reset/fail the transfer, not silently deliver a
    // shorter successful download that can be mistaken for a completed test.
    if let Ok(response) = client
        .get(format!("{base}/download-stream?maxMs=30000"))
        .send()
        .await
    {
        assert!(
            response.bytes().await.is_err(),
            "quota exhaustion must fail the body"
        );
    }
    let denied = client
        .get(format!("{base}/download?size=10240"))
        .send()
        .await?;
    assert_eq!(denied.status(), StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(
        client.get(format!("{base}/health")).send().await?.status(),
        StatusCode::OK
    );
    Ok(())
}

#[tokio::test]
async fn central_ui_cross_site_access_is_opt_in_and_origin_scoped() -> Result<()> {
    const ALLOWED: &str = "https://console.example.com";
    for enabled in ["0", "1"] {
        let (_server, base, client) = start_server(&[
            ("ALLOWED_ORIGINS", ALLOWED),
            ("SPEEDTEST_ALLOW_CROSS_SITE", enabled),
            ("PUBLIC_CORS", "1"),
        ])
        .await?;
        for origin in [
            Some(ALLOWED),
            Some("https://unlisted.example.net"),
            Some("null"),
            None,
        ] {
            let authorized = enabled == "1" && origin == Some(ALLOWED);
            for (method, path, success) in [
                (Method::GET, "/download?size=1024", StatusCode::OK),
                (Method::POST, "/upload", StatusCode::OK),
                (
                    Method::POST,
                    "/webrtc/offer",
                    StatusCode::UNSUPPORTED_MEDIA_TYPE,
                ),
                (Method::OPTIONS, "/upload-stream", StatusCode::NO_CONTENT),
            ] {
                let mut request = client
                    .request(method.clone(), format!("{base}{path}"))
                    .header("Sec-Fetch-Site", "cross-site");
                if let Some(origin) = origin {
                    request = request.header("Origin", origin);
                }
                if path == "/upload" {
                    request = request.body(vec![9_u8; 1024]);
                }
                let response = request.send().await?;
                assert_eq!(
                    response.status(),
                    if authorized {
                        success
                    } else {
                        StatusCode::FORBIDDEN
                    },
                    "enabled={enabled} origin={origin:?} {path}"
                );
                if origin == Some(ALLOWED) {
                    assert_eq!(response.headers()["access-control-allow-origin"], ALLOWED);
                    assert_eq!(response.headers()["vary"], "Origin");
                } else {
                    assert!(
                        !response
                            .headers()
                            .contains_key("access-control-allow-origin")
                    );
                }
                if method == Method::OPTIONS && authorized {
                    assert!(
                        response.headers()["access-control-allow-headers"]
                            .to_str()?
                            .contains("X-Speedtest-Attempt-Id")
                    );
                }
                // Consume bodies so these checks do not hold download leases.
                let _ = response.bytes().await?;
            }
        }
        // Existing same-site behavior is unchanged without the opt-in.
        let response = client
            .get(format!("{base}/download?size=1024"))
            .header("Origin", ALLOWED)
            .header("Sec-Fetch-Site", "same-site")
            .send()
            .await?;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.bytes().await?.len(), 1024);
    }
    Ok(())
}

#[cfg(feature = "web-ui")]
#[tokio::test]
async fn central_page_csp_uses_only_parsed_node_origins() -> Result<()> {
    let nodes = r#"[
        {"id":"remote","base":"https://agent.example.net:9443/api/;script-src%20*"},
        {"id":"primary","base":"https://h1.example.net"},
        {"id":"relative","base":"/local"},
        {"id":"bad","base":"javascript:alert(1)"},
        {"id":"credentials","base":"https://user:pass@forbidden.example.net"},
        {"id":"opaque","base":"data:text/plain,test"}
    ]"#;
    let (_server, base, client) = start_server(&[("SPEEDTEST_NODES", nodes)]).await?;
    let response = client.get(format!("{base}/")).send().await?;
    let csp = response.headers()["content-security-policy"].to_str()?;
    assert!(csp.contains("https://agent.example.net:9443"));
    assert!(csp.contains("https://h1.example.net:8443"));
    for forbidden in [
        ";script-src *",
        "javascript:",
        "forbidden.example.net",
        "data:text",
        "/api/",
    ] {
        assert!(
            !csp.contains(forbidden),
            "unexpected CSP token: {forbidden}"
        );
    }
    assert_eq!(csp.matches("script-src '").count(), 1);
    Ok(())
}

#[tokio::test]
async fn upload_receipts_stream_before_eof_and_finish_with_exact_bytes() -> Result<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let (_server, base, client) = start_server(&[]).await?;
    let created: serde_json::Value = client
        .post(format!("{base}/upload-progress?lanes=1&warmupMs=0"))
        .send()
        .await?
        .json()
        .await?;
    let id = created["id"].as_str().context("progress id")?.to_owned();
    let mut telemetry = client
        .get(format!("{base}/upload-progress?id={id}"))
        .send()
        .await?;
    assert_eq!(telemetry.status(), StatusCode::OK);
    assert_eq!(telemetry.headers()["content-encoding"], "identity");
    let address = base.trim_start_matches("http://").to_owned();
    let (finish, finished) = tokio::sync::oneshot::channel();
    let producer_id = id.clone();
    let producer = tokio::spawn(async move {
        let mut socket = tokio::net::TcpStream::connect(&address).await?;
        socket.write_all(format!("POST /upload-stream?progressId={producer_id}&warmupMs=0 HTTP/1.1\r\nHost: {address}\r\nTransfer-Encoding: chunked\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\n").as_bytes()).await?;
        for _ in 0..6 {
            socket.write_all(b"4000\r\n").await?;
            socket.write_all(&vec![9; 16384]).await?;
            socket.write_all(b"\r\n").await?;
            tokio::time::sleep(Duration::from_millis(45)).await;
        }
        let _ = finished.await;
        socket.write_all(b"0\r\n\r\n").await?;
        let mut response = Vec::new();
        socket.read_to_end(&mut response).await?;
        Ok::<_, anyhow::Error>(response)
    });
    let mut buffer = String::new();
    let mut seen = false;
    for _ in 0..10 {
        let bytes = telemetry
            .chunk()
            .await?
            .context("progress ended before upload EOF")?;
        buffer.push_str(std::str::from_utf8(&bytes)?);
        while let Some(end) = buffer.find('\n') {
            let row: serde_json::Value = serde_json::from_str(&buffer[..end])?;
            buffer.drain(..=end);
            if row["bytes"].as_u64().unwrap_or(0) > 0 {
                assert_eq!(row["done"], false);
                assert!(
                    !producer.is_finished(),
                    "receipt waited for the final upload ACK"
                );
                seen = true;
            }
        }
        if seen {
            break;
        }
    }
    assert!(seen, "no live receipt before EOF");
    let _ = finish.send(());
    let response = producer.await??;
    let text = std::str::from_utf8(&response)?;
    assert!(text.starts_with("HTTP/1.1 200"), "{text}");
    let accounting: serde_json::Value =
        serde_json::from_str(text.split_once("\r\n\r\n").context("HTTP body")?.1)?;
    assert_eq!(accounting["bytes"], 6 * 16384);
    let mut final_row = None;
    while let Some(bytes) = telemetry.chunk().await? {
        buffer.push_str(std::str::from_utf8(&bytes)?);
        while let Some(end) = buffer.find('\n') {
            let row: serde_json::Value = serde_json::from_str(&buffer[..end])?;
            buffer.drain(..=end);
            if row["done"] == true {
                final_row = Some(row);
            }
        }
    }
    let last = final_row.context("missing final receipt")?;
    assert_eq!(last["bytes"], accounting["bytes"]);
    assert_eq!(last["failed"], false);
    let duplicate = client
        .post(format!("{base}/upload-stream?progressId={id}&warmupMs=0"))
        .body(vec![1; 1024])
        .send()
        .await?;
    assert_eq!(duplicate.status(), StatusCode::BAD_REQUEST);
    Ok(())
}

#[tokio::test]
async fn upload_progress_capacity_and_origin_guards_are_bounded() -> Result<()> {
    let (_server, base, client) = start_server(&[
        ("MAX_ACTIVE_PER_IP", "8"),
        ("ALLOWED_ORIGINS", "https://console.example.com"),
        ("SPEEDTEST_ALLOW_CROSS_SITE", "1"),
    ])
    .await?;
    let bad = client
        .post(format!("{base}/upload-progress"))
        .header("Origin", "https://unlisted.example.net")
        .header("Sec-Fetch-Site", "cross-site")
        .send()
        .await?;
    assert_eq!(bad.status(), StatusCode::FORBIDDEN);
    for invalid in ["0", "3", "9", "256", "abc"] {
        assert_eq!(
            client
                .post(format!("{base}/upload-progress?lanes={invalid}"))
                .send()
                .await?
                .status(),
            StatusCode::BAD_REQUEST
        );
    }
    for _ in 0..8 {
        assert_eq!(
            client
                .post(format!("{base}/upload-progress?lanes=8"))
                .send()
                .await?
                .status(),
            StatusCode::OK
        );
    }
    assert_eq!(
        client
            .post(format!("{base}/upload-progress"))
            .send()
            .await?
            .status(),
        StatusCode::TOO_MANY_REQUESTS
    );
    assert_eq!(
        client
            .get(format!("{base}/upload-progress?id=not-a-valid-id"))
            .send()
            .await?
            .status(),
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        client.get(format!("{base}/health")).send().await?.status(),
        StatusCode::OK
    );
    Ok(())
}
