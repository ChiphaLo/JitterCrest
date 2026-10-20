//! Server-side pacing/percentiles: finite filtering and nearest-rank index match Node.
#[must_use]
pub fn percentile(values: &[f64], p: f64) -> Option<f64> {
    let mut finite: Vec<_> = values.iter().copied().filter(|v| v.is_finite()).collect();
    finite.sort_by(f64::total_cmp);
    if finite.is_empty() {
        return None;
    }
    let index = ((finite.len() - 1) as f64 * p).floor() as usize;
    finite.get(index.min(finite.len() - 1)).copied()
}

#[must_use]
pub fn required_sample_count(planned: usize, density: f64) -> usize {
    (planned as f64 * density).ceil() as usize
}

#[derive(Debug, PartialEq)]
pub struct Accounting {
    pub planned: usize,
    pub scheduled: usize,
    pub skipped: usize,
    pub actual_sent: usize,
}

impl Accounting {
    #[must_use]
    pub fn valid(&self) -> bool {
        self.planned > 0
            && self.scheduled.checked_add(self.skipped) == Some(self.planned)
            && self.actual_sent <= self.scheduled
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn percentile_filters_nonfinite_and_uses_node_index() {
        assert_eq!(percentile(&[], 0.95), None);
        assert_eq!(percentile(&[f64::NAN, 4., 1., 3., 2.], 0.95), Some(3.));
    }
    #[test]
    fn density_and_accounting() {
        assert_eq!(required_sample_count(100, 0.99), 99);
        assert_eq!(required_sample_count(101, 0.99), 100);
        assert!(
            Accounting {
                planned: 4,
                scheduled: 3,
                skipped: 1,
                actual_sent: 2
            }
            .valid()
        );
        assert!(
            !Accounting {
                planned: 4,
                scheduled: 3,
                skipped: 0,
                actual_sent: 2
            }
            .valid()
        );
    }
}
