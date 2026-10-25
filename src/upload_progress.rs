//! Bounded, optional upload-display telemetry. Final HTTP accounting stays in web.rs.
//! The registry lock is used only for lookup/allocation; chunk receipt uses atomics.
use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

pub enum Mode {
    Stream(u8),
    Chunks(u8),
}

#[derive(Clone, Default)]
pub struct Registry(Arc<Mutex<HashMap<String, Arc<Progress>>>>);

pub struct Progress {
    owner: String,
    born: Instant,
    ttl: Duration,
    lanes: u8,
    reusable: bool,
    active: AtomicU8,
    closing: AtomicBool,
    warmup_us: u64,
    started: AtomicU8,
    finished: AtomicU8,
    failed: AtomicBool,
    first_us: AtomicU64,
    measured_us: AtomicU64,
    end_us: AtomicU64,
    bytes: AtomicU64,
    measured: AtomicU64,
}

pub struct Receipt {
    progress: Arc<Progress>,
    bit: u8,
    success: bool,
}

impl Registry {
    pub fn create(
        &self,
        owner: &str,
        mode: Mode,
        warmup_ms: u64,
        ttl: Duration,
        maximum: usize,
        per_ip: usize,
    ) -> Result<String, &'static str> {
        let (lanes, reusable) = match mode {
            Mode::Stream(n) => (n, false),
            Mode::Chunks(n) => (n, true),
        };
        if ![1, 2, 4, 8].contains(&lanes) {
            return Err("invalid upload progress lane count");
        }
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).map_err(|_| "progress entropy unavailable")?;
        let id: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
        let mut records = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        records.retain(|_, value| !value.expired());
        if records.len() >= maximum
            || records.values().filter(|p| p.owner == owner).count() >= per_ip
        {
            return Err("upload progress capacity exceeded");
        }
        if records.contains_key(&id) {
            return Err("upload progress identifier collision");
        }
        records.insert(
            id.clone(),
            Arc::new(Progress {
                owner: owner.to_owned(),
                born: Instant::now(),
                ttl,
                lanes,
                reusable,
                active: AtomicU8::new(0),
                closing: AtomicBool::new(false),
                warmup_us: warmup_ms.saturating_mul(1000),
                started: AtomicU8::new(0),
                finished: AtomicU8::new(0),
                failed: AtomicBool::new(false),
                first_us: AtomicU64::new(0),
                measured_us: AtomicU64::new(0),
                end_us: AtomicU64::new(0),
                bytes: AtomicU64::new(0),
                measured: AtomicU64::new(0),
            }),
        );
        Ok(id)
    }

    pub fn get(&self, owner: &str, id: &str) -> Option<Arc<Progress>> {
        if id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let records = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        records
            .get(id)
            .filter(|p| p.owner == owner && !p.expired())
            .cloned()
    }

    pub fn finish(&self, owner: &str, id: &str) -> Result<(), &'static str> {
        let progress = self.get(owner, id).ok_or("upload progress not found")?;
        if progress.reusable && progress.done() {
            return Ok(());
        }
        if !progress.reusable || progress.closing.swap(true, Ordering::SeqCst) {
            return Err("upload progress cannot close");
        }
        let mask = ((1_u16 << progress.lanes) - 1) as u8;
        if progress.active.load(Ordering::SeqCst) != 0
            || progress.started.load(Ordering::Relaxed) != mask
        {
            progress.closing.store(false, Ordering::SeqCst);
            return Err("upload progress lanes are not finished");
        }
        progress
            .end_us
            .fetch_max(progress.micros(), Ordering::Relaxed);
        progress.finished.store(mask, Ordering::Release);
        Ok(())
    }

    pub fn attach(&self, owner: &str, id: &str, lane: u8) -> Result<Receipt, &'static str> {
        let progress = self.get(owner, id).ok_or("upload progress not found")?;
        if lane >= progress.lanes {
            return Err("invalid upload progress lane");
        }
        let bit = 1_u8 << lane;
        if progress.reusable {
            if progress.closing.load(Ordering::SeqCst) || progress.done() {
                return Err("upload progress is closed");
            }
            if progress.active.fetch_or(bit, Ordering::SeqCst) & bit != 0 {
                return Err("upload progress lane is active");
            }
            if progress.closing.load(Ordering::SeqCst) {
                progress.active.fetch_and(!bit, Ordering::SeqCst);
                return Err("upload progress is closing");
            }
            progress.started.fetch_or(bit, Ordering::Relaxed);
        } else if progress.started.fetch_or(bit, Ordering::AcqRel) & bit != 0 {
            return Err("upload progress lane already attached");
        }
        Ok(Receipt {
            progress,
            bit,
            success: false,
        })
    }
}

impl Progress {
    fn micros(&self) -> u64 {
        // Configured lifetimes are seconds, well below this saturating conversion.
        u64::try_from(self.born.elapsed().as_micros())
            .unwrap_or(u64::MAX)
            .saturating_add(1)
    }

    pub fn done(&self) -> bool {
        u16::from(self.finished.load(Ordering::Acquire)) == (1_u16 << self.lanes) - 1
    }

    pub fn expired(&self) -> bool {
        self.born.elapsed() >= self.ttl
            || (self.done()
                && self
                    .micros()
                    .saturating_sub(self.end_us.load(Ordering::Relaxed))
                    > 5_000_000)
    }

