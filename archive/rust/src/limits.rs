//! Short synchronous critical sections only. Leases release on response cancellation.
use crate::{
    config::Config,
    rate::{RateKind, RateTable},
};
use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
struct Budget {
    // Node keys these buckets by floor(Date.now() / 1000) * 1000.
    entries: VecDeque<(u64, u64)>,
    total: u64,
}

impl Budget {
    fn prune(&mut self, now_ms: u64, window_ms: u64) {
        let cutoff = now_ms.saturating_sub(window_ms);
        let mut expired = 0_u64;
        self.entries.retain(|(at, bytes)| {
            if *at <= cutoff {
                expired += *bytes;
                false
            } else {
                true
            }
        });
        self.total -= expired;
    }

    fn add(&mut self, now_ms: u64, bytes: u64) -> u64 {
        let bucket_at = now_ms / 1000 * 1000;
        if let Some((_, count)) = self.entries.iter_mut().find(|(at, _)| *at == bucket_at) {
            *count += bytes;
        } else {
            self.entries.push_back((bucket_at, bytes));
        }
        self.total += bytes;
        bucket_at
    }

    fn refund(&mut self, bucket_at: u64, bytes: u64, now_ms: u64, window_ms: u64) {
        if bytes == 0 || bucket_at <= now_ms.saturating_sub(window_ms) {
            return;
        }
        if let Some(index) = self.entries.iter().position(|(at, _)| *at == bucket_at) {
            let amount = bytes.min(self.total).min(self.entries[index].1);
            self.total -= amount;
            self.entries[index].1 -= amount;
            if self.entries[index].1 == 0 {
                self.entries.remove(index);
            }
        }
    }
}

fn wall_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Default)]
struct BudgetTable {
    entries: HashMap<String, Budget>,
    order: VecDeque<String>,
}

impl BudgetTable {
    fn remove(&mut self, ip: &str) {
        self.entries.remove(ip);
        if let Some(index) = self.order.iter().position(|key| key == ip) {
            self.order.remove(index);
        }
    }
}

#[derive(Default)]
struct Counters {
    general: RateTable,
    speed: RateTable,
    download: BudgetTable,
    upload: BudgetTable,
    active: HashMap<String, usize>,
    global: usize,
}

#[derive(Clone, Default)]
pub struct Limits(Arc<Mutex<Counters>>);

pub enum Direction {
    Download,
    Upload,
}

impl Limits {
    pub fn rate(&self, ip: &str, speed: bool, cfg: &Config) -> bool {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let table = if speed {
            &mut state.speed
        } else {
            &mut state.general
        };
        table.check(
            ip,
            if speed {
                cfg.speed_rate_max
            } else {
                cfg.rate_max
            },
            cfg.rate_map_max,
            RateKind::Standard,
        )
    }

    pub fn active(&self, ip: &str, cfg: &Config) -> Result<Lease, &'static str> {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if state.global >= cfg.max_active_global {
            return Err("too many global active connections");
        }
        let value = state.active.entry(ip.to_owned()).or_default();
        if *value >= cfg.max_active_per_ip {
            return Err("too many active connections");
        }
        *value += 1;
        state.global += 1;
        Ok(Lease {
            limits: self.clone(),
            ip: ip.to_owned(),
        })
    }

    pub fn consume(&self, ip: &str, bytes: u64, direction: Direction, cfg: &Config) -> bool {
        self.reserve(ip, bytes, direction, cfg).is_some()
    }

    pub fn reserve_download(&self, ip: &str, bytes: u64, cfg: &Config) -> Option<u64> {
        self.reserve(ip, bytes, Direction::Download, cfg)
    }

    fn reserve(&self, ip: &str, bytes: u64, direction: Direction, cfg: &Config) -> Option<u64> {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let (table, max) = match direction {
            Direction::Download => (&mut state.download, cfg.download_budget),
            Direction::Upload => (&mut state.upload, cfg.upload_budget),
        };
        let now_ms = wall_time_ms();
        if let Some(item) = table.entries.get_mut(ip) {
            item.prune(now_ms, cfg.budget_window_ms);
            if bytes > max.saturating_sub(item.total) {
                if item.total == 0 {
                    table.remove(ip);
                }
                return None;
            }
        } else if bytes > max {
            // A failed reservation must not consume a map slot.
            return None;
        }
        if !table.entries.contains_key(ip) {
            if table.entries.len() >= cfg.rate_map_max
                && let Some(oldest) = table.order.pop_front()
            {
                table.entries.remove(&oldest);
            }
            table.order.push_back(ip.to_owned());
        }
        let item = table.entries.entry(ip.to_owned()).or_default();
        Some(item.add(now_ms, bytes))
    }

    pub fn refund(&self, ip: &str, bucket_at: u64, bytes: u64, window_ms: u64) {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(item) = state.download.entries.get_mut(ip) {
            item.refund(bucket_at, bytes, wall_time_ms(), window_ms);
            if item.total == 0 {
                state.download.remove(ip);
            }
        }
    }
}

