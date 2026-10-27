extern crate self as speedtest_rust;
#[path = "../../src/config.rs"] mod reference_config;
// Test-only oracle: compiles original Rust pure modules without modifying them.
// The C++ service never links or invokes this program.
#[path = "../../src/protocol.rs"]
mod protocol;
#[path = "../../src/rtc_control.rs"]
mod rtc_control;
#[path = "../../src/rtc_sdp.rs"]
mod rtc_sdp;
#[path = "../../src/stats.rs"]
mod stats;
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn execute(v: &Value) -> Value {
    match v["op"].as_str().unwrap() {
        "hash" => json!(protocol::hash_string32(v["text"].as_str().unwrap())),
        "percentile" => {
            let values = v["values"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_f64().unwrap())
                .collect::<Vec<_>>();
            json!(stats::percentile(&values, v["p"].as_f64().unwrap()))
        }
        "density" => json!(stats::required_sample_count(
            v["planned"].as_u64().unwrap() as usize,
            v["density"].as_f64().unwrap()
        )),
        "accounting" => json!(
            stats::Accounting {
                planned: v["planned"].as_u64().unwrap() as usize,
                scheduled: v["scheduled"].as_u64().unwrap() as usize,
                skipped: v["skipped"].as_u64().unwrap() as usize,
                actual_sent: v["actualSent"].as_u64().unwrap() as usize
            }
            .valid()
        ),
        "frame" => {
            let h = v["hex"].as_str().unwrap();
            let bytes = (0..h.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
                .collect::<Vec<_>>();
            match protocol::Frame::decode(&bytes) {
                None => Value::Null,
                Some(f) => {
                    json!({"kind":f.kind,"flags":f.flags,"sessionHash":f.session_hash,"testHash":f.test_hash,"seq":f.seq,"timestampBits":f.sent_at.to_bits(),"actualSent":f.actual_sent,"encoded":f.encode().iter().map(|b|format!("{b:02x}")).collect::<String>()})
                }
            }
        }
        "sdp" => match rtc_sdp::sanitize(v["sdp"].as_str().unwrap()) {
            Ok(f) => {
                json!({"sdp":f.sdp,"received_candidates":f.received_candidates,"ignored_mdns_candidates":f.ignored_mdns_candidates,"ignored_unsafe_candidates":f.ignored_unsafe_candidates,"deduplicated_candidates":f.deduplicated_candidates,"remote_candidates_after_filter":f.remote_candidates_after_filter})
            }
            Err(e) => json!({"error":format!("{e:?}")}),
        },
        "serialize" => {
            let single = v["single"].as_u64().unwrap() as usize;
            let result = if v["legacy"] == true {
                rtc_control::serialize_legacy_down_result(&v["value"], single)
            } else {
                rtc_control::serialize_down_result(
                    &v["value"],
                    single,
                    v["chunk"].as_u64().unwrap() as usize,
                    v["maximum"].as_u64().unwrap() as usize,
                )
            };
            match result {
                Err(e) => json!({"error":format!("{e:?}")}),
                Ok(messages) => json!(
                    messages
                        .into_iter()
                        .map(|s| {
                            let mut value: Value = serde_json::from_str(&s).unwrap();
                            if value["type"] == "controlFragment" {
                                value["fragmentId"] = json!("NONCE");
                            }
                            value
                        })
                        .collect::<Vec<_>>()
                ),
            }
        }
        "fragments" => {
            let mut r = rtc_control::Reassembler::new(
                v["maximum"].as_u64().unwrap_or(2048) as usize,
                v["bytes"].as_u64().unwrap_or(2 << 20) as usize,
            );
            json!(
                v["parts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| match r.accept(f) {
                        Ok(value) => value.unwrap_or(Value::Null),
                        Err(e) => json!({"error":format!("{e:?}")}),
                    })
                    .collect::<Vec<_>>()
            )
        }
        _ => panic!("unknown test op"),
    }
}
fn dump_config() -> anyhow::Result<()> {
 let cfg=reference_config::Config::from_env()?;let _=cfg.bind_addr()?;let _=&cfg.nodes;let mut result=json!({});
 result["max_upload"]=json!(cfg.max_upload);
 result["upload_ms"]=json!(cfg.upload_ms);
 result["rtc_session_ms"]=json!(cfg.rtc_session_ms);
 result["port"]=json!(cfg.port);
 result["max_download"]=json!(cfg.max_download);
 result["max_stream_upload"]=json!(cfg.max_stream_upload);
 result["fixed_download_ms"]=json!(cfg.fixed_download_ms);
 result["stream_download_ms"]=json!(cfg.stream_download_ms);
 result["http_max_requests_per_socket"]=json!(cfg.http_max_requests_per_socket);
 result["http_keep_alive_ms"]=json!(cfg.http_keep_alive_ms);
 result["http_headers_ms"]=json!(cfg.http_headers_ms);
 result["http_request_ms"]=json!(cfg.http_request_ms);
 result["http_max_headers_count"]=json!(cfg.http_max_headers_count);
 result["rate_max"]=json!(cfg.rate_max);
 result["speed_rate_max"]=json!(cfg.speed_rate_max);
 result["rate_map_max"]=json!(cfg.rate_map_max);
 result["max_active_global"]=json!(cfg.max_active_global);
 result["max_active_per_ip"]=json!(cfg.max_active_per_ip);
 result["download_budget"]=json!(cfg.download_budget);
 result["upload_budget"]=json!(cfg.upload_budget);
 result["budget_window_ms"]=json!(cfg.budget_window_ms);
 result["rtc_result_grace_ms"]=json!(cfg.rtc_result_grace_ms);
 result["rtc_result_samples_max"]=json!(cfg.rtc_result_samples_max);
 result["rtc_down_tests_max"]=json!(cfg.rtc_down_tests_max);
 result["rtc_control_message_bytes"]=json!(cfg.rtc_control_message_bytes);
 result["rtc_control_messages_max"]=json!(cfg.rtc_control_messages_max);
 result["rtc_test_messages_max"]=json!(cfg.rtc_test_messages_max);
 result["rtc_inbound_bytes_max"]=json!(cfg.rtc_inbound_bytes_max);
 result["rtc_offer_body_max"]=json!(cfg.rtc_offer_body_max);
 result["control_single_bytes"]=json!(cfg.control_single_bytes);
 result["control_fragment_bytes"]=json!(cfg.control_fragment_bytes);
 result["control_fragment_count"]=json!(cfg.control_fragment_count);
 result["control_reassembled_bytes"]=json!(cfg.control_reassembled_bytes);
 result["max_rtc_peers"]=json!(cfg.max_rtc_peers);
 result["max_rtc_peers_per_ip"]=json!(cfg.max_rtc_peers_per_ip);
 result["rtc_cancel_max_per_ip"]=json!(cfg.rtc_cancel_max_per_ip);
 result["rtc_tombstones_max"]=json!(cfg.rtc_tombstones_max);
 result["rtc_offer_max_per_ip"]=json!(cfg.rtc_offer_max_per_ip);
 result["rtc_offer_rate_max"]=json!(cfg.rtc_offer_rate_max);
 result["rtc_negotiation_ms"]=json!(cfg.rtc_negotiation_ms);
 result["rtc_measurement_ms"]=json!(cfg.rtc_measurement_ms);
 result["rtc_ice_gather_ms"]=json!(cfg.rtc_ice_gather_ms);

 let mut allowed=cfg.allowed_origins.iter().collect::<Vec<_>>();allowed.sort();let mut trusted=cfg.trusted_proxy_ips.iter().collect::<Vec<_>>();trusted.sort();
 result["host"]=json!(cfg.host);result["name"]=json!(cfg.name);result["allowed_origins"]=json!(allowed);result["trusted_proxy_ips"]=json!(trusted);
 result["public_cors"]=json!(cfg.public_cors);result["allow_cross_site"]=json!(cfg.allow_cross_site);result["trust_proxy"]=json!(cfg.trust_proxy);
 result["rtc_experimental"]=json!(cfg.rtc_experimental);result["rtc_legacy_control"]=json!(cfg.rtc_legacy_control);result["rtc_local_test"]=json!(cfg.rtc_local_test);result["rtc_udp4"]=json!(cfg.rtc_udp4.map(|ip|ip.to_string()));
 println!("{}",result);Ok(())
}

fn main() {
 if std::env::args().nth(1).as_deref()==Some("--config") {if let Err(e)=dump_config(){eprintln!("{e}");std::process::exit(1);}return;}
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let value: Value = serde_json::from_str(&line).unwrap();
        println!("{}", execute(&value));
    }
}
