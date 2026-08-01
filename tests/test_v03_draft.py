"""v0.3 draft battery (protocol v0.3, change 1).

All scripted, $0. Covers: legacy no-op, pool shape/affordability invariants,
validation, determinism/replay-grade hashes, drafter-strategy gate
(balanced must beat greedy-stars and random; hoarding must not win), and
convergence (pool must not have one obvious solution).
"""

from __future__ import annotations

import pytest

from engine.core.params import M, load_params, apply_overrides
from engine.game import Game
from engine.persist.snapshot import state_hash
from engine.sim import draft as draft_mod

DRAFT_ON = {"draft": {"enabled": True}}


def _draft_game(seed: int, years: int = 5, overrides: dict | None = None) -> Game:
    ov = {"draft": {"enabled": True}}
    if overrides:
        ov = {**overrides, "draft": {**overrides.get("draft", {}), "enabled": True}}
    return Game(seed=seed, years=years, param_overrides=ov)


# ---------------------------------------------------------------------------
# legacy no-op
# ---------------------------------------------------------------------------

def test_default_off_worlds_unchanged():
    a = Game(seed=11, years=5)
    b = Game(seed=11, years=5)
    assert state_hash(a.world) == state_hash(b.world)
    assert not a.world.draft_open
    assert a.world.draft_templates == {}
    assert "draft" not in a.world.to_dict()
    assert "va_baseline_state" not in a.world.to_dict()


# ---------------------------------------------------------------------------
# pool invariants (across seeds)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_pool_shape_and_affordability(seed):
    g = _draft_game(seed)
    w = g.world
    cfg = w.params.draft
    assert len(w.draft_templates) == sum(int(t["count"]) for t in cfg.tiers)
    budget = M(cfg.budget_m)
    prices = sorted(w.draft_meta[t]["price_cents"] for t in w.draft_meta)
    # invariant 1 (owner decision #3): the best XI is unaffordable
    assert sum(prices[-11:]) > budget, "top-11 must exceed the budget"
    # invariant 2: at most ~4 top-tier picks fit even with a floor-tier rest
    top = sorted((w.draft_meta[t]["price_cents"]
                  for t in w.draft_meta
                  if w.draft_meta[t]["tier"] == "top"))
    floor = sorted(w.draft_meta[t]["price_cents"] for t in w.draft_meta
                   if w.draft_meta[t]["tier"] == "floor")
    five_stars_plus_floor = sum(top[:5]) + sum(floor[:int(cfg.min_picks) - 5])
    assert five_stars_plus_floor > budget, "5 stars + floor fill must not fit"
    # invariant 3: the cheapest min_picks are far below budget (always viable)
    assert sum(prices[:int(cfg.min_picks)]) < 0.4 * budget
    # invariant 4: every tier offers a GK; pool covers all 8 positions
    poss = {w.draft_templates[t].pos for t in w.draft_templates}
    assert len(poss) == 8
    for tier in ("starter", "rotation", "floor"):
        assert any(w.draft_templates[t].pos == "GK"
                   and w.draft_meta[t]["tier"] == tier for t in w.draft_meta)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_submission_validation():
    g = _draft_game(1)
    g.start()
    cards = g.call_tool("get_draft_pool", {})["data"]["pool"]
    cheap = [c["id"] for c in sorted(cards, key=lambda c: c["price_m"])]
    gk = [c["id"] for c in cards if c["pos"] == "GK"]
    non_gk = [c for c in cheap if c not in gk]

    r = g.call_tool("submit_draft", {"picks": non_gk[:20]})
    assert not r["ok"] and r["error"]["code"] == "no_goalkeeper"
    r = g.call_tool("submit_draft", {"picks": [gk[0]] + non_gk[:5]})
    assert not r["ok"] and r["error"]["code"] == "too_few_picks"
    top = [c["id"] for c in cards if c["tier"] in ("top", "starter")]
    r = g.call_tool("submit_draft", {"picks": top})
    assert not r["ok"] and r["error"]["code"] == "over_budget"
    r = g.call_tool("submit_draft", {"picks": [gk[0], gk[0]] + non_gk[:14]})
    assert not r["ok"] and r["error"]["code"] == "duplicate_picks"
    # advancing without a valid submission is refused and NOT logged
    r = g.call_tool("advance", {})
    assert not r["ok"] and r["error"]["code"] == "draft_pending"
    assert all(a["tool"] != "advance" for a in g.action_log)
    # auto_fill repairs a short list into a valid one
    r = g.call_tool("submit_draft", {"picks": non_gk[:3], "auto_fill": True})
    assert r["ok"] and r["data"]["picks"] >= g.world.params.draft.min_picks


