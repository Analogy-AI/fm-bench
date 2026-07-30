"""5/5/5 opponent competence tiers (params.opponents, capability track).

Contract under test:
- default OFF: tier_mix "none" merges/touches nothing — worlds and played
  runs stay byte-identical to the current engine (golden hashes are the
  cross-version anchor; here we pin default == explicit-"none").
- fixed-in-params assignment: non-player clubs ranked by initial cash get
  tier_order cyclically — a pure function of params + worldgen, no RNG, so
  every tested model sits the same exam on a given seed.
- interleaving: every wealth quintile of the 15 rivals contains easy AND
  medium AND hard (wealth x competence decorrelated).
- the player's own club is never tiered.
- flag ON is deterministic: same seed + flag twice -> identical final hash.
"""

from __future__ import annotations

from collections import Counter

import pytest

from engine.core.params import load_params
from engine.game import Game
from engine.persist.snapshot import state_hash
from engine.sim import scouting
from run_benchmark import build_overrides

ON = {"opponents": {"tier_mix": "555"}}


def _idle_run_hash(seed: int, overrides: dict | None = None) -> str:
    game = Game(seed=seed, years=1, player_club="mid",
                param_overrides=overrides)
    packet = game.start()
    while not packet.get("game_over"):
        packet = game.advance()
    return state_hash(game.world)


def _world(seed: int = 5, overrides: dict | None = None):
    return Game(seed=seed, years=1, player_club="mid",
                param_overrides=overrides).world


# -- default-off byte identity ------------------------------------------------

def test_default_is_555_official_world():
    # 2026-07-14 owner decision: the official exam IS the 5/5/5 world.
    # Default must equal explicit "555" and differ from the legacy world.
    assert _idle_run_hash(3, None) == \
        _idle_run_hash(3, {"opponents": {"tier_mix": "555"}})
    assert _idle_run_hash(3, None) != \
        _idle_run_hash(3, {"opponents": {"tier_mix": "none"}})


def test_off_worlds_carry_no_tiers():
    w = _world(5, {"opponents": {"tier_mix": "none"}})
    assert w.opponent_tiers == {} and w._opponent_tier_cfg == {}


# -- fixed interleaved assignment ----------------------------------------------

def test_assignment_is_5_5_5_and_wealth_interleaved():
    w = _world(5, ON)
    tiers = w.opponent_tiers
    assert len(tiers) == 15
    assert Counter(tiers.values()) == {"easy": 5, "medium": 5, "hard": 5}
    # wealth quintiles (initial cash desc) must each mix all three tiers
    rivals = sorted((c for c in w.clubs.values()
                     if c.cid != w.player_club_id),
                    key=lambda c: (-c.cash, c.cid))
    for q in range(3):
        quintile = {tiers[c.cid] for c in rivals[q * 5:(q + 1) * 5]}
        assert quintile == {"easy", "medium", "hard"}, f"quintile {q + 1}"


def test_assignment_is_the_params_tier_order_by_wealth_rank():
    # fixed in params: rank i (cash desc, cid tie-break) gets tier_order[i]
    w = _world(5, ON)
    order = load_params().opponents.tier_order
    rivals = sorted((c for c in w.clubs.values()
                     if c.cid != w.player_club_id),
                    key=lambda c: (-c.cash, c.cid))
    assert [w.opponent_tiers[c.cid] for c in rivals] == \
        [order[i % len(order)] for i in range(len(rivals))]


def test_assignment_identical_across_regenerations():
    # same seed + same params -> same exam, every time
    assert _world(5, ON).opponent_tiers == _world(5, ON).opponent_tiers


def test_medium_tier_levers_are_neutral():
    # medium must equal today's untiered AI: every lever at its no-op value
    med = load_params().opponents.tiers.medium
    assert med.scout_sigma_mult == 1.0
    assert med.fitness_aware_lineup is True
    assert med.market_day_stride == 1
    assert med.buy_need_gap_mult == 1.0
    assert med.wage_target_mult == 1.0


# -- player club is never tiered -----------------------------------------------

@pytest.mark.parametrize("strata", ["big", "mid", "small"])
def test_player_club_untiered(strata):
    w = Game(seed=5, years=1, player_club=strata, param_overrides=ON).world
    assert w.player_club_id not in w.opponent_tiers
    assert w.player_club_id not in w._opponent_tier_cfg
    assert len(w.opponent_tiers) == 15


def test_player_club_beliefs_unaffected_at_day_zero():
    # the tier multiplier scales VIEWER sigma; the player club is never a
    # tiered viewer, so its day-0 scouting reads match the default world
    base, tiered = _world(5, None), _world(5, ON)
    assert base.player_club_id == tiered.player_club_id
    cid = base.player_club_id
    for pid in sorted(base.players)[:80]:
        assert scouting.perceived_ca(base, cid, base.players[pid]) == \
            scouting.perceived_ca(tiered, cid, tiered.players[pid])


def test_headless_world_is_inert():
    # capability-track feature: with no player club the flag assigns nothing
    g1 = Game(seed=7, years=1, player_club=None, param_overrides=ON)
    assert g1.world.opponent_tiers == {}
    g2 = Game(seed=7, years=1, player_club=None)
    assert g1.simulate_headless()["final_hash"] == \
        g2.simulate_headless()["final_hash"]


# -- flag-on determinism ---------------------------------------------------------

def test_flag_on_deterministic_and_actually_different():
    h1 = _idle_run_hash(3, ON)
    h2 = _idle_run_hash(3, ON)
    assert h1 == h2
    assert h1 != _idle_run_hash(3, {"opponents": {"tier_mix": "none"}})


# -- config plumbing -------------------------------------------------------------

def test_build_overrides_tier_mix():
    assert "opponents" not in build_overrides()            # default merges nothing
    assert "opponents" not in build_overrides(tier_mix="none")
    assert build_overrides(tier_mix="555")["opponents"]["tier_mix"] == "555"


def test_unknown_tier_mix_rejected():
    with pytest.raises(ValueError, match="tier_mix"):
        Game(seed=1, years=1, player_club="mid",
             param_overrides={"opponents": {"tier_mix": "bogus"}})
