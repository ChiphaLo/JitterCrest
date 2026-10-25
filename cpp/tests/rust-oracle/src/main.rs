// Test-only oracle: compiles original Rust pure modules without modifying them.
// The C++ service never links or invokes this program.
#[path = "../../../../src/protocol.rs"]
mod protocol;
#[path = "../../../../src/rtc_control.rs"]
mod rtc_control;
#[path = "../../../../src/rtc_sdp.rs"]
mod rtc_sdp;
#[path = "../../../../src/stats.rs"]
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
fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        let value: Value = serde_json::from_str(&line).unwrap();
        println!("{}", execute(&value));
    }
}
