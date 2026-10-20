//! Experimental Rust WebRTC signaling. Not enabled for production until the
//! paired probe and stability protocol passes cross-implementation tests.
use crate::{
    config::Config,
    rate::{RateKind, RateTable},
};
use axum::http::StatusCode;
use bytes::Bytes;
use futures_util::future::join_all;
use speedtest_rust::{
    protocol::{FRAME_BYTES, Frame},
    rtc_control::{Reassembler, serialize_down_result, serialize_legacy_down_result},
    stats::percentile,
};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, VecDeque},
    sync::{Arc, Mutex, Weak},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};
use webrtc::{
    api::{APIBuilder, setting_engine::SettingEngine},
    data_channel::{RTCDataChannel, data_channel_message::DataChannelMessage},
    ice::network_type::NetworkType,
    ice_transport::ice_candidate_type::RTCIceCandidateType,
    peer_connection::{
        RTCPeerConnection, configuration::RTCConfiguration,
        peer_connection_state::RTCPeerConnectionState,
        sdp::session_description::RTCSessionDescription,
    },
};

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct Key {
    ip: String,
    attempt: String,
}

struct Attempt {
    token: CancellationToken,
    connection: tokio::sync::Mutex<Option<Arc<RTCPeerConnection>>>,
    session: tokio::sync::Mutex<Option<Arc<Session>>>,
}

struct Session {
    service: Weak<Mutex<Inner>>,
    cfg: Config,
    key: Key,
    token: CancellationToken,
    control: tokio::sync::Mutex<Option<Arc<RTCDataChannel>>>,
    test: tokio::sync::Mutex<Option<Arc<RTCDataChannel>>>,
    inbound_control: std::sync::atomic::AtomicUsize,
    inbound_test: std::sync::atomic::AtomicUsize,
    inbound_bytes: std::sync::atomic::AtomicUsize,
    down_starts: std::sync::atomic::AtomicUsize,
    pending_down_start: tokio::sync::Mutex<Option<serde_json::Value>>,
    reassembler: Mutex<Reassembler>,
    tests: Mutex<HashMap<(u32, u32), Arc<DownTest>>>,
    epoch: Instant,
}

struct DownTest {
    test_id: String,
    session_id: String,
    session_hash: u32,
    test_hash: u32,
    planned: usize,
    interval_ms: f64,
    duration_ms: u64,
    token: CancellationToken,
    notify: tokio::sync::Notify,
    data: Mutex<DownData>,
}

#[derive(Default)]
struct DownData {
    scheduled: usize,
    skipped: usize,
    actual_sent: usize,
    sent_seqs: BTreeSet<u32>,
    sent_times: Vec<f64>,
    acks: BTreeMap<u32, f64>,
    upload_received: BTreeSet<u32>,
    up_max_seq_seen: Option<u32>,
    upload_complete: Option<serde_json::Value>,
    upload_complete_at: Option<Instant>,
    upload_complete_at_ms: Option<f64>,
    invalid_reasons: Vec<&'static str>,
    download_done: bool,
    finalized: bool,
}

fn lock<T>(state: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    state
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.
}

fn number_field(value: &serde_json::Value, field: &str) -> Option<usize> {
    number_value(&value[field])
}

fn number_value(value: &serde_json::Value) -> Option<usize> {
    value.as_f64().and_then(|n| {
        if n.is_finite() && (0. ..=9_007_199_254_740_991.).contains(&n) && n.fract() == 0. {
            #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
            Some(n as usize)
        } else {
            None
        }
    })
}

// The original Node validator emits *all* applicable reason codes, even when
// a count is malformed. These helpers reproduce its JSON/JS comparisons only
// at that compatibility boundary; accepted counts still use typed Rust values.
fn js_string(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Null => "null".into(),
        serde_json::Value::Bool(value) => value.to_string(),
        serde_json::Value::String(value) => value.clone(),
        serde_json::Value::Number(value) => value.to_string(),
        serde_json::Value::Array(values) => values
            .iter()
            .map(|value| {
                if value.is_null() {
                    String::new()
                } else {
                    js_string(value)
                }
            })
            .collect::<Vec<_>>()
            .join(","),
        serde_json::Value::Object(_) => "[object Object]".into(),
    }
}

fn js_number(value: &serde_json::Value) -> Option<f64> {
    match value {
        serde_json::Value::Null => Some(0.),
        serde_json::Value::Bool(value) => Some(if *value { 1. } else { 0. }),
        serde_json::Value::Number(value) => value.as_f64(),
        _ => {
            let text = js_string(value);
            let text = text.trim();
            if text.is_empty() {
                Some(0.)
            } else if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
                u64::from_str_radix(hex, 16)
                    .ok()
                    .map(|number| number as f64)
            } else {
                text.parse::<f64>().ok()
            }
        }
    }
}

fn js_string_primitive(value: &serde_json::Value) -> bool {
    value.is_string() || value.is_array() || value.is_object()
}

fn js_nonnegative_integer(value: &serde_json::Value) -> bool {
    value
        .as_f64()
        .is_some_and(|number| number.is_finite() && number >= 0. && number.fract() == 0.)
}

fn js_add_equals(
    a: &serde_json::Value,
    b: &serde_json::Value,
    expected: &serde_json::Value,
) -> bool {
    if js_string_primitive(a) || js_string_primitive(b) {
        expected
            .as_str()
            .is_some_and(|value| value == format!("{}{}", js_string(a), js_string(b)))
    } else {
        a.as_f64()
            .or_else(|| js_number(a))
            .zip(b.as_f64().or_else(|| js_number(b)))
            .is_some_and(|(left, right)| expected.as_f64() == Some(left + right))
    }
}

