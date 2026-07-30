"""Counter-based deterministic RNG.

DESIGN §2.2 calls for numpy Philox; DECISIONS #5 locks deps to stdlib, so we
use a blake2s counter construction with the same structural property: every
(domain, entity, day) triple owns an independent stream, so consuming more or
fewer draws in one stream never shifts any other stream.
"""

from __future__ import annotations

import hashlib
import math


class Stream:
    """One isolated random stream. All draws derive from blake2s(key, counter)."""

    __slots__ = ("_key", "_ctr", "_buf", "_pos", "_gauss_spare")

    def __init__(self, key: bytes):
        self._key = key
        self._ctr = 0
        self._buf = b""
        self._pos = 0
        self._gauss_spare: float | None = None

    def _refill(self) -> None:
        h = hashlib.blake2s(
            self._ctr.to_bytes(8, "little"), key=self._key, digest_size=32
        )
        self._ctr += 1
        self._buf = h.digest()
        self._pos = 0

    def u64(self) -> int:
        if self._pos + 8 > len(self._buf):
            self._refill()
        v = int.from_bytes(self._buf[self._pos : self._pos + 8], "little")
        self._pos += 8
        return v

    def uniform(self, a: float = 0.0, b: float = 1.0) -> float:
        return a + (b - a) * (self.u64() / 2**64)

    def randint(self, a: int, b: int) -> int:
        """Inclusive [a, b]. Modulo bias is negligible for game-sized ranges."""
        if b < a:
            raise ValueError("randint: b < a")
        return a + self.u64() % (b - a + 1)

    def chance(self, p: float) -> bool:
        return self.uniform() < p

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        if self._gauss_spare is not None:
            z = self._gauss_spare
            self._gauss_spare = None
        else:
            u1 = max(self.uniform(), 1e-12)
            u2 = self.uniform()
            r = math.sqrt(-2.0 * math.log(u1))
            z = r * math.cos(2.0 * math.pi * u2)
            self._gauss_spare = r * math.sin(2.0 * math.pi * u2)
        return mu + sigma * z

    def truncgauss(self, mu: float, sigma: float, lo: float, hi: float) -> float:
        for _ in range(64):
            v = self.gauss(mu, sigma)
            if lo <= v <= hi:
                return v
        return min(max(mu, lo), hi)

    def poisson(self, lam: float) -> int:
        """Knuth; fine for small lambda (match segments use lambda/6 < 1)."""
        if lam <= 0:
            return 0
        limit = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= self.uniform()
            if p <= limit:
                return k
            k += 1
            if k > 50:  # hard safety, unreachable for game lambdas
                return k

    def choice(self, seq):
        if not seq:
            raise ValueError("choice on empty sequence")
        return seq[self.u64() % len(seq)]

    def weighted_choice(self, items, weights):
        total = sum(weights)
        if total <= 0:
            return self.choice(items)
        x = self.uniform(0.0, total)
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if x < acc:
                return item
        return items[-1]

    def shuffle(self, lst: list) -> None:
        for i in range(len(lst) - 1, 0, -1):
            j = self.u64() % (i + 1)
            lst[i], lst[j] = lst[j], lst[i]


class RNG:
    """Stream factory keyed by (domain, entity, day)."""

    def __init__(self, world_seed: int):
        self.world_seed = int(world_seed)

    def stream(self, domain: str, entity: object = "", day: int = 0) -> Stream:
        raw = f"{self.world_seed}|{domain}|{entity}|{day}".encode()
        key = hashlib.blake2s(raw, digest_size=32).digest()
        return Stream(key)
