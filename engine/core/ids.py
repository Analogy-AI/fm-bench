"""Deterministic sequential id allocation per entity prefix."""

from __future__ import annotations


class IdAllocator:
    def __init__(self):
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}{n:05d}"

    def to_dict(self) -> dict:
        return dict(sorted(self._counters.items()))

    @classmethod
    def from_dict(cls, d: dict) -> "IdAllocator":
        a = cls()
        a._counters = dict(d)
        return a
