//! Bounded JSON control message fragmentation (shared contract version 1).
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::collections::HashMap;

const ID_FIELDS: [&str; 4] = ["sessionId", "sessionHash", "testId", "testHash"];

#[derive(Debug, PartialEq, Eq)]
pub enum FragmentError {
    Metadata,
    Limit,
    Encoding,
    Identity,
    Payload,
}

struct Assembly {
    count: usize,
    identity: [Value; 4],
    parts: Vec<Option<Vec<u8>>>,
    received: usize,
    total_bytes: usize,
}

pub struct Reassembler {
    assemblies: HashMap<String, Assembly>,
    max_fragments: usize,
    max_bytes: usize,
}

impl Reassembler {
    #[must_use]
    pub fn new(max_fragments: usize, max_bytes: usize) -> Self {
        Self {
            assemblies: HashMap::new(),
            max_fragments,
            max_bytes,
        }
    }

    /// Accept a client `uploadComplete` fragment. Duplicate pieces do not
    /// increase accounting; a completed assembly returns the parsed message.
    ///
    /// # Errors
    /// Rejects malformed metadata/encoding, inconsistent identity and over-limit data.
    pub fn accept(&mut self, fragment: &Value) -> Result<Option<Value>, FragmentError> {
        let id = fragment["fragmentId"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or(FragmentError::Metadata)?;
        let count = fragment["count"]
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or(FragmentError::Metadata)?;
        let index = fragment["index"]
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or(FragmentError::Metadata)?;
        if fragment["type"] != "controlFragment"
            || fragment["messageType"] != "uploadComplete"
            || fragment["encoding"] != "base64"
            || fragment["data"].as_str().is_none()
            || count == 0
            || count > self.max_fragments
            || index >= count
        {
            return Err(FragmentError::Metadata);
        }
        let data = STANDARD
            .decode(fragment["data"].as_str().unwrap_or(""))
            .map_err(|_| FragmentError::Encoding)?;
        let identity = ID_FIELDS.map(|name| fragment[name].clone());
        if !self.assemblies.contains_key(id) {
            if self.assemblies.len() >= 4 {
                return Err(FragmentError::Limit);
            }
            self.assemblies.insert(
                id.to_owned(),
                Assembly {
                    count,
                    identity: identity.clone(),
                    parts: vec![None; count],
                    received: 0,
                    total_bytes: 0,
                },
            );
        }
        let assembly = self.assemblies.get_mut(id).ok_or(FragmentError::Payload)?;
        if assembly.count != count || assembly.identity != identity {
            return Err(FragmentError::Identity);
        }
        if assembly.parts[index].is_some() {
            return Ok(None);
        }
        assembly.total_bytes = assembly
            .total_bytes
            .checked_add(data.len())
            .ok_or(FragmentError::Limit)?;
        if assembly.total_bytes > self.max_bytes {
            self.assemblies.remove(id);
            return Err(FragmentError::Limit);
        }
        assembly.parts[index] = Some(data);
        assembly.received += 1;
        if assembly.received != count {
            return Ok(None);
        }
        let complete = self.assemblies.remove(id).ok_or(FragmentError::Payload)?;
        let mut payload = Vec::with_capacity(complete.total_bytes);
        for part in complete.parts {
            payload.extend_from_slice(&part.ok_or(FragmentError::Payload)?);
        }
        let result: Value = serde_json::from_slice(&payload).map_err(|_| FragmentError::Payload)?;
        if result["type"] != "uploadComplete"
            || ID_FIELDS
                .iter()
                .enumerate()
                .any(|(i, name)| result[name] != complete.identity[i])
        {
            return Err(FragmentError::Identity);
        }
        Ok(Some(result))
    }
}

/// Serialize a `downResult` as one JSON message or bounded base64 fragments.
///
/// # Errors
/// Fails if the result exceeds the configured fragment count or entropy fails.
pub fn serialize_down_result(
    value: &Value,
    single: usize,
    chunk: usize,
    max_fragments: usize,
) -> Result<Vec<String>, FragmentError> {
    let payload = serde_json::to_vec(value).map_err(|_| FragmentError::Payload)?;
    if payload.len() <= single {
        return String::from_utf8(payload)
            .map(|text| vec![text])
            .map_err(|_| FragmentError::Payload);
    }
    if chunk == 0 || payload.len().div_ceil(chunk) > max_fragments {
        return Err(FragmentError::Limit);
    }
    let mut random = [0_u8; 8];
    getrandom::fill(&mut random).map_err(|_| FragmentError::Payload)?;
    let id: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
    let count = payload.len().div_ceil(chunk);
    let mut result = Vec::with_capacity(count);
    for (index, part) in payload.chunks(chunk).enumerate() {
        result.push(
            json!({"type":"controlFragment","messageType":"downResult", "fragmentId":id,
            "index":index,"count":count,"encoding":"base64",
            "testId":value["testId"],"sessionId":value["sessionId"],
            "sessionHash":value["sessionHash"],"testHash":value["testHash"],
            "data":STANDARD.encode(part)})
            .to_string(),
        );
    }
    Ok(result)
}

/// Serialize one bounded JSON result for pre-fragmentation version-1 clients.
/// This is opt-in transport compatibility; no result fields or statistics change.
///
/// # Errors
/// Rejects an oversized result rather than truncating or allocating an unbounded queue.
pub fn serialize_legacy_down_result(
    value: &Value,
    max_bytes: usize,
) -> Result<Vec<String>, FragmentError> {
    let payload = serde_json::to_string(value).map_err(|_| FragmentError::Payload)?;
    if payload.len() > max_bytes {
        return Err(FragmentError::Limit);
    }
    Ok(vec![payload])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_result_is_one_exact_json_message_and_bounded() {
        let value = json!({"type":"downResult","sessionId":"s","testId":"t",
            "complete":true,"samples":(0..1000).collect::<Vec<_>>()});
        let payload = serde_json::to_string(&value).unwrap();
        assert!(payload.len() > 900);
        assert_eq!(
            serialize_legacy_down_result(&value, payload.len()),
            Ok(vec![payload.clone()])
        );
        assert_eq!(
            serialize_legacy_down_result(&value, payload.len() - 1),
            Err(FragmentError::Limit)
        );
        assert_eq!(serde_json::from_str::<Value>(&payload).unwrap(), value);
        assert!(serialize_down_result(&value, 900, 512, 2048).unwrap().len() > 1);
    }

    #[test]
    fn fragmented_upload_completion_reassembles_and_rejects_duplicates() {
        let original = json!({"type":"uploadComplete","testId":"t","sessionId":"s",
            "sessionHash":1,"testHash":2,"planned":500,"sentSeqs":(0..500).collect::<Vec<_>>()});
        let bytes = serde_json::to_vec(&original).unwrap();
        let count = bytes.len().div_ceil(512);
        let mut receiver = Reassembler::new(2048, 2 << 20);
        for (index, piece) in bytes.chunks(512).enumerate() {
            let fragment = json!({"type":"controlFragment","messageType":"uploadComplete",
                "fragmentId":"abc","index":index,"count":count,"encoding":"base64",
                "testId":"t","sessionId":"s","sessionHash":1,"testHash":2,"data":STANDARD.encode(piece)});
            let output = receiver.accept(&fragment).unwrap();
            if index + 1 == count {
                assert_eq!(output, Some(original.clone()));
            } else {
                assert_eq!(output, None);
            }
        }
        let outbound = serialize_down_result(
            &json!({"type":"downResult","testId":"t","sessionId":"s",
            "sessionHash":1,"testHash":2,"samples":(0..500).collect::<Vec<_>>()}),
            900,
            512,
            2048,
        )
        .unwrap();
        assert!(outbound.len() > 1);
        assert!(
            outbound
                .iter()
                .all(|fragment| serde_json::from_str::<Value>(fragment).is_ok())
        );
    }

    #[test]
    fn malformed_or_large_fragments_are_bounded() {
        let mut receiver = Reassembler::new(8, 16);
        assert_eq!(receiver.accept(&json!({})), Err(FragmentError::Metadata));
        let sample = json!({"type":"controlFragment","messageType":"uploadComplete", "fragmentId":"x",
            "index":0,"count":1,"encoding":"base64","data":STANDARD.encode([0u8; 20])});
        assert_eq!(receiver.accept(&sample), Err(FragmentError::Limit));
    }
}
