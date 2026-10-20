use anyhow::{Context, Result, bail};
use speedtest_rust::rtc_sdp::is_public_ip;
use std::{
    collections::HashSet,
    env,
    net::{IpAddr, Ipv4Addr},
};

#[derive(Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub name: String,
    pub allowed_origins: HashSet<String>,
    pub public_cors: bool,
    pub trust_proxy: bool,
    pub trusted_proxy_ips: HashSet<String>,
    pub nodes: serde_json::Value,
    pub max_download: u64,
    pub max_upload: u64,
    pub max_stream_upload: u64,
    pub fixed_download_ms: u64,
    pub stream_download_ms: u64,
    pub upload_ms: u64,
    pub http_max_requests_per_socket: usize,
    pub http_keep_alive_ms: u64,
    pub http_headers_ms: u64,
    pub http_request_ms: u64,
    pub http_max_headers_count: usize,
    pub rate_max: u32,
    pub speed_rate_max: u32,
    pub rate_map_max: usize,
    pub max_active_global: usize,
    pub max_active_per_ip: usize,
    pub download_budget: u64,
    pub upload_budget: u64,
    pub budget_window_ms: u64,
    pub rtc_result_grace_ms: u64,
    pub rtc_result_samples_max: usize,
    pub rtc_down_tests_max: usize,
    pub rtc_control_message_bytes: usize,
    pub rtc_control_messages_max: usize,
    pub rtc_test_messages_max: usize,
    pub rtc_inbound_bytes_max: usize,
    pub rtc_offer_body_max: usize,
    pub control_single_bytes: u64,
    pub control_fragment_bytes: u64,
    pub control_fragment_count: u64,
    pub control_reassembled_bytes: u64,
    pub rtc_experimental: bool,
    pub rtc_legacy_control: bool,
    pub rtc_local_test: bool,
    pub rtc_udp4: Option<Ipv4Addr>,
    pub max_rtc_peers: usize,
    pub max_rtc_peers_per_ip: usize,
    pub rtc_cancel_max_per_ip: u32,
    pub rtc_tombstones_max: usize,
    pub rtc_offer_max_per_ip: u32,
    pub rtc_offer_rate_max: usize,
    pub rtc_negotiation_ms: u64,
    pub rtc_session_ms: u64,
    pub rtc_measurement_ms: u64,
    pub rtc_ice_gather_ms: u64,
}