fn js_greater_than(a: &serde_json::Value, b: &serde_json::Value) -> bool {
    if js_string_primitive(a) && js_string_primitive(b) {
        js_string(a) > js_string(b)
    } else {
        js_number(a)
            .zip(js_number(b))
            .is_some_and(|(left, right)| left > right)
    }
}

fn pacing(times: &[f64], target: f64) -> serde_json::Value {
    if times.len() < 2 {
        return serde_json::Value::Null;
    }
    let intervals: Vec<_> = times
        .windows(2)
        .map(|pair| pair[1] - pair[0])
        .filter(|value| value.is_finite() && *value >= 0.)
        .collect();
    if intervals.is_empty() {
        return serde_json::Value::Null;
    }
    let drifts: Vec<_> = intervals
        .iter()
        .map(|interval| (interval - target).abs())
        .collect();
    let bad_count = drifts
        .iter()
        .filter(|drift| **drift > (target * 2.).max(20.))
        .count();
    serde_json::json!({"targetMs":target,"samples":intervals.len(),
        "p50Interval":percentile(&intervals,0.5),"p95Interval":percentile(&intervals,0.95),
        "p99Interval":percentile(&intervals,0.99),"p95Drift":percentile(&drifts,0.95),
        "maxDrift":drifts.iter().copied().reduce(f64::max),"badCount":bad_count,
        "badRate":bad_count as f64 / intervals.len() as f64})
}

// Like Node's monitorEventLoopDelay({ resolution: 1 }), this samples how late
// a scheduled timer runs. It is an executor/timer latency estimate, not packet
// RTT, and should not be confused with the sender's pacing distribution.
async fn sample_executor_delay(token: CancellationToken) -> Vec<f64> {
    let mut samples = Vec::with_capacity(4096);
    let interval = Duration::from_millis(1);
    loop {
        let due = Instant::now() + interval;
        tokio::select! {
            () = tokio::time::sleep_until(tokio::time::Instant::from_std(due)) => {},
            () = token.cancelled() => break,
        }
        if samples.len() < 65_536 {
            samples.push(Instant::now().saturating_duration_since(due).as_secs_f64() * 1000.);
        }
    }
    samples
}

fn summarize_executor_delay(samples: &[f64]) -> serde_json::Value {
    if samples.is_empty() {
        return serde_json::Value::Null;
    }
    serde_json::json!({
        "minMs":samples.iter().copied().reduce(f64::min),
        "meanMs":samples.iter().sum::<f64>() / samples.len() as f64,
        "maxMs":samples.iter().copied().reduce(f64::max),
        "p50Ms":percentile(samples,0.5),
        "p95Ms":percentile(samples,0.95),
        "p99Ms":percentile(samples,0.99),
    })
}

impl Session {
    fn new(service: RtcService, key: Key, token: CancellationToken) -> Arc<Self> {
        let fragment_count = service.cfg.control_fragment_count as usize;
        let reassembled_bytes = service.cfg.control_reassembled_bytes as usize;
        Arc::new(Self {
            service: Arc::downgrade(&service.inner),
            cfg: service.cfg,
            key,
            token,
            control: tokio::sync::Mutex::new(None),
            test: tokio::sync::Mutex::new(None),
            inbound_control: std::sync::atomic::AtomicUsize::new(0),
            inbound_test: std::sync::atomic::AtomicUsize::new(0),
            inbound_bytes: std::sync::atomic::AtomicUsize::new(0),
            down_starts: std::sync::atomic::AtomicUsize::new(0),
            pending_down_start: tokio::sync::Mutex::new(None),
            reassembler: Mutex::new(Reassembler::new(fragment_count, reassembled_bytes)),
            tests: Mutex::new(HashMap::new()),
            epoch: service.epoch,
        })
    }

    fn service(&self) -> Option<RtcService> {
        self.service.upgrade().map(|inner| RtcService {
            inner,
            cfg: self.cfg.clone(),
            epoch: self.epoch,
        })
    }

    async fn install(self: Arc<Self>, channel: Arc<RTCDataChannel>) {
        let label = channel.label().to_owned();
        if label == "control" {
            *self.control.lock().await = Some(Arc::clone(&channel));
        } else if label == "test" {
            debug!(ordered = channel.ordered(), max_retransmits = ?channel.max_retransmits(),
                "WebRTC test channel policy");
            *self.test.lock().await = Some(Arc::clone(&channel));
            let pending = self.pending_down_start.lock().await.take();
            if let Some(pending) = pending {
                self.start_down(pending).await;
            }
        } else {
            warn!(%label, "unexpected WebRTC data channel");
            return;
        }
        let session = Arc::downgrade(&self);
        channel.on_message(Box::new(move |message| {
            let session = session.upgrade();
            let label = label.clone();
            Box::pin(async move {
                let Some(session) = session else { return };
                if let Err(reason) = session.on_message(&label, message).await {
                    warn!(reason, "WebRTC protocol violation");
                    if let Some(service) = session.service() {
                        service.cleanup(&session.key).await;
                    }
                }
            })
        }));
    }