# ---------------------------------------------------------------------------
# scripted drafter strategies (shared by gate tests below)
# ---------------------------------------------------------------------------

def _band_mid(c):
    return (c["band"]["ca_high"] + c["band"]["ca_low"]) / 2.0


def picks_greedy_stars(cards, budget):
    """Buy the most expensive talent first, fill the rest from the floor."""
    picks, spent = [], 0.0
    for c in sorted(cards, key=lambda c: (-c["price_m"], c["id"])):
        if len(picks) >= 15:
            break
        rest = 15 - len(picks) - 1
        floor_cost = rest * 1.0
        if spent + c["price_m"] + floor_cost <= budget:
            picks.append(c["id"]); spent += c["price_m"]
    have_gk = any(c["pos"] == "GK" for c in cards if c["id"] in picks)
    for c in sorted(cards, key=lambda c: (c["price_m"], c["id"])):
        if c["id"] in picks or len(picks) >= 15 and have_gk:
            continue
        if len(picks) >= 15 and have_gk:
            break
        if c["pos"] == "GK" and not have_gk:
            picks.append(c["id"]); spent += c["price_m"]; have_gk = True
        elif len(picks) < 15 and spent + c["price_m"] <= budget:
            picks.append(c["id"]); spent += c["price_m"]
    return picks


def picks_balanced(cards, budget):
    """Positional needs by band-per-cost, ~19 players, keep a small reserve."""
    needs = {"GK": 2, "CB": 3, "FB": 2, "DM": 2, "CM": 3, "W": 2, "AM": 1,
             "ST": 3}
    picks, spent = [], 0.0
    def afford(c):
        return spent + c["price_m"] <= budget - 8
    for pos, n in needs.items():
        cands = sorted((c for c in cards if c["pos"] == pos),
                       key=lambda c: (-_band_mid(c) / (c["price_m"] + 15), c["id"]))
        for c in cands[:n]:
            if afford(c):
                picks.append(c["id"]); spent += c["price_m"]
    for c in sorted(cards, key=lambda c: (-_band_mid(c), c["id"])):
        if c["id"] in picks or len(picks) >= 19:
            continue
        if afford(c):
            picks.append(c["id"]); spent += c["price_m"]
    return picks


def picks_cash_hoard(cards, budget):
    """Minimum legal squad, cheapest possible: maximize opening cash."""
    picks = []
    gk = sorted((c for c in cards if c["pos"] == "GK"),
                key=lambda c: (c["price_m"], c["id"]))
    picks.append(gk[0]["id"])
    for c in sorted(cards, key=lambda c: (c["price_m"], c["id"])):
        if c["id"] in picks:
            continue
        if len(picks) >= 15:
            break
        picks.append(c["id"])
    return picks


def picks_random(cards, budget, seed=7):
    import random
    r = random.Random(seed)
    ids = [c["id"] for c in cards]
    while True:
        sel = r.sample(ids, 16)
        cost = sum(c["price_m"] for c in cards if c["id"] in sel)
        if cost <= budget and any(c["pos"] == "GK" for c in cards
                                  if c["id"] in sel):
            return sel


