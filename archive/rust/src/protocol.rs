//! Fixed 128-byte DataChannel probe frame, matching server.js little-endian layout.
pub const FRAME_BYTES: usize = 128;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Frame {
    pub kind: u8,
    pub flags: u8,
    pub session_hash: u32,
    pub test_hash: u32,
    pub seq: u32,
    pub sent_at: f64,
    pub actual_sent: u32,
}

impl Frame {
    #[must_use]
    pub fn decode(input: &[u8]) -> Option<Self> {
        if input.len() != FRAME_BYTES {
            return None;
        }
        Some(Self {
            kind: input[0],
            flags: input[1],
            session_hash: u32::from_le_bytes(input[4..8].try_into().ok()?),
            test_hash: u32::from_le_bytes(input[8..12].try_into().ok()?),
            seq: u32::from_le_bytes(input[12..16].try_into().ok()?),
            sent_at: f64::from_le_bytes(input[16..24].try_into().ok()?),
            actual_sent: u32::from_le_bytes(input[24..28].try_into().ok()?),
        })
    }

    #[must_use]
    pub fn encode(self) -> [u8; FRAME_BYTES] {
        let mut buf = [0; FRAME_BYTES];
        buf[0] = self.kind;
        buf[1] = self.flags;
        buf[4..8].copy_from_slice(&self.session_hash.to_le_bytes());
        buf[8..12].copy_from_slice(&self.test_hash.to_le_bytes());
        buf[12..16].copy_from_slice(&self.seq.to_le_bytes());
        buf[16..24].copy_from_slice(&self.sent_at.to_le_bytes());
        buf[24..28].copy_from_slice(&self.actual_sent.to_le_bytes());
        buf
    }
}

#[must_use]
pub fn hash_string32(input: &str) -> u32 {
    // JavaScript charCodeAt iterates UTF-16 code units, not UTF-8 bytes.
    input.encode_utf16().fold(0x811c_9dc5_u32, |hash, unit| {
        (hash ^ u32::from(unit)).wrapping_mul(0x0100_0193)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_round_trip_and_bounds() {
        let frame = Frame {
            kind: 3,
            flags: 1,
            session_hash: 0x11223344,
            test_hash: 5,
            seq: 42,
            sent_at: 123.5,
            actual_sent: 11,
        };
        let bytes = frame.encode();
        assert_eq!(&bytes[4..8], &[0x44, 0x33, 0x22, 0x11]);
        assert_eq!(Frame::decode(&bytes), Some(frame));
        assert!(Frame::decode(&bytes[..127]).is_none());
        assert!(Frame::decode(&[0; 129]).is_none());
    }

    #[test]
    fn utf16_hash_matches_node_semantics() {
        assert_eq!(hash_string32(""), 0x811c9dc5);
        assert_eq!(hash_string32("a"), 0xe40c292c);
    }
}
