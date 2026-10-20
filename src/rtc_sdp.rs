//! Bounded SDP sanitization before handing a remote offer to the ICE stack.
//! Candidate filtering mirrors the Node reference; unknown non-candidate SDP
//! lines are preserved verbatim for the WebRTC library to validate.
use std::{collections::HashSet, net::IpAddr};

const MAX_BYTES: usize = 64 * 1024;
const MAX_LINES: usize = 1024;
const MAX_LINE_BYTES: usize = 4096;
const MAX_MEDIA: usize = 8;
const MAX_CANDIDATES: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SdpError {
    Limit,
    Candidate,
}

#[derive(Debug, PartialEq, Eq)]
pub struct FilteredSdp {
    pub sdp: String,
    pub received_candidates: usize,
    pub ignored_mdns_candidates: usize,
    pub ignored_unsafe_candidates: usize,
    pub deduplicated_candidates: usize,
    pub remote_candidates_after_filter: usize,
}

fn prefix6(address: u128, base: u128, bits: u32) -> bool {
    (address >> (128 - bits)) == (base >> (128 - bits))
}

#[must_use]
pub fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(address) => {
            let [a, b, c, _] = address.octets();
            !(a == 0
                || a == 10
                || a == 127
                || a >= 224
                || (a == 100 && (64..=127).contains(&b))
                || (a == 169 && b == 254)
                || (a == 172 && (16..=31).contains(&b))
                || (a == 192 && b == 168)
                || (a == 192 && b == 0 && (c == 0 || c == 2))
                || (a == 192 && b == 31 && c == 196)
                || (a == 192 && b == 52 && c == 193)
                || (a == 192 && b == 88 && c == 99)
                || (a == 198 && (b == 18 || b == 19))
                || (a == 198 && b == 51 && c == 100)
                || (a == 192 && b == 175 && c == 48)
                || (a == 203 && b == 0 && c == 113))
        }
        IpAddr::V6(address) => {
            let value = u128::from(address);
            let global = prefix6(value, 0x2000_u128 << 112, 3);
            global
                && ![
                    (0_u128, 96),
                    (0xfc00_u128 << 112, 7),
                    (0xfe80_u128 << 112, 10),
                    (0xff00_u128 << 112, 8),
                    (0x0100_u128 << 112, 64),
                    (0x20010000_u128 << 96, 23),
                    (0x20010db8_u128 << 96, 32),
                    (0x20010002_u128 << 96, 48),
                    (0x20010010_u128 << 96, 28),
                    (0x20010020_u128 << 96, 28),
                    (0x20010030_u128 << 96, 28),
                    (0x2002_u128 << 112, 16),
                    (0x3fff_u128 << 112, 20),
                ]
                .iter()
                .any(|(base, bits)| prefix6(value, *base, *bits))
        }
    }
}

struct Candidate {
    normalized: String,
    key: Option<(IpAddr, u16)>,
    mdns: bool,
    safe: bool,
}

fn candidate(line: &str) -> Result<Candidate, SdpError> {
    let fields: Vec<&str> = line[12..].split_whitespace().collect();
    if fields.len() < 8 || !(fields.len() - 8).is_multiple_of(2) {
        return Err(SdpError::Candidate);
    }
    let component = fields[1].parse::<u32>().map_err(|_| SdpError::Candidate)?;
    let priority = fields[3].parse::<u32>().map_err(|_| SdpError::Candidate)?;
    let port = fields[5].parse::<u16>().map_err(|_| SdpError::Candidate)?;
    let transport = fields[2].to_ascii_lowercase();
    let kind = fields[7].to_ascii_lowercase();
    if fields[0].is_empty()
        || component != 1
        || transport != "udp"
        || !fields[3].bytes().all(|b| b.is_ascii_digit())
        || priority == 0
        || !fields[5].bytes().all(|b| b.is_ascii_digit())
        || port == 0
        || !fields[6].eq_ignore_ascii_case("typ")
        || !matches!(kind.as_str(), "host" | "srflx" | "prflx" | "relay")
    {
        return Err(SdpError::Candidate);
    }
    let mdns = fields[4]
        .trim_end_matches('.')
        .to_ascii_lowercase()
        .ends_with(".local");
    let address = fields[4].parse::<IpAddr>().ok();
    let safe = address.is_some_and(is_public_ip);
    let normalized_address = address.map_or_else(|| fields[4].to_owned(), |ip| ip.to_string());
    let mut normalized = format!(
        "a=candidate:{} {} {} {} {} {} typ {}",
        fields[0], component, transport, priority, normalized_address, port, kind
    );
    for extra in &fields[8..] {
        normalized.push(' ');
        normalized.push_str(extra);
    }
    Ok(Candidate {
        normalized,
        key: address.map(|ip| (ip, port)),
        mdns,
        safe,
    })
}

