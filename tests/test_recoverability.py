"""Recoverability knobs (opt-in): adaptive targets + confidence mean-reversion.

Both default OFF and must leave the default world byte-identical; when ON they
must have the designed effect (self-correcting targets, recoverable confidence).
"""

from engine.game import Game
from engine.sim import board as board_mod


def _idle_hash(overrides):
    g = Game(seed=3, years=2, player_club="mid", param_overrides=overrides)
    p = g.start()
    while not p.get("game_over"):
        p = g.advance()
    return p["result"]["final_state_hash"]


def test_defaults_off_byte_identical():
    base = _idle_hash(None)
    assert base == _idle_hash({"board": {"adaptive_target_weight": 1.0,
                                         "confidence_drift_rate": 0}})


def test_adaptive_target_softens_after_underperformance():
    # idle play underperforms; by year 2 the adapted target must sit at or
    # below expectation (never harder without cause), be capped by the X5
    # relax guard, and differ from the pure-rank world somewhere.
    # target_cushion pinned to 0: these tests isolate the ADAPTIVE mechanism
    # (the calibrated default cushion stacks on top of the relax cap)
    # tier_mix none: this isolates the ADAPTIVE mechanism; the official 555
    # exam changes idle's rank trajectory on this seed and is irrelevant here
    g = Game(seed=3, years=2, player_club="mid",
             param_overrides={"board": {"adaptive_target_weight": 0.5,
                                        "target_cushion": 0},
                              "opponents": {"tier_mix": "none"}})
    p = g.start()
    seen_relaxed = False
    while not p.get("game_over"):
        p = g.advance()
        if not p.get("game_over"):
            club = g.world.clubs[g.world.player_club_id]
            if g.world.date.year >= 2:
                assert club.season_target <= club.expectation_rank + 3  # X5 cap
                if club.season_target > club.expectation_rank:
                    seen_relaxed = True
    assert seen_relaxed, "adaptive target never relaxed despite underperformance"


def test_adaptive_relax_capped_by_guard():
    # even blending 100% toward results (w=0), relax is clamped to max_relax
    g = Game(seed=3, years=3, player_club="mid",
             param_overrides={"board": {"adaptive_target_weight": 0.0,
                                        "adaptive_target_max_relax": 1,
                                        "target_cushion": 0}})
    p = g.start()
    while not p.get("game_over"):
        p = g.advance()
        if not p.get("game_over") and g.world.date.year >= 2:
            club = g.world.clubs[g.world.player_club_id]
            assert club.season_target <= club.expectation_rank + 1


def test_confidence_drift_pulls_toward_neutral():
    g = Game(seed=3, years=1, player_club="mid",
             param_overrides={"board": {"confidence_drift_rate": 2}})
    w = g.world
    club = w.clubs[w.player_club_id]
    club.board_confidence = 10.0
    before = club.board_confidence
    board_mod.monthly(w)  # drift applies at the top of the monthly review
    assert club.board_confidence > before
    # symmetric: euphoria fades too
    club.board_confidence = 90.0
    board_mod.monthly(w)
    assert club.board_confidence < 90.0


def test_drift_changes_world_when_on():
    assert _idle_hash({"board": {"confidence_drift_rate": 2}}) != _idle_hash(None)
