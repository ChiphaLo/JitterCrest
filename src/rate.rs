//! Bounded, insertion-ordered per-IP request windows matching the Node Maps.
use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

#[derive(Default)]
struct Window {
    start: Option<Instant>,
    count: u32,
}

#[derive(Clone, Copy)]
pub(crate) enum RateKind {
    // HTTP general/speed and RTC offers: find one stale entry before FIFO.
    Standard,
    // RTC cancel: FIFO only, and the 10-second window resets at equality.
    Cancel,
}

#[derive(Default)]
pub(crate) struct RateTable {
    entries: HashMap<String, Window>,
    order: VecDeque<String>,
}

impl RateTable {
    pub(crate) fn len(&self) -> usize {
        self.entries.len()
    }

    pub(crate) fn check(&mut self, ip: &str, max: u32, map_max: usize, kind: RateKind) -> bool {
        self.check_at(ip, max, map_max, kind, Instant::now())
    }

    fn check_at(
        &mut self,
        ip: &str,
        max: u32,
        map_max: usize,
        kind: RateKind,
        now: Instant,
    ) -> bool {
        if !self.entries.contains_key(ip) {
            if self.len() >= map_max {
                let old = if matches!(kind, RateKind::Standard) {
                    self.order
                        .iter()
                        .find(|key| {
                            self.entries.get(*key).is_some_and(|window| {
                                window.start.is_some_and(|start| {
                                    now.duration_since(start) >= Duration::from_secs(60)
                                })
                            })
                        })
                        .cloned()
                } else {
                    None
                }
                .or_else(|| self.order.front().cloned());
                if let Some(old) = old {
                    self.entries.remove(&old);
                    if let Some(index) = self.order.iter().position(|key| key == &old) {
                        self.order.remove(index);
                    }
                }
            }
            self.order.push_back(ip.to_owned());
        }
        let entry = self.entries.entry(ip.to_owned()).or_default();
        let expired = entry.start.is_none_or(|start| {
            let elapsed = now.duration_since(start);
            if matches!(kind, RateKind::Cancel) {
                elapsed >= Duration::from_secs(10)
            } else {
                elapsed > Duration::from_secs(10)
            }
        });
        if expired {
            entry.start = Some(now);
            entry.count = 0;
        }
        entry.count = entry.count.saturating_add(1);
        entry.count <= max
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fifo_eviction_keeps_existing_key_at_original_position() {
        let mut table = RateTable::default();
        let now = Instant::now();
        assert!(table.check_at("first", 2, 2, RateKind::Standard, now));
        assert!(table.check_at("second", 2, 2, RateKind::Standard, now));
        assert!(table.check_at("first", 2, 2, RateKind::Standard, now));
        assert!(table.check_at("third", 2, 2, RateKind::Standard, now));
        assert!(!table.entries.contains_key("first"));
        assert_eq!(
            table.order,
            VecDeque::from(["second".into(), "third".into()])
        );
        assert!(table.check_at("first", 2, 2, RateKind::Standard, now));
        assert_eq!(table.len(), 2);
        assert!(!table.entries.contains_key("second"));
    }

    #[test]
    fn standard_prefers_stale_ip_and_cancel_resets_at_boundary() {
        let now = Instant::now();
        let mut table = RateTable::default();
        assert!(table.check_at("fresh", 1, 2, RateKind::Standard, now));
        assert!(table.check_at("stale", 1, 2, RateKind::Standard, now));
        table.entries.get_mut("stale").unwrap().start = Some(now - Duration::from_secs(60));
        assert!(table.check_at("new", 1, 2, RateKind::Standard, now));
        assert!(table.entries.contains_key("fresh"));
        assert!(!table.entries.contains_key("stale"));

        let mut cancel = RateTable::default();
        assert!(cancel.check_at("ip", 1, 2, RateKind::Cancel, now));
        assert!(!cancel.check_at("ip", 1, 2, RateKind::Cancel, now));
        assert!(cancel.check_at("ip", 1, 2, RateKind::Cancel, now + Duration::from_secs(10)));
        let mut standard = RateTable::default();
        assert!(standard.check_at("ip", 1, 2, RateKind::Standard, now));
        assert!(!standard.check_at(
            "ip",
            1,
            2,
            RateKind::Standard,
            now + Duration::from_secs(10)
        ));
    }
}