pub struct Lease {
    limits: Limits,
    ip: String,
}
impl Drop for Lease {
    fn drop(&mut self) {
        let mut state = self
            .limits
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.global = state.global.saturating_sub(1);
        if let Some(count) = state.active.get_mut(&self.ip) {
            *count -= 1;
            if *count == 0 {
                state.active.remove(&self.ip);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budget_uses_wall_clock_second_buckets_and_exact_expiry() {
        let mut budget = Budget::default();
        let first = budget.add(1_999, 80);
        let second = budget.add(2_000, 20);
        assert_eq!((first, second), (1_000, 2_000));
        budget.refund(first, 30, 60_999, 60_000);
        assert_eq!(budget.total, 70);
        budget.refund(first, 50, 61_000, 60_000);
        assert_eq!(
            budget.total, 70,
            "refund is forbidden at the expiry boundary"
        );
        budget.prune(61_000, 60_000);
        assert_eq!(budget.entries, VecDeque::from([(2_000, 20)]));
        assert_eq!(budget.total, 20);
        budget.refund(second, 20, 61_999, 60_000);
        assert_eq!(budget.total, 0);
    }

    #[test]
    fn reserved_bytes_are_refunded_without_erasing_other_usage() {
        let mut cfg = Config::from_env().unwrap();
        cfg.download_budget = 100;
        let limits = Limits::default();
        let first = limits.reserve_download("127.0.0.1", 80, &cfg).unwrap();
        assert!(limits.consume("127.0.0.1", 20, Direction::Download, &cfg));
        assert!(limits.reserve_download("127.0.0.1", 1, &cfg).is_none());
        limits.refund("127.0.0.1", first, 80, cfg.budget_window_ms);
        assert!(limits.consume("127.0.0.1", 80, Direction::Download, &cfg));
        assert!(limits.reserve_download("127.0.0.1", 1, &cfg).is_none());
    }

    #[test]
    fn active_lease_releases_on_drop_and_rate_is_bounded() {
        let mut cfg = Config::from_env().unwrap();
        cfg.max_active_per_ip = 1;
        cfg.rate_max = 2;
        let limits = Limits::default();
        let lease = limits.active("127.0.0.1", &cfg).unwrap();
        assert!(limits.active("127.0.0.1", &cfg).is_err());
        drop(lease);
        assert!(limits.active("127.0.0.1", &cfg).is_ok());
        assert!(limits.rate("127.0.0.1", false, &cfg));
        assert!(limits.rate("127.0.0.1", false, &cfg));
        assert!(!limits.rate("127.0.0.1", false, &cfg));
    }

    #[test]
    fn rate_map_respects_configured_capacity() {
        let mut cfg = Config::from_env().unwrap();
        cfg.rate_map_max = 100;
        let limits = Limits::default();
        for index in 0..100 {
            assert!(limits.rate(&format!("192.0.2.{index}"), false, &cfg));
        }
        assert!(limits.rate("198.51.100.1", false, &cfg));
        assert_eq!(limits.0.lock().unwrap().general.len(), 100);
        // Node evicts the first inserted IP instead of rejecting the newcomer.
        assert!(limits.rate("192.0.2.0", false, &cfg));
    }

    #[test]
    fn byte_budget_map_evicts_oldest_and_failed_reservations_do_not_insert() {
        let mut cfg = Config::from_env().unwrap();
        cfg.rate_map_max = 100;
        cfg.download_budget = 2;
        let limits = Limits::default();
        assert!(limits.reserve_download("rejected", 3, &cfg).is_none());
        assert!(limits.0.lock().unwrap().download.entries.is_empty());
        for index in 0..100 {
            assert!(
                limits
                    .reserve_download(&format!("192.0.2.{index}"), 1, &cfg)
                    .is_some()
            );
        }
        assert!(limits.reserve_download("new", 1, &cfg).is_some());
        let state = limits.0.lock().unwrap();
        assert_eq!(state.download.entries.len(), 100);
        assert_eq!(state.download.order.len(), 100);
        assert!(!state.download.entries.contains_key("192.0.2.0"));
        assert!(state.download.entries.contains_key("192.0.2.1"));
        assert!(state.download.entries.contains_key("new"));
    }

    #[test]
    fn refund_removes_empty_budget_slot() {
        let mut cfg = Config::from_env().unwrap();
        cfg.download_budget = 100;
        let limits = Limits::default();
        let at = limits.reserve_download("127.0.0.1", 70, &cfg).unwrap();
        limits.refund("127.0.0.1", at, 70, cfg.budget_window_ms);
        let state = limits.0.lock().unwrap();
        assert!(state.download.entries.is_empty());
        assert!(state.download.order.is_empty());
    }
}
