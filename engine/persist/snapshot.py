"""Full world serialization and the deterministic state hash used by CI."""

from __future__ import annotations

import hashlib
import json


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def state_hash(world) -> str:
    return hashlib.blake2s(canonical(world.to_dict()).encode(),
                           digest_size=16).hexdigest()


def save(world, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical(world.to_dict()))
