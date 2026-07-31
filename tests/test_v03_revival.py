"""v0.3 revival + uniform bailout battery (protocol v0.3, change 3).

All scripted, $0. Covers: legacy no-op, no-death-arbitrage (self-destruct
twin probe), VA injection neutrality (dying idle vs living idle), death
discount monotonicity, unified death types, bot bailout symmetry, and
full-run determinism with revival on.
"""

from __future__ import annotations

from engine.game import Game
from engine.persist.snapshot import state_hash

REVIVAL = {"revival": {"enabled": True}}
FULL = {"revival": {"enabled": True}, "draft": {"enabled": True},
        "market_ai": {"sealed_bids": True}}


def _run_policy(seed, years, policy, overrides):
    from baselines import POLICIES
    g = Game(seed=seed, years=years, param_overrides=overrides)
    pkt = g.start()
    if g.world.draft_open:
        pool = g.call_tool("get_draft_pool", {})["data"]["pool"]
        gk = [c["id"] for c in pool if c["pos"] == "GK"][:1]
        cheap = [c["id"] for c in sorted(pool, key=lambda c: c["price_m"])
                 if c["id"] not in gk][:16]
        g.call_tool("submit_draft", {"picks": gk + cheap, "auto_fill": True})
        pkt = g.call_tool("advance", {})["data"]
    fn = POLICIES[policy]
    while not pkt.get("game_over"):
        fn(g, pkt)
        pkt = g.advance()
    return g


# ---------------------------------------------------------------------------
# legacy no-op
# ---------------------------------------------------------------------------

def test_default_off_unchanged():
    a = Game(seed=17, years=3, player_club=None)
    b = Game(seed=17, years=3, player_club=None)
    assert a.simulate_headless()["final_hash"] == \
        b.simulate_headless()["final_hash"]
    d = a.world.to_dict()
    for k in ("death_count", "bailout_count", "va_injections"):
        assert k not in d


# ---------------------------------------------------------------------------
# revival flow: a run that used to die completes the horizon with deaths
# counted and discounts applied
# ---------------------------------------------------------------------------

def test_idle_exhausts_cap_then_settles():
    """A hopeless seat gets max_deaths revivals, then the next death settles
    for real (owner decision 2026-07-17: stop burning cost on zombies)."""
    g = _run_policy(1, 5, "idle", REVIVAL)
    r = g.final_result()
    cap = g.world.params.revival.get("max_deaths", 3)
    assert r["death_count"] == cap + 1
    assert r["settle_reason"] in ("fired", "administration")
    assert r["death_discount"] < 1.0


def test_single_death_still_revives_to_horizon():
    """The cap must not reintroduce the zero-signal problem: a seat under
    the cap keeps playing to the horizon."""
    g = _run_policy(2, 5, "heuristic", REVIVAL)
    r = g.final_result()
    assert r["death_count"] <= g.world.params.revival.get("max_deaths", 3)
    assert r["settle_reason"] in ("completed", "abandoned")


def test_deaths_monotone_discount():
    g = _run_policy(1, 5, "idle", REVIVAL)
    r = g.final_result()
    k = r["death_count"]
    assert k >= 1
    base = g.world.params.score.get("death_discount_base", 0.8)
    assert abs(r["death_discount"] - base ** k) < 1e-9


# ---------------------------------------------------------------------------
# no death arbitrage: an agent that engineers extra deaths must not beat
# its identical twin that lives clean. We compare heuristic (survives) vs
# idle (dies repeatedly): idle already collects bailout money, so if
# bailouts leaked into VA idle would jump the ladder.
# ---------------------------------------------------------------------------

def test_injection_neutral_and_no_arbitrage():
    live = _run_policy(2, 5, "heuristic", REVIVAL).final_result()
    die = _run_policy(2, 5, "idle", REVIVAL).final_result()
    assert live["s_final"] > die["s_final"], (live["s_final"], die["s_final"])
    # injections neutralized: dying idle's VA must not be inflated by the
    # rescue cash it received (baseline rose one-for-one)
    g = _run_policy(3, 5, "idle", REVIVAL)
    w = g.world
    cid = w.player_club_id
    inj = w.va_injections.get(cid, 0)
    assert w.death_count.get(cid, 0) >= 1
    assert inj > 0, "revival must have injected"
    r = g.final_result()
    va_with = r["channels"]["net_worth_va_m"]
    # recompute VA as if injections were free money (leak scenario)
    va_leak = va_with + inj / 10**8
    assert va_leak > va_with, "sanity"
    # the reported VA uses the neutralized baseline
    assert abs((r["channels"]["net_worth_baseline_m"])
               - (w.va_injections.get(cid, 0) / 10**8
                  + __baseline_raw(w, cid))) < 0.51


def __baseline_raw(world, cid):
    if world.va_baseline_state:
        return world.va_baseline_state.get(cid, 0) / 10**8
    from score.composite import _va_baselines
    return _va_baselines(world).get(cid, 0) / 10**8


# ---------------------------------------------------------------------------
# unified death types: administration also counts a death (no cheaper way
# to die) and pays exactly one injection
# ---------------------------------------------------------------------------

def test_administration_counts_death_single_injection():
    g = Game(seed=4, years=3, param_overrides=REVIVAL)
    g.start()
    w = g.world
    cid = w.player_club_id
    club = w.clubs[cid]
    club.cash = -10**12  # force insolvency countdown
    from engine.sim import economy
    e = w.params.economy
    for _ in range(e.admin_days + 1):
        economy._administration_check(w, club)
    assert club.in_administration
    assert w.death_count.get(cid) == 1
    assert w.bailout_count.get(cid) == 1, "exactly one injection"
    assert not w.settled


# ---------------------------------------------------------------------------
# bot bailout symmetry: a bot club entering administration draws the same
# schedule and its cash rises by the same first injection
# ---------------------------------------------------------------------------

def test_bot_bailout_symmetry():
    g = Game(seed=5, years=3, param_overrides=REVIVAL)
    g.start()
    w = g.world
    bot = next(c for c in w.sorted_club_ids() if c != w.player_club_id)
    club = w.clubs[bot]
    club.cash = -10**12
    from engine.core.params import M
    from engine.sim import economy
    e = w.params.economy
    before = club.cash
    for _ in range(e.admin_days + 1):
        economy._administration_check(w, club)
    assert club.in_administration
    sched = w.params.revival["injections_m"]
    assert club.cash == before + M(sched[0])
    assert w.bailout_count.get(bot) == 1
    assert w.death_count.get(bot) is None, "bots have no score-side deaths"


# ---------------------------------------------------------------------------
# full v0.3 stack determinism
# ---------------------------------------------------------------------------

def test_full_v03_stack_determinism():
    def once():
        g = _run_policy(6, 3, "idle", FULL)
        return state_hash(g.world), g.final_result()["s_final"]
    assert once() == once()
