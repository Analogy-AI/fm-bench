"""Shared helpers for scripted policies. Policies only see observation data."""

from __future__ import annotations


def call(game, name: str, args: dict | None = None) -> dict:
    """Call a tool; never raise. Returns the envelope (ok may be False)."""
    return game.call_tool(name, args or {})


def data(game, name: str, args: dict | None = None):
    env = call(game, name, args)
    return env.get("data") if env.get("ok") else None


def band_center(view: dict) -> float:
    a = view.get("ability") or {}
    return (a.get("ca_low", 0) + a.get("ca_high", 0)) / 2.0


def sorted_by_ability(views: list[dict]) -> list[dict]:
    return sorted(views, key=lambda v: (-band_center(v), v["pid"]))


def fit_players(squad: list[dict]) -> list[dict]:
    return [p for p in squad if p.get("injury_days", 0) == 0]


def best_lineup_ids(squad: list[dict], avoid_exhausted: bool = True) -> list[str]:
    pool = fit_players(squad)
    if avoid_exhausted:
        fresh = [p for p in pool if p.get("fatigue") != "exhausted"]
        if len(fresh) >= 11:
            pool = fresh
    return [p["pid"] for p in sorted_by_ability(pool)[:11]]


def value_proxy_m(view: dict) -> float:
    """Crude transfer-value anchor from observable data only (no truth):
    wage-anchored, youth-premium, cliff for veterans."""
    wage = view.get("wage_m_per_year") or 1.0
    age = view.get("age", 27)
    age_mult = 3.0 if age <= 23 else 2.0 if age <= 27 else 1.0 if age <= 30 else 0.4
    return max(0.5, wage * 3.0 * age_mult)


# ---------------------------------------------------------------------------
# v0.3 draft handling (policies call these on a DRAFT stop)
# ---------------------------------------------------------------------------

def draft_pool(game) -> dict | None:
    return data(game, "get_draft_pool")


def _card_mid(c: dict) -> float:
    b = c.get("band") or {}
    return (b.get("ca_low", 0) + b.get("ca_high", 0)) / 2.0


def draft_balanced_picks(pool: dict) -> list[str]:
    """Positional needs by band-per-cost, ~19 players, small cash reserve."""
    cards, budget = pool["pool"], float(pool["budget_m"])
    needs = [("GK", 2), ("CB", 3), ("FB", 2), ("DM", 2), ("CM", 3), ("W", 2),
             ("AM", 1), ("ST", 3)]
    picks: list[str] = []
    spent = 0.0

    def afford(c) -> bool:
        return spent + c["price_m"] <= budget - 8

    for pos, n in needs:
        cands = sorted((c for c in cards if c["pos"] == pos),
                       key=lambda c: (-_card_mid(c) / (c["price_m"] + 15.0),
                                      c["id"]))
        taken = 0
        for c in cands:
            if taken >= n:
                break
            if afford(c):
                picks.append(c["id"]); spent += c["price_m"]; taken += 1
    for c in sorted(cards, key=lambda c: (-_card_mid(c), c["id"])):
        if c["id"] in picks or len(picks) >= 19:
            continue
        if afford(c):
            picks.append(c["id"]); spent += c["price_m"]
    return picks


def draft_stars_picks(pool: dict) -> list[str]:
    """Most expensive talent first; auto_fill repairs the tail."""
    cards, budget = pool["pool"], float(pool["budget_m"])
    picks, spent = [], 0.0
    for c in sorted(cards, key=lambda c: (-c["price_m"], c["id"])):
        if spent + c["price_m"] <= budget:
            picks.append(c["id"]); spent += c["price_m"]
    return picks


def draft_random_picks(pool: dict, seed: int) -> list[str]:
    """Deterministic pseudo-random legal-ish list (auto_fill repairs)."""
    import hashlib
    cards = pool["pool"]
    ranked = sorted(cards, key=lambda c: hashlib.blake2s(
        f"{seed}:{c['id']}".encode(), digest_size=8).hexdigest())
    return [c["id"] for c in ranked[:16]]


def handle_draft(game, packet: dict, style: str = "balanced") -> bool:
    """If this is the draft stop, submit picks (style: balanced | stars |
    floor | random) and return True. auto_fill backstops validity."""
    if "DRAFT" not in packet.get("stop_types", ()):
        return False
    pool = draft_pool(game)
    if pool is None:
        return True
    if style == "stars":
        picks = draft_stars_picks(pool)
    elif style == "floor":
        picks = []
    elif style == "random":
        picks = draft_random_picks(pool, game.world.seed)
    else:
        picks = draft_balanced_picks(pool)
    call(game, "submit_draft", {"picks": picks, "auto_fill": True})
    return True