    pub fn snapshot(&self) -> serde_json::Value {
        let done = self.done();
        let now = if done {
            self.end_us.load(Ordering::Relaxed)
        } else {
            self.micros()
        };
        let first = self.first_us.load(Ordering::Relaxed);
        let measured = self.measured_us.load(Ordering::Acquire);
        // Live snapshots are approximate between concurrent atomic updates. Only
        // the existing final upload response is authoritative for final results.
        let measured_bytes = self.measured.load(Ordering::Relaxed);
        let bytes = self.bytes.load(Ordering::Relaxed);
        serde_json::json!({
            "bytes": bytes,
            "measuredBytes": measured_bytes.min(bytes),
            "elapsedMs": if first == 0 { 0. } else { now.saturating_sub(first) as f64 / 1000. },
            "measuredElapsedMs": if measured == 0 { 0. } else { now.saturating_sub(measured) as f64 / 1000. },
            "done": done,
            "failed": self.failed.load(Ordering::Relaxed),
            "expired": self.born.elapsed() >= self.ttl,
        })
    }
}

impl Receipt {
    pub fn received(&self, bytes: usize) {
        if bytes == 0 {
            return;
        }
        let p = &self.progress;
        let now = p.micros();
        let first = match p
            .first_us
            .compare_exchange(0, now, Ordering::Relaxed, Ordering::Relaxed)
        {
            Ok(value) | Err(value) => value,
        };
        let first = if first == 0 { now } else { first };
        p.bytes.fetch_add(bytes as u64, Ordering::Relaxed);
        if now.saturating_sub(first) >= p.warmup_us {
            p.measured.fetch_add(bytes as u64, Ordering::Relaxed);
            let _ = p
                .measured_us
                .compare_exchange(0, now, Ordering::Release, Ordering::Relaxed);
        }
    }

    pub fn success(&mut self) {
        self.success = true;
    }
}

impl Drop for Receipt {
    fn drop(&mut self) {
        let p = &self.progress;
        if !self.success {
            p.failed.store(true, Ordering::Relaxed);
        }
        p.end_us.fetch_max(p.micros(), Ordering::Relaxed);
        if p.reusable {
            p.active.fetch_and(!self.bit, Ordering::SeqCst);
        } else {
            p.finished.fetch_or(self.bit, Ordering::Release);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_owned_one_use_lanes_and_final_receipt() {
        let registry = Registry::default();
        let ttl = Duration::from_secs(30);
        let id = registry
            .create("one", Mode::Stream(2), 0, ttl, 2, 1)
            .unwrap();
        assert!(
            registry
                .create("one", Mode::Stream(1), 0, ttl, 2, 1)
                .is_err()
        );
        assert!(registry.get("other", &id).is_none());
        assert!(registry.get("one", &"x".repeat(33)).is_none());
        let mut first = registry.attach("one", &id, 0).unwrap();
        assert!(registry.attach("one", &id, 0).is_err());
        assert!(registry.attach("one", &id, 2).is_err());
        let progress = registry.get("one", &id).unwrap();
        first.received(1000);
        first.success();
        drop(first);
        assert!(!progress.done());
        let mut second = registry.attach("one", &id, 1).unwrap();
        second.received(2000);
        second.success();
        drop(second);
        assert!(progress.done());
        let snapshot = progress.snapshot();
        assert_eq!(snapshot["bytes"], 3000);
        assert_eq!(snapshot["measuredBytes"], 3000);
        assert_eq!(snapshot["failed"], false);
        assert_eq!(snapshot["elapsedMs"], progress.snapshot()["elapsedMs"]);
        registry
            .create("two", Mode::Stream(1), 0, ttl, 2, 1)
            .unwrap();
        assert!(
            registry
                .create("three", Mode::Stream(1), 0, ttl, 2, 1)
                .is_err()
        );
    }

    #[test]
    fn reusable_chunk_lanes_cannot_overlap_or_close_while_active() {
        let registry = Registry::default();
        let id = registry
            .create("one", Mode::Chunks(2), 0, Duration::from_secs(30), 1, 1)
            .unwrap();
        let progress = registry.get("one", &id).unwrap();
        for _ in 0..3 {
            let mut receipt = registry.attach("one", &id, 0).unwrap();
            assert!(registry.attach("one", &id, 0).is_err());
            assert!(registry.finish("one", &id).is_err());
            receipt.received(1000);
            receipt.success();
            drop(receipt);
            assert!(!progress.done());
        }
        assert!(registry.finish("other", &id).is_err());
        assert!(registry.finish("one", &id).is_err());
        let mut second = registry.attach("one", &id, 1).unwrap();
        second.received(2000);
        second.success();
        drop(second);
        registry.finish("one", &id).unwrap();
        registry.finish("one", &id).unwrap();
        assert!(progress.done());
        assert_eq!(progress.snapshot()["bytes"], 5000);
        assert_eq!(progress.snapshot()["failed"], false);
        assert!(registry.attach("one", &id, 0).is_err());
    }

    #[test]
    fn cancellation_is_failed_and_pending_sessions_expire() {
        let registry = Registry::default();
        let id = registry
            .create("one", Mode::Stream(1), 0, Duration::from_secs(30), 1, 1)
            .unwrap();
        let receipt = registry.attach("one", &id, 0).unwrap();
        let progress = registry.get("one", &id).unwrap();
        drop(receipt);
        assert_eq!(progress.snapshot()["failed"], true);
        let expired = Registry::default();
        let id = expired
            .create("one", Mode::Stream(1), 0, Duration::ZERO, 1, 1)
            .unwrap();
        assert!(expired.get("one", &id).is_none());
        assert!(
            expired
                .create("one", Mode::Stream(1), 0, Duration::ZERO, 1, 1)
                .is_ok()
        );
    }
}