def run_drafter(seed, strategy, years=5):
    """Draft by `strategy`, then hand the season to the heuristic policy.

    In-season play must be held at a competent constant, NOT idle: board
    targets scale with squad strength (X5), so idle play systematically
    rewards weak drafts (low squad -> bottom target -> survive by default)
    and punishes strong ones — the first run of this gate caught exactly
    that. Identical heuristic management isolates the draft decision.
    """
    from baselines import POLICIES
    g = _draft_game(seed, years=years)
    g.start()
    data = g.call_tool("get_draft_pool", {})["data"]
    picks = strategy(data["pool"], data["budget_m"])
    r = g.call_tool("submit_draft", {"picks": picks, "auto_fill": True})
    assert r["ok"], r
    env = g.call_tool("advance", {})
    pkt = env["data"]
    heuristic = POLICIES["heuristic"]
    while not pkt.get("game_over"):
        heuristic(g, pkt)
        pkt = g.advance()
    return g.final_result()


# ---------------------------------------------------------------------------
# the pricing gate (owner decision #3 + DESIGN test plan)
# ---------------------------------------------------------------------------

SEEDS = [1, 2, 3, 4, 5]


def _mean(xs):
    return sum(xs) / len(xs)


def test_draft_gate_balanced_beats_greedy_and_random():
    bal = [run_drafter(s, picks_balanced)["s_final"] for s in SEEDS]
    greedy = [run_drafter(s, picks_greedy_stars)["s_final"] for s in SEEDS]
    rand = [run_drafter(s, lambda c, b: picks_random(c, b))["s_final"]
            for s in SEEDS]
    assert _mean(bal) > _mean(greedy), (bal, greedy)
    assert _mean(bal) > _mean(rand), (bal, rand)


def test_draft_gate_hoarding_does_not_win():
    bal = [run_drafter(s, picks_balanced)["s_final"] for s in SEEDS]
    hoard = [run_drafter(s, picks_cash_hoard)["s_final"] for s in SEEDS]
    assert _mean(bal) > _mean(hoard), (bal, hoard)


def test_no_single_obvious_solution():
    """Different objectives must produce substantially different squads."""
    g = _draft_game(1)
    g.start()
    data = g.call_tool("get_draft_pool", {})["data"]
    cards, budget = data["pool"], data["budget_m"]
    a = set(picks_balanced(cards, budget))
    b = set(picks_greedy_stars(cards, budget))
    c = set(picks_cash_hoard(cards, budget))
    # the sensible strategy must not collapse onto either degenerate corner
    # (the corners themselves share a floor-tier tail by construction, so
    # comparing greedy vs hoard would only measure that tail)
    for x, y in ((a, b), (a, c)):
        overlap = len(x & y) / min(len(x), len(y))
        assert overlap < 0.8, f"strategies converge: {overlap:.2f}"


# ---------------------------------------------------------------------------
# determinism / state
# ---------------------------------------------------------------------------

def test_draft_run_determinism():
    def once():
        r = run_drafter(2, picks_balanced, years=3)
        return (r["s_final"], r["final_state_hash"])
    assert once() == once()


def test_va_baseline_stored_and_neutral():
    r_heavy = run_drafter(4, picks_greedy_stars, years=1)
    r_light = run_drafter(4, picks_cash_hoard, years=1)
    # post-draft baseline rule: neither converting the budget into players
    # nor hoarding it as cash may show up as day-0 value added — after one
    # season both VAs must sit in a modest band (wages/opex burn only),
    # not at the -100M scale that a pre-draft cash baseline would produce
    for r in (r_heavy, r_light):
        va = r["channels"]["net_worth_va_m"]
        assert -80.0 < va < 80.0, (va, r["channels"])


def test_va_baseline_covers_all_clubs():
    g = _draft_game(4)
    g.start()
    data = g.call_tool("get_draft_pool", {})["data"]
    g.call_tool("submit_draft",
                {"picks": picks_balanced(data["pool"], data["budget_m"]),
                 "auto_fill": True})
    g.call_tool("advance", {})
    assert set(g.world.va_baseline_state) == set(g.world.clubs)