    async fn on_message(
        self: &Arc<Self>,
        label: &str,
        message: DataChannelMessage,
    ) -> Result<(), &'static str> {
        use std::sync::atomic::Ordering;
        if self.token.is_cancelled() {
            return Ok(());
        }
        let bytes = message.data.len();
        if self
            .inbound_bytes
            .fetch_add(bytes, Ordering::Relaxed)
            .saturating_add(bytes)
            > self.cfg.rtc_inbound_bytes_max
        {
            return Err("inbound-byte-limit");
        }
        if label == "test" {
            if bytes != FRAME_BYTES
                || self.inbound_test.fetch_add(1, Ordering::Relaxed)
                    >= self.cfg.rtc_test_messages_max
            {
                return Err("test-message-limit");
            }
            let frame = Frame::decode(&message.data).ok_or("invalid-frame")?;
            if frame.kind == 1 {
                let state = lock(&self.tests)
                    .get(&(frame.session_hash, frame.test_hash))
                    .cloned();
                let reordered = state.is_some_and(|test| {
                    let mut data = lock(&test.data);
                    if (frame.seq as usize) < test.planned {
                        data.upload_received.insert(frame.seq);
                    }
                    let seen = data.up_max_seq_seen.is_some_and(|last| frame.seq < last);
                    data.up_max_seq_seen = Some(
                        data.up_max_seq_seen
                            .map_or(frame.seq, |last| last.max(frame.seq)),
                    );
                    seen
                });
                let ack = Frame {
                    kind: 2,
                    flags: u8::from(reordered),
                    actual_sent: 0,
                    sent_at: if frame.sent_at.is_nan() {
                        0.
                    } else {
                        frame.sent_at
                    },
                    ..frame
                };
                let channel = self.test.lock().await.clone();
                if let Some(channel) = channel {
                    let started = tracing::enabled!(tracing::Level::DEBUG).then(Instant::now);
                    let sent = channel.send(&Bytes::copy_from_slice(&ack.encode())).await;
                    if let Some(started) = started
                        && started.elapsed() >= Duration::from_millis(20)
                    {
                        debug!(
                            seq = frame.seq,
                            elapsed_ms = started.elapsed().as_secs_f64() * 1000.,
                            "WebRTC upAck send delayed"
                        );
                    }
                    if let Err(error) = sent {
                        warn!(%error, "WebRTC upAck send failed");
                    }
                }
            } else if frame.kind == 4 {
                let test = lock(&self.tests)
                    .get(&(frame.session_hash, frame.test_hash))
                    .cloned();
                if let Some(test) = test {
                    let rtt = ms(self.epoch) - frame.sent_at;
                    let accepted = {
                        let mut data = lock(&test.data);
                        if data.sent_seqs.contains(&frame.seq)
                            && !data.acks.contains_key(&frame.seq)
                        {
                            data.acks.insert(frame.seq, rtt);
                            true
                        } else {
                            false
                        }
                    };
                    if accepted {
                        let sample = Frame {
                            kind: 5,
                            flags: 0,
                            session_hash: frame.session_hash,
                            test_hash: frame.test_hash,
                            seq: frame.seq,
                            sent_at: rtt,
                            actual_sent: 0,
                        };
                        let channel = self.test.lock().await.clone();
                        if let Some(channel) = channel {
                            let started =
                                tracing::enabled!(tracing::Level::DEBUG).then(Instant::now);
                            let sent = channel
                                .send(&Bytes::copy_from_slice(&sample.encode()))
                                .await;
                            if let Some(started) = started
                                && started.elapsed() >= Duration::from_millis(20)
                            {
                                debug!(
                                    seq = frame.seq,
                                    elapsed_ms = started.elapsed().as_secs_f64() * 1000.,
                                    "WebRTC downSample send delayed"
                                );
                            }
                            if let Err(error) = sent {
                                warn!(%error, "WebRTC downSample send failed");
                            }
                        }
                    }
                }
            }
        } else {
            if bytes > self.cfg.rtc_control_message_bytes
                || self.inbound_control.fetch_add(1, Ordering::Relaxed)
                    >= self.cfg.rtc_control_messages_max
            {
                return Err("control-message-limit");
            }
            let Ok(mut parsed) = serde_json::from_slice::<serde_json::Value>(&message.data) else {
                // Node silently ignores malformed control JSON.
                return Ok(());
            };
            if parsed["type"] == "controlFragment" {
                if parsed["messageType"] != "uploadComplete" {
                    return Err("unsupported-control-fragment");
                }
                if tracing::enabled!(tracing::Level::DEBUG) {
                    debug!(
                        index = ?parsed["index"].as_u64(),
                        count = ?parsed["count"].as_u64(),
                        "WebRTC uploadComplete fragment received"
                    );
                }
                let assembled = lock(&self.reassembler)
                    .accept(&parsed)
                    .map_err(|_| "invalid-control-fragment")?;
                let Some(message) = assembled else {
                    return Ok(());
                };
                debug!("WebRTC uploadComplete fragments reassembled");
                parsed = message;
            }
            if parsed["type"] == "close" {
                if let Some(service) = self.service() {
                    service.cleanup(&self.key).await;
                }
            } else if parsed["type"] == "uploadComplete" {
                self.accept_upload_complete(parsed);
            } else if parsed["type"] == "downStart" {
                if self.down_starts.fetch_add(1, Ordering::Relaxed) >= self.cfg.rtc_down_tests_max {
                    return Err("down-start-limit");
                }
                let test_ready = self.test.lock().await.is_some();
                if test_ready {
                    self.start_down(parsed).await;
                } else {
                    *self.pending_down_start.lock().await = Some(parsed);
                }
            }
        }
        Ok(())
    }

    async fn start_down(self: &Arc<Self>, msg: serde_json::Value) {
        let rate = msg["rate"]
            .as_f64()
            .filter(|v| v.is_finite())
            .unwrap_or(200.)
            .floor()
            .clamp(1., 500.);
        let duration = msg["durationMs"]
            .as_f64()
            .filter(|v| v.is_finite())
            .unwrap_or(5000.)
            .floor()
            .clamp(100., self.cfg.rtc_measurement_ms as f64);
        let planned = (rate * duration / 1000.).floor() as usize;
        if planned == 0 {
            warn!("zero-length WebRTC measurement request");
            return;
        }
        let test_id = msg["testId"].as_str().unwrap_or("").to_owned();
        let session_id = msg["sessionId"].as_str().unwrap_or("").to_owned();
        let session_hash = msg["sessionHash"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .unwrap_or_else(|| speedtest_rust::protocol::hash_string32(&session_id));
        let test_hash = msg["testHash"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .unwrap_or_else(|| speedtest_rust::protocol::hash_string32(&test_id));
        let key = (session_hash, test_hash);
        let measurement = Arc::new(DownTest {
            test_id: test_id.clone(),
            session_id: session_id.clone(),
            session_hash,
            test_hash,
            planned,
            interval_ms: 1000. / rate,
            duration_ms: duration as u64,
            token: self.token.child_token(),
            notify: tokio::sync::Notify::new(),
            data: Mutex::new(DownData::default()),
        });
        let inserted = {
            let mut tests = lock(&self.tests);
            if tests.len() >= self.cfg.rtc_down_tests_max || tests.contains_key(&key) {
                false
            } else {
                tests.insert(key, Arc::clone(&measurement));
                true
            }
        };
        if !inserted {
            let control = self.control.lock().await.clone();
            if let Some(control) = control {
                let _ = control
                    .send_text(
                        "{\"type\":\"error\",\"error\":\"duplicate or excessive down test\"}",
                    )
                    .await;
            }
            return;
        }
        let identity = serde_json::json!({"type":"measurementReady","testId":msg["testId"],
            "sessionId":msg["sessionId"],"sessionHash":msg["sessionHash"],"testHash":msg["testHash"]});
        let control = self.control.lock().await.clone();
        if let Some(channel) = control
            && let Err(error) = channel.send_text(identity.to_string()).await
        {
            warn!(%error, "measurementReady send failed");
        }
        let test_channel = self.test.lock().await.clone();
        if let Some(test_channel) = test_channel {
            let sampler = tokio::spawn(sample_executor_delay(measurement.token.clone()));
            let session = Arc::clone(self);
            let state = Arc::clone(&measurement);
            let scheduler = tokio::spawn(async move {
                session.down_scheduler(state, test_channel).await;
            });
            let session = Arc::clone(self);
            tokio::spawn(async move {
                session
                    .down_finalizer(measurement, scheduler, sampler)
                    .await;
            });
        }
    }

    async fn down_scheduler(self: Arc<Self>, test: Arc<DownTest>, channel: Arc<RTCDataChannel>) {
        debug!("WebRTC downlink diagnostics active");
        let started = Instant::now();
        let mut next = 0_usize;
        let mut max_buffered_bytes = 0_usize;
        while next < test.planned && !test.token.is_cancelled() {
            let target = started + Duration::from_secs_f64(next as f64 * test.interval_ms / 1000.);
            tokio::select! {
                () = tokio::time::sleep_until(tokio::time::Instant::from_std(target)) => {},
                () = test.token.cancelled() => break,
            }
            let due = ((ms(started) / test.interval_ms).floor() as usize).min(test.planned - 1);
            if next < due {
                lock(&test.data).skipped += due - next;
                next = due;
            }
            let seq = next as u32;
            let sent_at = ms(self.epoch);
            {
                let mut data = lock(&test.data);
                data.scheduled += 1;
                data.sent_seqs.insert(seq);
            }
            let frame = Frame {
                kind: 3,
                flags: 0,
                session_hash: test.session_hash,
                test_hash: test.test_hash,
                seq,
                sent_at,
                actual_sent: 0,
            };
            let payload = Bytes::copy_from_slice(&frame.encode());
            // On demand, distinguish SCTP send/backpressure stalls from loss
            // after a send returns. Avoid packet-level logging at normal levels.
            let send_started = tracing::enabled!(tracing::Level::DEBUG).then(Instant::now);
            let sent = tokio::select! {
                () = test.token.cancelled() => false,
                result = tokio::time::timeout(Duration::from_secs(2), channel.send(&payload)) =>
                    result.is_ok_and(|sent| sent.is_ok()),
            };
            if let Some(send_started) = send_started {
                let elapsed = send_started.elapsed();
                if elapsed >= Duration::from_millis(20) {
                    debug!(
                        seq,
                        elapsed_ms = elapsed.as_secs_f64() * 1000.,
                        "WebRTC downlink send delayed"
                    );
                }
            }
            if tracing::enabled!(tracing::Level::DEBUG) && seq.is_multiple_of(10) {
                let buffered_bytes = channel.buffered_amount().await;
                max_buffered_bytes = max_buffered_bytes.max(buffered_bytes);
                if buffered_bytes >= 128 * 10 {
                    debug!(seq, buffered_bytes, "WebRTC downlink buffered data");
                }
            }
            {
                let mut data = lock(&test.data);
                if sent {
                    data.actual_sent += 1;
                    data.sent_times.push(ms(self.epoch));
                } else {
                    data.sent_seqs.remove(&seq);
                    if !test.token.is_cancelled() {
                        data.invalid_reasons.push("DOWNLOAD_SEND_FAILED");
                    }
                }
            }
            if !sent {
                break;
            }
            next += 1;
        }
        debug!(max_buffered_bytes, "WebRTC downlink maximum buffered data");
        {
            let mut data = lock(&test.data);
            if test.token.is_cancelled() {
                data.skipped = test.planned.saturating_sub(data.scheduled);
            }
            data.download_done = !test.token.is_cancelled()
                && data.scheduled + data.skipped == test.planned
                && data.actual_sent == data.sent_seqs.len();
            if !data.download_done && !test.token.is_cancelled() {
                data.invalid_reasons.push("DOWNLOAD_SCHEDULER_INCOMPLETE");
            }
        }
        test.notify.notify_one();
    }

    fn accept_upload_complete(&self, msg: serde_json::Value) {
        let Some(session_hash) =
            number_field(&msg, "sessionHash").and_then(|n| u32::try_from(n).ok())
        else {
            return;
        };
        let Some(test_hash) = number_field(&msg, "testHash").and_then(|n| u32::try_from(n).ok())
        else {
            return;
        };
        let Some(test) = lock(&self.tests).get(&(session_hash, test_hash)).cloned() else {
            return;
        };
        let mut data = lock(&test.data);
        if data.finalized {
            return;
        }
        if data.upload_complete.is_some() {
            data.invalid_reasons.push("UPLOAD_COMPLETE_DUPLICATE");
            test.notify.notify_one();
            return;
        }
        let fields = [
            ("planned", "UPLOAD_PLANNED_INVALID"),
            ("scheduled", "UPLOAD_SCHEDULED_INVALID"),
            ("skipped", "UPLOAD_SKIPPED_INVALID"),
            ("actualSent", "UPLOAD_ACTUALSENT_INVALID"),
        ];
        for (field, reason) in fields {
            if !js_nonnegative_integer(&msg[field]) {
                data.invalid_reasons.push(reason);
            }
        }
        if msg["planned"].as_f64() != Some(test.planned as f64) {
            data.invalid_reasons.push("UPLOAD_PLANNED_MISMATCH");
        }
        if !js_add_equals(&msg["scheduled"], &msg["skipped"], &msg["planned"]) {
            data.invalid_reasons.push("UPLOAD_SCHEDULER_ACCOUNTING");
        }
        if js_greater_than(&msg["actualSent"], &msg["scheduled"]) {
            data.invalid_reasons.push("UPLOAD_SCHEDULER_BOUNDS");
        }
        match msg["sentSeqs"].as_array() {
            Some(sequences) if msg["actualSent"].as_f64() == Some(sequences.len() as f64) => {
                let mut unique = BTreeSet::new();
                if sequences.iter().any(|value| {
                    !js_nonnegative_integer(value)
                        || number_value(value)
                            .and_then(|n| u32::try_from(n).ok())
                            .is_none_or(|seq| seq as usize >= test.planned || !unique.insert(seq))
                }) {
                    data.invalid_reasons.push("UPLOAD_SENT_SEQS_INVALID");
                }
            }
            _ => data.invalid_reasons.push("UPLOAD_SENT_SEQS_COUNT_MISMATCH"),
        }
        if msg["testId"] != test.test_id || msg["sessionId"] != test.session_id {
            data.invalid_reasons.push("UPLOAD_COMPLETE_ID_MISMATCH");
        }
        if !data.invalid_reasons.is_empty() {
            data.invalid_reasons.push("UPLOAD_COMPLETE_INVALID");
            test.notify.notify_one();
            return;
        }
        data.upload_complete_at = Some(Instant::now());
        data.upload_complete_at_ms = Some(ms(self.epoch));
        data.upload_complete = Some(msg);
        test.notify.notify_one();
    }

    async fn down_finalizer(
        self: Arc<Self>,
        test: Arc<DownTest>,
        scheduler: tokio::task::JoinHandle<()>,
        sampler: tokio::task::JoinHandle<Vec<f64>>,
    ) {
        let deadline = Instant::now() + Duration::from_millis(test.duration_ms + 2000);
        let complete = loop {
            if test.token.is_cancelled() {
                return;
            }
            let (download_done, upload_at, invalid) = {
                let state = lock(&test.data);
                (
                    state.download_done,
                    state.upload_complete_at,
                    !state.invalid_reasons.is_empty(),
                )
            };
            if invalid {
                break false;
            }
            if download_done
                && upload_at.is_some_and(|at| {
                    at.elapsed() >= Duration::from_millis(self.cfg.rtc_result_grace_ms)
                })
            {
                break true;
            }
            // An accepted completion cancels Node's missing-upload deadline.
            // Its grace may legitimately extend past that original deadline.
            if upload_at.is_none() && Instant::now() >= deadline {
                let mut data = lock(&test.data);
                if data.upload_complete.is_none() {
                    data.invalid_reasons.push("UPLOAD_COMPLETE_MISSING");
                } else if !download_done {
                    data.invalid_reasons.push("DOWNLOAD_SCHEDULER_INCOMPLETE");
                }
                break false;
            }
            let wake = upload_at.map_or(deadline, |at| {
                at + Duration::from_millis(self.cfg.rtc_result_grace_ms)
            });
            tokio::select! {
                () = test.notify.notified() => {},
                () = tokio::time::sleep_until(tokio::time::Instant::from_std(wake)) => {},
                () = test.token.cancelled() => return,
            }
        };
        test.token.cancel();
        if let Err(error) = scheduler.await {
            warn!(%error, "WebRTC download scheduler failed");
            lock(&test.data)
                .invalid_reasons
                .push("DOWNLOAD_SCHEDULER_INCOMPLETE");
        }
        let delays = match sampler.await {
            Ok(samples) => samples,
            Err(error) => {
                warn!(%error, "WebRTC executor delay sampler failed");
                Vec::new()
            }
        };
        let result = {
            let mut data = lock(&test.data);
            if data.finalized {
                return;
            }
            data.finalized = true;
            // A duplicate completion or scheduler failure may arrive while
            // joining the tasks; never publish a contradictory true result.
            let complete = complete && data.invalid_reasons.is_empty() && data.download_done;
            let samples: Vec<_> = data
                .acks
                .iter()
                .take(self.cfg.rtc_result_samples_max)
                .map(|(seq, rtt)| serde_json::json!({"seq":seq,"rtt":rtt}))
                .collect();
            let upload = data.upload_complete.as_ref();
            serde_json::json!({
                "type":"downResult","final":complete,"complete":complete,"invalidReasons":data.invalid_reasons,
                "testId":test.test_id,"sessionId":test.session_id,"sessionHash":test.session_hash,"testHash":test.test_hash,
                "planned":test.planned,"scheduled":data.scheduled,"skipped":data.skipped,
                "actualSent":data.actual_sent,"sent":data.actual_sent,
                "sentSeqs":data.sent_seqs.iter().copied().collect::<Vec<_>>(),
                "received":data.acks.len(),"acked":data.acks.len(),
                "uploadReceived":data.upload_received.len(),
                "uploadReceivedSeqs":data.upload_received.iter().copied().collect::<Vec<_>>(),
                "clientUploadScheduled":upload.map(|v| &v["scheduled"]),
                "clientUploadSkipped":upload.map(|v| &v["skipped"]),
                "clientUploadActualSent":upload.map(|v| &v["actualSent"]),
                "samples":samples,"pacing":pacing(&data.sent_times,test.interval_ms),
                "serverEventLoopDelay":summarize_executor_delay(&delays),
                "uploadCompleteAtMs":data.upload_complete_at_ms,"finalizedAtMs":ms(self.epoch)
            })
        };
        lock(&self.tests).remove(&(test.session_hash, test.test_hash));
        test.token.cancel();
        let serialized = if self.cfg.rtc_legacy_control {
            serialize_legacy_down_result(&result, self.cfg.control_reassembled_bytes as usize)
        } else {
            serialize_down_result(
                &result,
                self.cfg.control_single_bytes as usize,
                self.cfg.control_fragment_bytes as usize,
                self.cfg.control_fragment_count as usize,
            )
        };
        let control = self.control.lock().await.clone();
        match (control, serialized) {
            (Some(channel), Ok(messages)) => {
                for message in messages {
                    if self.token.is_cancelled() {
                        break;
                    }
                    if let Err(error) = channel.send_text(message).await {
                        warn!(%error, "downResult send failed");
                        break;
                    }
                }
            }
            (_, Err(error)) => warn!(?error, "downResult fragmentation failed"),
            _ => {}
        }
    }
}

#[derive(Default)]
struct Inner {
    peers: HashMap<Key, Arc<Attempt>>,
    closed: bool,
    tombstones: HashMap<Key, Instant>,
    tombstone_order: VecDeque<Key>,
    offer_rates: RateTable,
    cancel_rates: RateTable,
}

#[derive(Clone)]
pub struct RtcService {
    inner: Arc<Mutex<Inner>>,
    cfg: Config,
    epoch: Instant,
}

#[derive(Debug)]
pub struct Failure {
    pub status: StatusCode,
    pub message: &'static str,
}
impl Failure {
    const fn new(status: StatusCode, message: &'static str) -> Self {
        Self { status, message }
    }
}

impl Inner {
    fn expire_tombstones(&mut self) {
        while let Some(first) = self.tombstone_order.front() {
            if self
                .tombstones
                .get(first)
                .is_some_and(|at| at.elapsed() < Duration::from_secs(30))
            {
                break;
            }
            if let Some(key) = self.tombstone_order.pop_front() {
                self.tombstones.remove(&key);
            }
        }
    }

    fn remember_tombstone(&mut self, key: Key, max: usize) {
        self.expire_tombstones();
        if self.tombstones.contains_key(&key) {
            return;
        }
        while self.tombstones.len() >= max {
            if let Some(oldest) = self.tombstone_order.pop_front() {
                self.tombstones.remove(&oldest);
            } else {
                break;
            }
        }
        self.tombstones.insert(key.clone(), Instant::now());
        self.tombstone_order.push_back(key);
    }
}

impl RtcService {
    pub fn new(cfg: Config) -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner::default())),
            cfg,
            epoch: Instant::now(),
        }
    }

    pub async fn shutdown(&self) {
        let keys = {
            let mut state = lock(&self.inner);
            state.closed = true;
            state.peers.keys().cloned().collect::<Vec<_>>()
        };
        join_all(keys.iter().map(|key| self.cleanup(key))).await;
        info!("WebRTC service stopped");
    }

    pub async fn cancel(&self, ip: &str, id: &str) -> Result<(), Failure> {
        let key = Key {
            ip: ip.to_owned(),
            attempt: id.to_owned(),
        };
        let active = {
            let mut state = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.expire_tombstones();
            if let Some(entry) = state.peers.get(&key) {
                Some(Arc::clone(entry))
            } else if state.tombstones.contains_key(&key) {
                None
            } else {
                if !state.cancel_rates.check(
                    ip,
                    self.cfg.rtc_cancel_max_per_ip,
                    self.cfg.rate_map_max,
                    RateKind::Cancel,
                ) {
                    return Err(Failure::new(
                        StatusCode::TOO_MANY_REQUESTS,
                        "too many WebRTC cancels",
                    ));
                }
                state.remember_tombstone(key.clone(), self.cfg.rtc_tombstones_max);
                None
            }
        };
        if let Some(entry) = active {
            entry.token.cancel();
            self.cleanup(&key).await;
        }
        Ok(())
    }

    async fn cleanup(&self, key: &Key) {
        let removed = {
            let mut state = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let entry = state.peers.remove(key);
            if entry.is_some() {
                state.remember_tombstone(key.clone(), self.cfg.rtc_tombstones_max);
            }
            entry
        };
        if let Some(entry) = removed {
            entry.token.cancel();
            // Release the sole strong session owner; channel callbacks only
            // hold Weak references, so closed channels cannot retain a peer.
            entry.session.lock().await.take();
            let pc = entry.connection.lock().await.take();
            if let Some(pc) = pc {
                match tokio::time::timeout(Duration::from_secs(3), pc.close()).await {
                    Ok(Ok(())) => {}
                    Ok(Err(error)) => warn!(%error, "WebRTC peer close failed"),
                    Err(_) => warn!("WebRTC peer close timed out"),
                }
            }
            info!("WebRTC peer cleaned up");
        }
    }

    pub async fn offer(
        &self,
        ip: &str,
        id: &str,
        sdp: String,
        probe_only: bool,
    ) -> Result<serde_json::Value, Failure> {
        let key = Key {
            ip: ip.to_owned(),
            attempt: id.to_owned(),
        };
        let entry = Arc::new(Attempt {
            token: CancellationToken::new(),
            connection: tokio::sync::Mutex::new(None),
            session: tokio::sync::Mutex::new(None),
        });
        {
            let mut state = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if state.closed {
                return Err(Failure::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "WebRTC service stopping",
                ));
            }
            if !state.offer_rates.check(
                ip,
                self.cfg.rtc_offer_max_per_ip,
                self.cfg.rtc_offer_rate_max,
                RateKind::Standard,
            ) {
                return Err(Failure::new(
                    StatusCode::TOO_MANY_REQUESTS,
                    "too many WebRTC offers",
                ));
            }
            state.expire_tombstones();
            if state.tombstones.contains_key(&key) {
                return Err(Failure::new(StatusCode::CONFLICT, "attempt cancelled"));
            }
            if state.peers.contains_key(&key) {
                return Err(Failure::new(StatusCode::CONFLICT, "attempt already active"));
            }
            if state.peers.len() >= self.cfg.max_rtc_peers {
                return Err(Failure::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "too many active WebRTC peers",
                ));
            }
            if state.peers.keys().filter(|item| item.ip == ip).count()
                >= self.cfg.max_rtc_peers_per_ip
            {
                return Err(Failure::new(
                    StatusCode::TOO_MANY_REQUESTS,
                    "too many WebRTC peers from this IP",
                ));
            }
            state.peers.insert(key.clone(), Arc::clone(&entry));
        }
        // At most MAX_RTC_PEERS timers exist. Each finishes when the peer is
        // cancelled, or removes the peer at its session deadline.
        let service = self.clone();
        let timer_key = key.clone();
        let token = entry.token.clone();
        tokio::spawn(async move {
            tokio::select! {
                () = tokio::time::sleep(Duration::from_millis(service.cfg.rtc_session_ms)) => service.cleanup(&timer_key).await,
                () = token.cancelled() => {},
            }
        });
        let result = tokio::time::timeout(
            Duration::from_millis(self.cfg.rtc_negotiation_ms),
            self.negotiate(&key, &entry, sdp, probe_only),
        )
        .await;
        match result {
            Ok(Ok(answer)) if !entry.token.is_cancelled() => Ok(answer),
            Ok(Err(error)) => {
                self.cleanup(&key).await;
                Err(error)
            }
            _ => {
                self.cleanup(&key).await;
                Err(Failure::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "WebRTC offer failed",
                ))
            }
        }
    }

    async fn negotiate(
        &self,
        key: &Key,
        entry: &Arc<Attempt>,
        sdp: String,
        probe_only: bool,
    ) -> Result<serde_json::Value, Failure> {
        let mut settings = SettingEngine::default();
        if self.cfg.rtc_local_test {
            settings.set_include_loopback_candidate(true);
        }
        if let Some(address) = self.cfg.rtc_udp4 {
            settings.set_network_types(vec![NetworkType::Udp4]);
            settings.set_nat_1to1_ips(vec![address.to_string()], RTCIceCandidateType::Host);
            settings.set_interface_filter(Box::new(|name| {
                let lower = name.to_ascii_lowercase();
                !["tailscale", "docker", "br-", "tun", "lo"]
                    .iter()
                    .any(|prefix| lower.starts_with(prefix))
            }));
        }
        // The probe-only node has a separate NAT mapping. Do not silently advertise
        // the wrong public address when its binding has not been configured.
        if probe_only && self.cfg.rtc_udp4.is_none() {
            return Err(Failure::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "WebRTC offer failed",
            ));
        }
        let api = APIBuilder::new().with_setting_engine(settings).build();
        let pc = Arc::new(
            api.new_peer_connection(RTCConfiguration::default())
                .await
                .map_err(|error| {
                    warn!(%error, "WebRTC peer creation failed");
                    Failure::new(StatusCode::INTERNAL_SERVER_ERROR, "WebRTC offer failed")
                })?,
        );
        {
            let mut connection = entry.connection.lock().await;
            if entry.token.is_cancelled() {
                drop(connection);
                let _ = pc.close().await;
                return Err(Failure::new(StatusCode::CONFLICT, "attempt cancelled"));
            }
            // Cleanup takes the same lock after cancelling the token, so a
            // cancel during negotiation cannot orphan the newly created PC.
            *connection = Some(Arc::clone(&pc));
        }
        if entry.token.is_cancelled() {
            return Err(Failure::new(StatusCode::CONFLICT, "attempt cancelled"));
        }
        let session = Session::new(self.clone(), key.clone(), entry.token.clone());
        *entry.session.lock().await = Some(Arc::clone(&session));
        let weak_session = Arc::downgrade(&session);
        pc.on_data_channel(Box::new(move |channel| {
            let session = weak_session.upgrade();
            Box::pin(async move {
                if let Some(session) = session {
                    session.install(channel).await;
                }
            })
        }));
        let service = self.clone();
        let close_key = key.clone();
        pc.on_peer_connection_state_change(Box::new(move |state| {
            let service = service.clone();
            let key = close_key.clone();
            Box::pin(async move {
                debug!(?state, "WebRTC connection state changed");
                if matches!(
                    state,
                    RTCPeerConnectionState::Disconnected
                        | RTCPeerConnectionState::Failed
                        | RTCPeerConnectionState::Closed
                ) {
                    service.cleanup(&key).await;
                }
            })
        }));
        let offer = RTCSessionDescription::offer(sdp)
            .map_err(|_| Failure::new(StatusCode::INTERNAL_SERVER_ERROR, "WebRTC offer failed"))?;
        pc.set_remote_description(offer).await.map_err(|error| {
            warn!(%error, "WebRTC remote description failed");
            Failure::new(StatusCode::INTERNAL_SERVER_ERROR, "WebRTC offer failed")
        })?;
        if entry.token.is_cancelled() {
            return Err(Failure::new(StatusCode::CONFLICT, "attempt cancelled"));
        }
        let answer = pc.create_answer(None).await.map_err(|error| {
            warn!(%error, "WebRTC answer creation failed");
            Failure::new(StatusCode::INTERNAL_SERVER_ERROR, "WebRTC offer failed")
        })?;
        let mut gathering = pc.gathering_complete_promise().await;
        pc.set_local_description(answer).await.map_err(|error| {
            warn!(%error, "WebRTC local description failed");
            Failure::new(StatusCode::INTERNAL_SERVER_ERROR, "WebRTC offer failed")
        })?;
        let _ = tokio::time::timeout(
            Duration::from_millis(self.cfg.rtc_ice_gather_ms),
            gathering.recv(),
        )
        .await;
        if entry.token.is_cancelled() {
            return Err(Failure::new(StatusCode::CONFLICT, "attempt cancelled"));
        }
        let local = pc.local_description().await.ok_or(Failure::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "WebRTC offer failed",
        ))?;
        Ok(serde_json::json!({"type":local.sdp_type.to_string(),"sdp":local.sdp}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn shutdown_rejects_new_offers() {
        let service = RtcService::new(Config::from_env().unwrap());
        let key = Key {
            ip: "127.0.0.1".into(),
            attempt: "active".into(),
        };
        let entry = Arc::new(Attempt {
            token: CancellationToken::new(),
            connection: tokio::sync::Mutex::new(None),
            session: tokio::sync::Mutex::new(None),
        });
        lock(&service.inner).peers.insert(key, Arc::clone(&entry));
        service.shutdown().await;
        assert!(entry.token.is_cancelled());
        let rejected = service
            .offer("127.0.0.1", "attempt", String::new(), false)
            .await;
        assert!(matches!(
            rejected,
            Err(Failure {
                status: StatusCode::SERVICE_UNAVAILABLE,
                ..
            })
        ));
        assert!(lock(&service.inner).peers.is_empty());
    }

    #[tokio::test]
    async fn cancelled_attempts_evict_oldest_tombstone_at_configured_limit() {
        let mut cfg = Config::from_env().unwrap();
        cfg.rtc_tombstones_max = 128;
        let service = RtcService::new(cfg);
        for index in 0..160 {
            let ip = if index < 80 { "192.0.2.1" } else { "192.0.2.2" };
            service
                .cancel(ip, &format!("attempt-{index}"))
                .await
                .unwrap();
        }
        let state = lock(&service.inner);
        assert_eq!(state.tombstones.len(), 128);
        assert_eq!(state.tombstone_order.len(), 128);
        assert!(!state.tombstones.contains_key(&Key {
            ip: "192.0.2.1".into(),
            attempt: "attempt-0".into(),
        }));
        assert!(state.tombstones.contains_key(&Key {
            ip: "192.0.2.2".into(),
            attempt: "attempt-159".into(),
        }));
    }

    #[test]
    fn tombstone_expiry_and_cancel_window_are_bounded() {
        let mut state = Inner::default();
        let stale = Key {
            ip: "192.0.2.1".into(),
            attempt: "old".into(),
        };
        state.remember_tombstone(stale.clone(), 128);
        state
            .tombstones
            .insert(stale.clone(), Instant::now() - Duration::from_secs(31));
        let fresh = Key {
            ip: "192.0.2.1".into(),
            attempt: "new".into(),
        };
        state.remember_tombstone(fresh.clone(), 128);
        assert!(!state.tombstones.contains_key(&stale));
        assert_eq!(state.tombstone_order.front(), Some(&fresh));

        for _ in 0..80 {
            assert!(
                state
                    .cancel_rates
                    .check("192.0.2.1", 80, 100, RateKind::Cancel)
            );
        }
        assert!(
            !state
                .cancel_rates
                .check("192.0.2.1", 80, 100, RateKind::Cancel)
        );
    }
}
