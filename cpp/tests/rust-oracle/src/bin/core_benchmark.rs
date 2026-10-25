// Test-only native-core driver. These remain the unmodified reference modules.
#[path = "../../../../../src/protocol.rs"]
mod protocol;
#[path = "../../../../../src/stats.rs"]
mod stats;
use serde_json::{Value, json};
use std::io::{self, Read};
use std::sync::atomic::{Ordering, compiler_fence};
use std::time::Instant;

fn run(
    operation: &str,
    iterations: u64,
    frames: &[Vec<u8>],
    texts: &[String],
    values: &mut [f64],
) -> u64 {
    let mut checksum = 0_u64;
    match operation {
        "decode" => {
            for i in 0..iterations {
                let value =
                    match protocol::Frame::decode(&frames[(i % frames.len() as u64) as usize]) {
                        Some(frame) => frame
                            .sent_at
                            .to_bits()
                            .wrapping_add(u64::from(frame.session_hash) << 32)
                            .wrapping_add(u64::from(frame.test_hash))
                            .wrapping_add(u64::from(frame.seq))
                            .wrapping_add(u64::from(frame.actual_sent))
                            .wrapping_add(u64::from(frame.kind) << 8)
                            .wrapping_add(u64::from(frame.flags)),
                        None => i,
                    };
                checksum = checksum.rotate_left(7) ^ value;
            }
        }
        "hash" => {
            for i in 0..iterations {
                let value = u64::from(protocol::hash_string32(
                    &texts[(i % texts.len() as u64) as usize],
                ));
                checksum = checksum.rotate_left(7) ^ value;
            }
        }
        "percentile" => {
            for i in 0..iterations {
                values[0] = (i % 1024) as f64 / 64.0;
                let value = stats::percentile(values, 0.95).unwrap().to_bits();
                checksum = checksum.rotate_left(7) ^ value;
            }
        }
        _ => unreachable!(),
    }
    checksum
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text)?;
    let input: Value = serde_json::from_str(&text)?;
    let operation = input["operation"].as_str().ok_or("missing operation")?;
    let iterations = input["iterations"].as_u64().ok_or("missing iterations")?;
    if iterations == 0 || iterations > 1_000_000_000 {
        return Err("iterations outside benchmark bound".into());
    }
    let mut frames = Vec::new();
    let mut texts = Vec::new();
    let mut values = Vec::new();
    match operation {
        "decode" => {
            for value in input["frames"].as_array().ok_or("missing frames")? {
                let hex = value.as_str().ok_or("invalid frame")?;
                if hex.len() > 512 || hex.len() % 2 != 0 {
                    return Err("invalid fixture hex length".into());
                }
                frames.push(
                    (0..hex.len())
                        .step_by(2)
                        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16))
                        .collect::<Result<Vec<_>, _>>()?,
                );
            }
            if frames.is_empty() {
                return Err("empty frame dataset".into());
            }
        }
        "hash" => {
            texts = serde_json::from_value(input["texts"].clone())?;
            if texts.is_empty() {
                return Err("empty hash dataset".into());
            }
        }
        "percentile" => {
            values = serde_json::from_value(input["values"].clone())?;
            if values.is_empty() {
                return Err("empty percentile dataset".into());
            }
        }
        _ => return Err("unknown benchmark operation".into()),
    }
    let warmup_checksum = run(operation, iterations / 10, &frames, &texts, &mut values);
    let start = Instant::now();
    compiler_fence(Ordering::SeqCst);
    let checksum = run(operation, iterations, &frames, &texts, &mut values);
    compiler_fence(Ordering::SeqCst);
    let elapsed_ns = start.elapsed().as_nanos();
    println!(
        "{}",
        json!({"checksum":checksum,"warmup_checksum":warmup_checksum,
                         "iterations":iterations,"elapsed_ns":elapsed_ns})
    );
    Ok(())
}