/// Validate hard limits and filter unsafe/mDNS/duplicate remote ICE candidates.
///
/// # Errors
/// Returns `Limit` for oversized SDP or too many media/candidate lines and
/// `Candidate` for malformed candidate syntax. No remote data is allocated
/// beyond the 64 KiB SDP input bound.
pub fn sanitize(sdp: &str) -> Result<FilteredSdp, SdpError> {
    if sdp.len() > MAX_BYTES {
        return Err(SdpError::Limit);
    }
    let mut result = FilteredSdp {
        sdp: String::with_capacity(sdp.len()),
        received_candidates: 0,
        ignored_mdns_candidates: 0,
        ignored_unsafe_candidates: 0,
        deduplicated_candidates: 0,
        remote_candidates_after_filter: 0,
    };
    let mut keys = HashSet::new();
    let mut media = 0;
    let mut lines = 0;
    let mut rest = sdp;
    loop {
        lines += 1;
        if lines > MAX_LINES {
            return Err(SdpError::Limit);
        }
        let end = rest.find(['\r', '\n']).unwrap_or(rest.len());
        let (line, after) = rest.split_at(end);
        if line.len() > MAX_LINE_BYTES {
            return Err(SdpError::Limit);
        }
        let separator_len = if after.starts_with("\r\n") {
            2
        } else if after.is_empty() {
            0
        } else {
            1
        };
        let separator = &after[..separator_len];
        if line.starts_with("m=") {
            media += 1;
            if media > MAX_MEDIA {
                return Err(SdpError::Limit);
            }
        }
        if line.starts_with("a=candidate:") {
            result.received_candidates += 1;
            if result.received_candidates > MAX_CANDIDATES {
                return Err(SdpError::Limit);
            }
            let parsed = candidate(line)?;
            if parsed.mdns {
                result.ignored_mdns_candidates += 1;
            } else if !parsed.safe {
                result.ignored_unsafe_candidates += 1;
            } else if parsed.key.is_some_and(|key| !keys.insert(key)) {
                result.deduplicated_candidates += 1;
            } else {
                result.sdp.push_str(&parsed.normalized);
                result.sdp.push_str(separator);
            }
        } else {
            result.sdp.push_str(line);
            result.sdp.push_str(separator);
        }
        if separator_len == 0 {
            break;
        }
        rest = &after[separator_len..];
    }
    result.remote_candidates_after_filter = keys.len();
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidates_are_filtered_and_deduplicated() {
        let sdp = "v=0\r\nm=application 9 UDP/DTLS/SCTP webrtc-data\r\na=candidate:1 1 UDP 123 8.8.8.8 9999 typ host\r\na=candidate:2 1 udp 124 8.8.8.8 9999 typ srflx\r\na=candidate:3 1 udp 125 192.168.1.2 9999 typ host\r\na=candidate:4 1 udp 126 peer.local 9999 typ host\r\n";
        let result = sanitize(sdp).unwrap();
        assert_eq!(result.received_candidates, 4);
        assert_eq!(result.deduplicated_candidates, 1);
        assert_eq!(result.ignored_unsafe_candidates, 1);
        assert_eq!(result.ignored_mdns_candidates, 1);
        assert_eq!(result.remote_candidates_after_filter, 1);
        assert!(
            result
                .sdp
                .contains("a=candidate:1 1 udp 123 8.8.8.8 9999 typ host\r\n")
        );
    }

    #[test]
    fn malformed_and_limits_fail_closed() {
        assert_eq!(
            sanitize("a=candidate:1 2 udp 1 8.8.8.8 9999 typ host"),
            Err(SdpError::Candidate)
        );
        assert_eq!(
            sanitize(&"x".repeat(MAX_LINE_BYTES + 1)),
            Err(SdpError::Limit)
        );
        assert_eq!(sanitize(&"\n".repeat(MAX_LINES)), Err(SdpError::Limit));
    }

    #[test]
    fn special_ip_ranges_match_node_filter() {
        for ip in ["100.64.0.1", "192.0.2.1", "2001:db8::1", "::1", "127.0.0.1"] {
            assert!(!is_public_ip(ip.parse().unwrap()));
        }
        for ip in ["8.8.8.8", "1.1.1.1", "2606:4700::1111"] {
            assert!(is_public_ip(ip.parse().unwrap()));
        }
    }
}
