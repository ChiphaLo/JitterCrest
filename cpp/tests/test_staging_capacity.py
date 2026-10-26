"""Pure byte-accounting regression: no SSH, filesystem mutation or services."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from staging_capacity import (MIN_METADATA_MARGIN_BYTES, MIN_RESERVE_BYTES,
                              StagingCapacity)


class StagingCapacityTest(unittest.TestCase):
    def test_all_copies_and_margin_are_additive(self):
        cost = StagingCapacity(70 << 20, 104 << 20, 13 << 20, 50 << 20)
        self.assertEqual(cost.copied_bytes, 237 << 20)
        self.assertEqual(cost.required_free_bytes, 1021 << 20)
        self.assertFalse(cost.admits(907 << 20))
        self.assertFalse(cost.admits(cost.required_free_bytes - 1))
        self.assertTrue(cost.admits(cost.required_free_bytes))
        self.assertTrue(cost.admits(cost.required_free_bytes + 1))

    def test_old_expanded_times_two_can_underbudget(self):
        cost = StagingCapacity(70 << 20, 104 << 20, 13 << 20, 50 << 20)
        old = 2 * cost.expanded_bytes + MIN_RESERVE_BYTES
        free = old + 1
        self.assertGreater(free, old)
        self.assertFalse(cost.admits(free))

    def test_exact_sizes_not_compression_guess(self):
        a = StagingCapacity(1, 10, 100, 1000)
        b = StagingCapacity(2, 20, 200, 2000)
        self.assertEqual(b.required_free_bytes - a.required_free_bytes, 1111)

    def test_reserve_and_margin_cannot_be_lowered(self):
        for field, minimum in [('reserve_bytes', MIN_RESERVE_BYTES),
                               ('metadata_margin_bytes', MIN_METADATA_MARGIN_BYTES)]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    StagingCapacity(0, 0, 0, 0, **{field: minimum - 1})
                cost = StagingCapacity(0, 0, 0, 0, **{field: minimum + 1})
                self.assertEqual(getattr(cost, field), minimum + 1)

    def test_invalid_counts_rejected_for_every_field(self):
        defaults = dict(archive_bytes=1, expanded_bytes=1, reference_bytes=1, proxy_bytes=1)
        for field in StagingCapacity.__dataclass_fields__:
            for bad in [-1, True, False, 1.0, float('nan'), float('inf'), '1', None]:
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ValueError):
                        StagingCapacity(**{**defaults, field: bad})

    def test_invalid_free_space_rejected(self):
        for bad in [-1, True, 1.0, float('nan'), '1', None]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    StagingCapacity(0, 0, 0, 0).admits(bad)

    def test_large_integer_is_not_rounded_or_overflowed(self):
        value = 2 ** 60 + 1
        cost = StagingCapacity(value, value, value, value)
        self.assertEqual(cost.copied_bytes, 4 * value)
        self.assertFalse(cost.admits(cost.required_free_bytes - 1))

    def test_budget_cannot_be_mutated_after_admission(self):
        cost = StagingCapacity(1, 2, 3, 4)
        with self.assertRaises(FrozenInstanceError):
            cost.reserve_bytes = 0


if __name__ == '__main__':
    unittest.main()
