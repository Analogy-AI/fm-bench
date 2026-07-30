"""Composite score (score_v0.2 — calibration round 4).

S_raw = wh*ln(1 + H/h_div)                                    (honors)
      + ww*sign(VA)*ln(1 + |VA|/va_w_div)                     (net-worth VALUE ADDED)
      + wm*ln(1 + max(M,0)/m_div)                             (squad value, stabilizer)
S_final = rho(t) * max(S_raw, 0) + min(S_raw, 0)

where VA = (last-3-season deflated mean net worth) - (day-0 net worth of the
same seed's world). ZERO now means "left the club as valuable as you found
it, in real terms" — beating the three-inflation drag is the bar.

Why v0.2 (round-4 calibration evidence, docs/CALIBRATION_LOG.md):
- v0.1 scored ABSOLUTE wealth, so per-seed endowment luck explained 34% of
  score variance vs 16% for skill; subtracting the seed's own endowment
  flips that to 13% / 33% and un-inverts the hard-seed ordering.
- VA is measured on NET WORTH ONLY (cash + discounted squad + facilities -
  fee liabilities): buying players books the asset AND the cash/fee cost, so
  squad-shopping sprees cannot pump the score (the naive per-channel VA
  variant re-opened exactly that arbitrage and was rejected).
- The squad-value channel stays absolute but at reduced weight: it rewards
  fielding a valuable squad without double-counting trading profits.
- rho multiplies only the POSITIVE part: dying early can never shrink a
  negative result, so there is no "get fired to cap losses" arbitrage (H2).
- W/M remain last-3-season deflated means (anti end-game pumping);
  penalties still apply to S_final only (X3); comparability remains
  per-track (rho is relative to the configured run length).

The day-0 baseline is NOT stored in world state (state hashes unchanged);
it is regenerated deterministically from the seed on first use and cached
on the world object (worldgen output does not depend on which club is the
player club).
"""

from __future__ import annotations

import math

from engine.core.params import CENTS_PER_M

# SCORE_VERSION names the SCORING FORMULA, not the protocol. It is deliberately
# still "score_v0.2" (calibration round 4) even for v0.3 runs: the v0.3 protocol
# changes (equal-endowment draft, sealed-bid market, capped revival, and the
# per-death 0.8^k discount added below) alter the GAME and the run inputs, not
# the composite formula's shape. The protocol a run used is a SEPARATE field —
# `protocol_version` (e.g. "fm_v0.3") stamped on the result summary. So a stored
# per_seed reading {"score_version": "score_v0.2", ...} inside a summary whose
# {"protocol_version": "fm_v0.3"} is a v0.3 run scored with the v0.2 formula —
# it is NOT an old/mismatched scorer. (An auditor who conflated the two once
# read score_v0.2 as "the v0.3 scorer is missing" — it is not; it is here.)
SCORE_VERSION = "score_v0.2"
SCORE_FORMULA_VERSION = SCORE_VERSION  # explicit alias: this is the FORMULA id

# Display score: 100*S/(|S|+K) — a bounded 0-to-±100 PRESENTATION of the
# uncapped official score. Monotonic, invertible (S = K*d/(100-|d|)),
# zero-preserving ("returned the club as found" stays 0), asymptotic to ±100
# (never cut off). Rational saturation, not tanh: tanh approaches 100
# exponentially fast, so long-horizon tracks (where top S_final reaches
# 80-100+) bunched every strong model at 99.x; the rational form approaches
# 100 polynomially and keeps top-end gaps visible at any horizon. Slope at
# zero is identical (100/K per point). Purely cosmetic: S_final remains the
# official quantity and the tie-breaker; K is a display constant, deliberately
# NOT a params.yaml entry so it never perturbs params_hash stamps.
DISPLAY_K = 25.0


def display_score(s_final: float) -> float:
    return round(100.0 * s_final / (abs(s_final) + DISPLAY_K), 2)


def _deflate(cents: int, year: int, rate: float) -> float:
    return cents / CENTS_PER_M / ((1.0 + rate) ** (year - 1))