fn integer(name: &str, default: u64, min: u64, max: u64) -> Result<u64> {
    let raw = env::var(name).unwrap_or_default();
    let value = if raw.is_empty() {
        default
    } else {
        raw.parse().with_context(|| format!("invalid {name}"))?
    };
    if !(min..=max).contains(&value) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(value)
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let host = env::var("SPEEDTEST_HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let trust_proxy = env::var("TRUST_PROXY_HEADERS").as_deref() == Ok("1");
        if trust_proxy && matches!(host.as_str(), "0.0.0.0" | "::" | "") {
            bail!("refusing proxy headers on a public listener");
        }
        let trusted_proxy_ips = env::var("TRUSTED_PROXY_IPS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect();
        let allowed_origins = env::var("ALLOWED_ORIGINS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect();
        let mut nodes = serde_json::from_str::<serde_json::Value>(
            &env::var("SPEEDTEST_NODES").unwrap_or_default(),
        )
        .unwrap_or_else(|_| {
            serde_json::json!([
                {"id":"local","label":"Local server","base":"","rtcTargetMs":25}
            ])
        });
        if nodes.as_array().is_none_or(|array| array.is_empty()) {
            nodes = serde_json::json!([
                {"id":"local","label":"Local server","base":"","rtcTargetMs":25}
            ]);
        }
        if env::var("ENABLE_PROBE_ONLY_NODE").as_deref() == Ok("0")
            && let Some(array) = nodes.as_array_mut()
        {
            array.retain(|node| node["id"] != "probe-only");
        }
        let max_upload = integer("MAX_UPLOAD", 64 << 20, 1024, 256 << 20)?;
        let upload_ms = integer("MAX_UPLOAD_MS", 25_000, 1000, 120_000)?;
        let rtc_session_ms = integer("MAX_RTC_SESSION_MS", 90_000, 10_000, 300_000)?;
        let rtc_udp4 = match env::var("SPEEDTEST_WEBRTC_UDP4").unwrap_or_default() {
            raw if raw.trim().is_empty() => None,
            raw => {
                let parsed: Ipv4Addr = raw
                    .trim()
                    .parse()
                    .context("invalid SPEEDTEST_WEBRTC_UDP4")?;
                if !is_public_ip(IpAddr::V4(parsed)) {
                    bail!("SPEEDTEST_WEBRTC_UDP4 must be a public IPv4 address");
                }
                Some(parsed)
            }
        };
        let rtc_local_test =
            env::var("SPEEDTEST_RUST_ALLOW_PRIVATE_RTC_TEST").as_deref() == Ok("1");
        if rtc_local_test && host != "127.0.0.1" {
            bail!("private WebRTC candidates are allowed only on a loopback listener");
        }
        Ok(Self {
            host,
            port: integer("PORT", 3010, 1, 65535)? as u16,
            name: env::var("SPEEDTEST_NODE_NAME").unwrap_or_else(|_| "speedtest-node".into()),
            allowed_origins,
            public_cors: env::var("PUBLIC_CORS").as_deref() == Ok("1"),
            trust_proxy,
            trusted_proxy_ips,
            nodes,
            max_download: integer("MAX_DOWNLOAD", 256 << 20, 96 << 20, 1 << 30)?,
            max_upload,
            max_stream_upload: integer("MAX_STREAM_UPLOAD", 2 << 30, max_upload, 4 << 30)?,
            fixed_download_ms: integer("MAX_FIXED_DOWNLOAD_MS", 120_000, 1000, 300_000)?,
            stream_download_ms: integer("MAX_DOWNLOAD_STREAM_MS", 15_000, 1000, 30_000)?,
            upload_ms,
            http_max_requests_per_socket: integer("HTTP_MAX_REQUESTS_PER_SOCKET", 100, 1, 1000)?
                as usize,
            http_keep_alive_ms: integer("HTTP_KEEP_ALIVE_TIMEOUT_MS", 5000, 1000, 30000)?,
            http_headers_ms: integer("HTTP_HEADERS_TIMEOUT_MS", 10000, 1000, 60000)?,
            http_request_ms: integer("HTTP_REQUEST_TIMEOUT_MS", 35000, upload_ms, 180000)?,
            http_max_headers_count: integer("HTTP_MAX_HEADERS_COUNT", 64, 16, 256)? as usize,
            rate_max: integer("RATE_MAX", 80, 10, 1000)? as u32,
            speed_rate_max: integer("SPEED_RATE_MAX", 240, 16, 2000)? as u32,
            rate_map_max: integer("RATE_MAP_MAX", 10_000, 100, 100_000)? as usize,
            max_active_global: integer("MAX_ACTIVE_GLOBAL", 128, 8, 1024)? as usize,
            max_active_per_ip: integer("MAX_ACTIVE_PER_IP", 16, 8, 128)? as usize,
            download_budget: integer("DOWNLOAD_BYTE_BUDGET_PER_IP", 4 << 30, 1 << 20, 32 << 30)?,
            upload_budget: integer("UPLOAD_BYTE_BUDGET_PER_IP", 4 << 30, 1 << 20, 32 << 30)?,
            budget_window_ms: integer("BYTE_BUDGET_WINDOW_MS", 60_000, 10_000, 300_000)?,
            rtc_result_grace_ms: integer("RTC_RESULT_GRACE_MS", 300, 50, 2000)?,
            rtc_result_samples_max: integer("MAX_RTC_RESULT_SAMPLES", 20_000, 100, 50_000)?
                as usize,
            rtc_down_tests_max: integer("MAX_DOWN_TESTS_PER_SOCKET", 2, 1, 8)? as usize,
            rtc_control_message_bytes: integer(
                "MAX_RTC_CONTROL_MESSAGE_BYTES",
                16_384,
                256,
                65_536,
            )? as usize,
            rtc_control_messages_max: integer("MAX_RTC_CONTROL_MESSAGES_PER_PEER", 256, 4, 512)?
                as usize,
            rtc_test_messages_max: integer(
                "MAX_RTC_TEST_MESSAGES_PER_PEER",
                30_000,
                24_000,
                50_000,
            )? as usize,
            rtc_inbound_bytes_max: integer(
                "MAX_RTC_INBOUND_BYTES_PER_PEER",
                4 << 20,
                256 << 10,
                16 << 20,
            )? as usize,
            rtc_offer_body_max: integer("MAX_RTC_OFFER_BODY_BYTES", 256 << 10, 16 << 10, 1 << 20)?
                as usize,
            control_single_bytes: integer("RTC_CONTROL_SINGLE_MESSAGE_BYTES", 900, 512, 1200)?,
            control_fragment_bytes: integer("RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES", 512, 256, 768)?,
            control_fragment_count: integer("MAX_RTC_CONTROL_FRAGMENT_COUNT", 2048, 8, 4096)?,
            control_reassembled_bytes: integer(
                "MAX_RTC_REASSEMBLED_CONTROL_BYTES",
                2 << 20,
                64 << 10,
                4 << 20,
            )?,
            rtc_experimental: env::var("SPEEDTEST_RUST_RTC_EXPERIMENTAL").as_deref() == Ok("1"),
            rtc_legacy_control: env::var("SPEEDTEST_RTC_LEGACY_CONTROL").as_deref() == Ok("1"),
            rtc_local_test,
            rtc_udp4,
            max_rtc_peers: integer("MAX_RTC_PEERS", 16, 3, 256)? as usize,
            max_rtc_peers_per_ip: integer("MAX_RTC_PEERS_PER_IP", 4, 3, 64)? as usize,
            rtc_cancel_max_per_ip: integer("RTC_CANCEL_MAX_PER_IP", 80, 10, 1000)? as u32,
            rtc_tombstones_max: integer("MAX_RTC_ATTEMPT_TOMBSTONES", 2048, 128, 10000)? as usize,
            rtc_offer_max_per_ip: integer("RTC_OFFER_MAX_PER_IP", 20, 3, 200)? as u32,
            rtc_offer_rate_max: integer("RTC_OFFER_RATE_MAX", 10_000, 100, 100_000)? as usize,
            rtc_negotiation_ms: integer("MAX_RTC_NEGOTIATION_MS", 15_000, 1000, 60_000)?,
            rtc_session_ms,
            rtc_measurement_ms: integer(
                "MAX_RTC_MEASUREMENT_MS",
                60_000.min(rtc_session_ms - 5000),
                1000,
                rtc_session_ms - 5000,
            )?,
            rtc_ice_gather_ms: integer("RTC_ICE_GATHER_MAX_MS", 50, 5, 5000)?,
        })
    }

    pub fn bind_addr(&self) -> Result<std::net::SocketAddr> {
        Ok((
            self.host
                .parse::<IpAddr>()
                .context("SPEEDTEST_HOST must be an IP literal")?,
            self.port,
        )
            .into())
    }
}
