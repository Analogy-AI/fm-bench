"""Transfer market entities and valuation model.

V_true (true market value) is ENGINE-INTERNAL: it feeds score settlement and
match-of-record accounting only. AI clubs and the observation layer must use
belief-based values (see sim/scouting.py) — X1 ruling.
"""

from __future__ import annotations

import math

from engine.core.params import Params


def _age_curve(params: Params, age: int) -> float:
    curve = sorted((int(k), float(v)) for k, v in params.valuation.age_curve.items())
    if age <= curve[0][0]:
        return curve[0][1]
    if age >= curve[-1][0]:
        return curve[-1][1]
    for (a1, v1), (a2, v2) in zip(curve, curve[1:]):
        if a1 <= age <= a2:
            t = (age - a1) / (a2 - a1)
            return v1 + t * (v2 - v1)
    return 1.0


def value_from_ability(params: Params, ca_fixed: int, age: int, year: int,
                       months_left: int, form: float) -> int:
    """Market value in cents given an ability estimate (true or believed)."""
    v = params.valuation
    ca_display = ca_fixed / 10.0
    base = v.a_cents * math.exp(v.k_per_ca_pt * ca_display)
    base *= _age_curve(params, age)
    if months_left >= 24:
        base *= v.contract_gt24
    elif months_left >= 12:
        base *= v.contract_12to24
    else:
        base *= v.contract_lt12
    base *= 0.9 + 0.2 * max(0.0, min(1.0, (form - 5.8) / 1.4))
    base *= (1.0 + params.economy.inflation_transfer) ** (year - 1)
    return int(base)


LADDER_STEP = 1.15
_LADDER_ANCHOR = 1_000_000  # 0.01M in cents


def quantize_ask(cents: int) -> int:
    """Coarse-bucket a price onto a ~15% geometric ladder (X1/G3: prices
    carry at most one rung of information, so ask inversion resolves no
    finer than the scouting bands)."""
    if cents <= 0:
        return 0
    import math
    rung = round(math.log(cents / _LADDER_ANCHOR) / math.log(LADDER_STEP))
    return int(_LADDER_ANCHOR * LADDER_STEP ** max(rung, 0))


class Offer:
    __slots__ = ("oid", "player_id", "buyer_cid", "seller_cid", "amount",
                 "status", "rounds", "created_abs_day", "expires_abs_day",
                 "counter_amount", "ask_at_creation")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        self.status = kw.get("status", "pending")
        self.rounds = kw.get("rounds", 0)
        self.counter_amount = kw.get("counter_amount", 0)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "Offer":
        return cls(**d)


class MarketState:
    """Offers plus the adaptive-counterpressure state (DESIGN §7.3 layer 1)."""

    def __init__(self):
        self.offers: dict[str, Offer] = {}
        # (buyer_cid, seller_cid) -> markup multiplier state (starts at 0.0 extra)
        self.source_markup: dict[str, float] = {}
        # per-seed systematic mispricing by age bucket, rolled at worldgen (X1)
        self.age_mispricing: dict[str, float] = {}
        self.transfer_log: list[dict] = []
        # per-window ask bumps after rejected lowballs: pid -> mult (reset at window open)
        self.window_ask_bump: dict[str, float] = {}

    @staticmethod
    def pair_key(buyer_cid: str, seller_cid: str) -> str:
        return f"{buyer_cid}>{seller_cid}"

    def markup(self, buyer_cid: str, seller_cid: str) -> float:
        return self.source_markup.get(self.pair_key(buyer_cid, seller_cid), 0.0)

    def bump_markup(self, buyer_cid: str, seller_cid: str, amount: float) -> None:
        k = self.pair_key(buyer_cid, seller_cid)
        self.source_markup[k] = self.source_markup.get(k, 0.0) + amount

    def season_decay(self, rate: float) -> None:
        for k in sorted(self.source_markup):
            v = self.source_markup[k] * (1.0 - rate)
            if v < 0.005:
                del self.source_markup[k]
            else:
                self.source_markup[k] = v

    def mispricing_for_age(self, age: int) -> float:
        if age <= 21:
            return self.age_mispricing.get("young", 0.0)
        if age >= 30:
            return self.age_mispricing.get("old", 0.0)
        return self.age_mispricing.get("prime", 0.0)

    def bump_window_ask(self, pid: str, rate: float) -> None:
        self.window_ask_bump[pid] = self.window_ask_bump.get(pid, 1.0) * (1.0 + rate)

    def clear_window_bumps(self) -> None:
        self.window_ask_bump = {}

    def to_dict(self) -> dict:
        return {
            "age_mispricing": dict(sorted(self.age_mispricing.items())),
            "offers": {k: v.to_dict() for k, v in sorted(self.offers.items())},
            "source_markup": dict(sorted(self.source_markup.items())),
            "transfer_log": self.transfer_log,
            "window_ask_bump": dict(sorted(self.window_ask_bump.items())),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MarketState":
        m = cls()
        m.age_mispricing = dict(d["age_mispricing"])
        m.offers = {k: Offer.from_dict(v) for k, v in d["offers"].items()}
        m.source_markup = dict(d["source_markup"])
        m.transfer_log = list(d["transfer_log"])
        m.window_ask_bump = dict(d.get("window_ask_bump", {}))
        return m
