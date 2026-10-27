//! Diagnostic test adapter: JSON array of SDP strings on stdin -> sanitize results.
//! Not installed or used by the production service.
use speedtest_rust::rtc_sdp::{SdpError, sanitize};
use std::io::{self, Read};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let cases: Vec<String> = serde_json::from_str(&input)?;
    let results: Vec<_> = cases
        .iter()
        .map(|value| match sanitize(value) {
            Ok(result) => serde_json::json!({
                "sdp":result.sdp,
                "receivedCandidates":result.received_candidates,
                "ignoredMdnsCandidates":result.ignored_mdns_candidates,
                "ignoredUnsafeCandidates":result.ignored_unsafe_candidates,
                "deduplicatedCandidates":result.deduplicated_candidates,
                "remoteCandidatesAfterFilter":result.remote_candidates_after_filter,
            }),
            Err(SdpError::Limit) => serde_json::json!({"error":"ERR_RTC_SDP_LIMIT"}),
            Err(SdpError::Candidate) => serde_json::json!({"error":"ERR_RTC_SDP_CANDIDATE"}),
        })
        .collect();
    println!("{}", serde_json::to_string(&results)?);
    Ok(())
}
