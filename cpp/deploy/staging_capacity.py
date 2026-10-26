"""Disk admission for an isolated paired C++/Rust staging deployment.

This calculates bytes only. It never connects, copies, deletes, installs or
changes a service. Measure archive, expanded files, reference and proxy first.
The retained free-space reserve is not interchangeable with the copy budget.
"""
from dataclasses import dataclass


MIN_RESERVE_BYTES = 768 << 20
MIN_METADATA_MARGIN_BYTES = 16 << 20


@dataclass(frozen=True)
class StagingCapacity:
    archive_bytes: int
    expanded_bytes: int
    reference_bytes: int
    proxy_bytes: int
    metadata_margin_bytes: int = MIN_METADATA_MARGIN_BYTES
    reserve_bytes: int = MIN_RESERVE_BYTES

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            # bool, floats, NaN and negative inputs must not bypass admission.
            if type(value) is not int or value < 0:
                raise ValueError(f'{name} must be a nonnegative integer byte count')
        if self.metadata_margin_bytes < MIN_METADATA_MARGIN_BYTES:
            raise ValueError('metadata margin must be at least 16 MiB')
        if self.reserve_bytes < MIN_RESERVE_BYTES:
            raise ValueError('retained free-space reserve must be at least 768 MiB')

    @property
    def copied_bytes(self):
        return (self.archive_bytes + self.expanded_bytes + self.reference_bytes +
                self.proxy_bytes)

    @property
    def required_free_bytes(self):
        return self.copied_bytes + self.metadata_margin_bytes + self.reserve_bytes

    def admits(self, free_bytes):
        if type(free_bytes) is not int or free_bytes < 0:
            raise ValueError('free_bytes must be a nonnegative integer byte count')
        return free_bytes >= self.required_free_bytes
