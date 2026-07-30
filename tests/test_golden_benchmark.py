"""Benchmark Mode byte-identity guard for the League Mode refactor.

Hashes in golden_benchmark_hashes.json were captured on the pre-league
engine (commit a0af6b0). Benchmark Mode (single player club + scripted
opponents) must stay bit-identical while League Mode is added — any drift
here means official scores are no longer comparable across engine versions.

Re-captures (each a deliberate, documented calibration change):
- League plumbing: headless free-agent inbox quirk fix (see league commits).
- Calibration round 2 (autopsy recs): board recovery channels (trend relief,
  year-1 ramp, upper-half floor), opening board briefing inbox item,
  board_warning event-log entries, and the Club.prev_wage_ratio slot all
  legitimately move played-mode and serialized-state hashes. Scores are not
  comparable across this boundary; earlier results/*.json are historical.
- Calibration round 4 (score_v0.2): net-worth VALUE-ADDED scoring. Only the
  golden s_final values were re-captured; state and event-chain hashes were
  verified bit-identical before recapture (score layer only, world evolution
  untouched). v0.1 and v0.2 scores are not comparable.
- Calibration round 5 (v0.3, ENGINE layer — full recapture): fatigue
  activated (recovery 12->3.9/day; fitness-aware auto-selection), board
  strength floor vs roster-shrink (Club.strength_floor slot), transfer
  market reopened (ask/accept/counter retune, list noise 0.15), free-agent
  signing fee + AI walk-down signing. World evolution deliberately changed
  everywhere; all four goldens re-captured. Pre-v0.3 results are historical.
"""

from __future__ import annotations

import json
import pathlib

from engine.game import Game

GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "golden_benchmark_hashes.json").read_text())


def _idle_run(seed: int, overrides: dict | None = None) -> dict:
    game = Game(seed=seed, years=2, player_club="mid",
                param_overrides=overrides)
    packet = game.start()
    while not packet.get("game_over"):
        packet = game.advance()
    return packet["result"]


def test_headless_hashes_unchanged():
    for seed in (3, 11):
        g = Game(seed=seed, years=2, player_club=None)
        assert g.simulate_headless()["final_hash"] == \
            GOLDEN[f"headless_2y_seed{seed}"], f"seed {seed} headless drifted"


def test_idle_played_legacy_world_unchanged():
    # engine-core invariance: the pre-555 world (tier_mix none) must still
    # reproduce the original goldens byte-for-byte
    for seed in (3, 11):
        res = _idle_run(seed, {"opponents": {"tier_mix": "none"}})
        want = GOLDEN[f"idle_2y_seed{seed}"]
        assert res["final_state_hash"] == want["final_state_hash"]
        assert res["event_chain_hash"] == want["event_chain_hash"]
        assert res["s_final"] == want["s_final"]
        assert res["stops_total"] == want["stops_total"]
        assert res["settle_reason"] == want["settle_reason"]


def test_idle_played_official_555_world_pinned():
    # the official (default) world since 2026-07-14 is 5/5/5 — pin it too
    for seed in (3, 11):
        res = _idle_run(seed)
        want = GOLDEN[f"idle_2y_seed{seed}_555"]
        assert res["final_state_hash"] == want["final_state_hash"]
        assert res["event_chain_hash"] == want["event_chain_hash"]
        assert res["s_final"] == want["s_final"]
