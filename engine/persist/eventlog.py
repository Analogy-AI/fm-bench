"""Append-only event log with a prev_hash chain (auditable results)."""

from __future__ import annotations

import hashlib
import json


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class EventLog:
    def __init__(self):
        self.entries: list[dict] = []
        self._last_hash = "0" * 16

    def append(self, etype: str, date, data: dict) -> None:
        entry = {
            "data": data, "day": date.day, "prev_hash": self._last_hash,
            "seq": len(self.entries), "type": etype, "year": date.year,
        }
        self._last_hash = hashlib.blake2s(
            canonical(entry).encode(), digest_size=8).hexdigest()
        self.entries.append(entry)

    def chain_hash(self) -> str:
        return self._last_hash

    def write_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(canonical(e) + "\n")