def _va_baselines(world) -> dict:
    """{cid: day-0 net worth cents} for every club, regenerated from seed.

    Deterministic pure function of (seed, params): worldgen ignores the
    player-club marking, so a fresh day-0 world has identical clubs. Cached
    per world instance; never serialized (snapshots/replay unaffected).
    """
    cached = getattr(world, "_va_baselines", None)
    if cached is None:
        from engine.domain.finance import financial_snapshot
        from engine.domain.worldgen import generate_world
        w0 = generate_world(world.seed, world.params, player_club=None,
                            years=world.years)
        cached = {cid: financial_snapshot(w0, club)["net_worth"]
                  for cid, club in w0.clubs.items()}
        world._va_baselines = cached
    return cached


def compute(world, cid: str | None = None) -> dict:
    params = world.params
    sc = params.score
    cid = cid or world.player_club_id
    if cid is None:
        raise ValueError("scoring requires a player club")
    rate = params.economy.inflation_revenue

    honors_points = sum(h["points"] for h in world.honors if h["cid"] == cid)
    honors_events = [h for h in world.honors if h["cid"] == cid]

    snaps = world.fin_snapshots[cid][-sc.last_n_seasons:]
    if snaps:
        w_real = sum(_deflate(s["net_worth"], s["year"], rate)
                     for s in snaps) / len(snaps)
        m_real = sum(_deflate(s["squad_value_true"], s["year"], rate)
                     for s in snaps) / len(snaps)
    else:
        w_real = m_real = 0.0
    # v0.3 draft/revival worlds: the baseline is stored state — the club's own
    # post-draft day-0 net worth, later incremented by revival injections so
    # bailout cash can never appear as value added. Legacy worlds keep the
    # seed-regenerated baseline (pure function, not state).
    if world.va_baseline_state:
        w_baseline = world.va_baseline_state.get(cid, 0) / CENTS_PER_M
    else:
        w_baseline = _va_baselines(world).get(cid, 0) / CENTS_PER_M
    # v0.3 revival: bailout injections raise the baseline one-for-one, so
    # rescue cash can never appear as value added
    w_baseline += world.va_injections.get(cid, 0) / CENTS_PER_M
    va = w_real - w_baseline

    h_term = sc.w_honors * math.log(1.0 + max(0, honors_points) / sc.h_div)
    w_term = sc.w_networth * math.copysign(
        math.log(1.0 + abs(va) / sc.va_w_div), va)
    m_term = sc.w_squadvalue * math.log(1.0 + max(0.0, m_real) / sc.m_div)
    raw = h_term + w_term + m_term

    t = world.settle_t if world.settle_t is not None else float(world.years)
    completed = world.settle_reason == "completed"
    # IMPORTANT for auditors replaying a run: rho is 1.0 ONLY once the world has
    # actually reached its final settlement (settle_reason == "completed"). Score
    # a world that has NOT completed — e.g. a replay stopped one season short, or
    # a mid-game snapshot — and rho < 1 (here rho_base, i.e. 0.8, at full term),
    # so the positive part is discounted and s_final comes out well below the
    # official figure. That is by design (no reward for a run you didn't finish),
    # NOT a formula/version difference. To reproduce a published S_final you must
    # replay to completion so settle_reason == "completed".
    rho = 1.0 if completed else sc.rho_base * (min(t, world.years) / world.years) ** sc.rho_exp
    # v0.3 revival: each death multiplies the positive part by
    # death_discount_base (0.8^k); the negative part passes through — same
    # asymmetry as rho, so dying can never improve a bad run
    deaths = world.death_count.get(cid, 0)
    dd = sc.get("death_discount_base", 0.8) ** deaths if deaths else 1.0
    s_final = rho * dd * max(raw, 0.0) + min(raw, 0.0)

    return {
        "channels": {
            "honors_points": honors_points,
            "honors_term": round(h_term, 4),
            "net_worth_baseline_m": round(w_baseline, 2),
            "net_worth_real_m": round(w_real, 2),
            "net_worth_term": round(w_term, 4),
            "net_worth_va_m": round(va, 2),
            "squad_value_real_m": round(m_real, 2),
            "squad_value_term": round(m_term, 4),
        },
        "honors_events": honors_events,
        "death_count": deaths,
        "death_discount": round(dd, 4),
        "rho": round(rho, 4),
        "s_display": display_score(s_final),
        "s_final": round(s_final, 4),
        "score_version": SCORE_VERSION,
        "settle_reason": world.settle_reason,
        "settle_t": round(t, 3),
        "years": world.years,
    }
