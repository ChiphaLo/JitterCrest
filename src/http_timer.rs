//! Node's HTTP request expiration is checked on a server-wide 30 s cadence.
//! Hyper normally starts an independent timer for every request head; align
//! that timer to the next check tick instead of rounding its duration alone.
use hyper::rt::{Sleep, Timer};
use hyper_util::rt::TokioTimer;
use std::{
    pin::Pin,
    time::{Duration, Instant},
};

const CHECK_INTERVAL: Duration = Duration::from_secs(30);

#[derive(Clone)]
pub struct NodeCheckTimer {
    epoch: Instant,
    inner: TokioTimer,
}

impl NodeCheckTimer {
    pub fn new() -> Self {
        Self {
            epoch: Instant::now(),
            inner: TokioTimer::new(),
        }
    }

    fn after_deadline(&self, deadline: Instant) -> Instant {
        let periods =
            deadline.saturating_duration_since(self.epoch).as_secs() / CHECK_INTERVAL.as_secs() + 1;
        self.epoch + CHECK_INTERVAL * u32::try_from(periods).unwrap_or(u32::MAX)
    }

    pub fn request_deadline(&self, start: Instant, timeout_ms: u64) -> tokio::time::Instant {
        self.after_deadline(start + Duration::from_millis(timeout_ms))
            .into()
    }
}

impl Timer for NodeCheckTimer {
    fn sleep(&self, duration: Duration) -> Pin<Box<dyn Sleep>> {
        self.sleep_until(Instant::now() + duration)
    }

    fn sleep_until(&self, deadline: Instant) -> Pin<Box<dyn Sleep>> {
        self.inner.sleep_until(self.after_deadline(deadline))
    }

    fn reset(&self, sleep: &mut Pin<Box<dyn Sleep>>, deadline: Instant) {
        self.inner.reset(sleep, self.after_deadline(deadline));
    }

    fn now(&self) -> Instant {
        self.inner.now()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expiration_uses_next_global_tick_strictly_after_deadline() {
        let timer = NodeCheckTimer::new();
        let start = timer.epoch + Duration::from_millis(100);
        assert_eq!(
            timer.request_deadline(start, 1000),
            (timer.epoch + CHECK_INTERVAL).into()
        );
        assert_eq!(
            timer.request_deadline(start, 35_000),
            (timer.epoch + CHECK_INTERVAL * 2).into()
        );
        assert_eq!(
            timer.request_deadline(start, 60_000),
            (timer.epoch + CHECK_INTERVAL * 3).into()
        );
        assert_eq!(
            timer.request_deadline(timer.epoch + Duration::from_secs(29), 1000),
            (timer.epoch + CHECK_INTERVAL * 2).into()
        );
    }
}
