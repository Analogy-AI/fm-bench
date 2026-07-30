"""Oracle (v2) information-ceiling invariants (real 5y sims, 555 world).

The privileged oracle is the benchmark's soft ceiling and leak alarm: its
score must sit strictly above the best legitimate scripted play on every
seed, it must be a pure deterministic function of world state (bit-identical
replays), and — since its privilege is information, not power — the engine
must never have to reject one of its actions.
"""

from __future__ import annotations

import pytest

from baselines import POLICIES
from engine.game import Game

SEEDS = (1, 2, 3)
YEARS = 5

# every state-mutating tool the scripted baselines use; an envelope error on
# any of these is an "illegal action" for the zero-rejection invariant
MUTATING = {"invest", "list_player", "make_transfer_offer", "offer_contract",
            "promote_youth", "release_player", "respond_to_offer",
            "set_lineup", "set_standing_order", "set_tactics"}


def _run(agent: str, seed: int) -> tuple[dict, list]:
    game = Game(seed=seed, years=YEARS, player_club="mid")
    rejected: list[tuple[str, str]] = []
    orig = game.call_tool

    def wrapped(name, args=None):
        env = orig(name, args)
        if not env.get("ok") and name in MUTATING:
            rejected.append((name, env.get("error", {}).get("code")))
        return env

    game.call_tool = wrapped
    packet = game.start()
    policy = POLICIES[agent]
    while not packet.get("game_over"):
        policy(game, packet)
        packet = game.advance()
    return game.final_result(), rejected


@pytest.fixture(scope="module")
def oracle_runs():
    return {seed: _run("oracle", seed) for seed in SEEDS}


def test_oracle_deterministic_replay(oracle_runs):
    """Same seed twice -> bit-identical run (no own RNG, no wall clock)."""
    first, _ = oracle_runs[SEEDS[0]]
    again, _ = _run("oracle", SEEDS[0])
    assert again["s_final"] == first["s_final"]
    assert again["event_chain_hash"] == first["event_chain_hash"]
    assert again["final_state_hash"] == first["final_state_hash"]


def test_oracle_zero_rejected_actions(oracle_runs):
    """Privilege is information, not power: every action must be legal."""
    for seed in SEEDS:
        _, rejected = oracle_runs[seed]
        assert rejected == [], (
            f"seed {seed}: engine rejected oracle actions {rejected[:5]}")


def test_oracle_survives_full_horizon(oracle_runs):
    """A truth-reading manager is never fired and never goes insolvent."""
    for seed in SEEDS:
        res, _ = oracle_runs[seed]
        assert res["settle_reason"] == "completed", (
            f"seed {seed}: oracle settled '{res['settle_reason']}'")


def test_oracle_strictly_above_heuristic(oracle_runs):
    """Ceiling property: oracle > heuristic on every seed, with real margin
    in aggregate (a leak that lets belief-space play reach truth-level scores
    must trip this alarm)."""
    gaps = []
    for seed in SEEDS:
        o, _ = oracle_runs[seed]
        h, _ = _run("heuristic", seed)
        assert o["s_final"] > h["s_final"], (
            f"seed {seed}: oracle {o['s_final']:.2f} <= "
            f"heuristic {h['s_final']:.2f} — ceiling broken")
        gaps.append(o["s_final"] - h["s_final"])
    assert max(gaps) > 10.0, f"oracle barely above heuristic: gaps {gaps}"
