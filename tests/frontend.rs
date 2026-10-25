use anyhow::{Context, Result, bail};
use reqwest::{Client, Method, StatusCode};
use std::{
    net::TcpListener,
    process::{Child, Command, Stdio},
    time::Duration,
};

const PAGE: &str = include_str!("../public/index.html");

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
        "data-result-mbps",
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

#[test]
fn speed_timestamps_and_runtime_placeholders_are_preserved() {
    for marker in [
        "concurrency, now - started)",
        "sampleBytes, 1, now - measurementStartAt)",
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
    let child = Command::new(env!("CARGO_BIN_EXE_speedtest-rust"))
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
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers().contains_key("content-security-policy"));
    let page = response.text().await?;
    assert!(page.contains("function drawSpeedCharts()"));
    assert!(page.contains("root.SpeedtestContracts = contracts;"));
    assert!(!page.contains("__NODE_CONFIG__") && !page.contains("__CSP_NONCE__"));
    assert!(!page.contains("__RTC_") && !page.contains("__MAX_RTC_"));
    for (method, uri, status, length) in [
        (Method::HEAD, "/", StatusCode::OK, Some(0)),
        (Method::GET, "/latency", StatusCode::NO_CONTENT, Some(0)),
        (Method::GET, "/favicon.svg", StatusCode::OK, None),
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
