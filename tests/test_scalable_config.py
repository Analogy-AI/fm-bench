"""Scalable run-config: operating modes, world size, notebook cap, market seam.

Every knob must default to current behavior (goldens cover byte-identity); here
we assert the NON-default paths actually work and stay deterministic.
"""

from engine.game import Game
from run_benchmark import build_overrides


def _headless(**overrides):
    g = Game(seed=7, years=3, param_overrides=overrides or None)
    return g.simulate_headless()["final_hash"]


def test_shrunk_worlds_run_and_are_deterministic():
    for size in ({"divisions": 1, "clubs_per_division": 8, "rounds": 14},
                 {"divisions": 2, "clubs_per_division": 8, "rounds": 14}):
        h1 = _headless(world=dict(size))
        h2 = _headless(world=dict(size))
        assert h1 == h2 and len(h1) == 32


def test_world_size_changes_the_world():
    assert _headless(world={"clubs_per_division": 8, "rounds": 14}) != _headless()


def test_hard_mode_advantages_opponents():
    # non-player clubs start richer -> the world evolves differently
    base = _headless()
    hard = _headless(world={"opponent_cash_mult": 1.8})
    assert base != hard


def test_notebook_cap_param():
    unlimited = Game(seed=1, years=1)
    unlimited.start()
    assert unlimited.call_tool("rewrite_notes", {"text": "x" * 50000})["ok"]
    capped = Game(seed=1, years=1,
                  param_overrides={"stops": {"notebook_max_chars": 1000}})
    capped.start()
    r = capped.call_tool("rewrite_notes", {"text": "x" * 50000})
    assert not r["ok"] and r["error"]["code"] == "notebook_full"


def test_build_overrides_defaults_empty():
    # unconfigured run => empty overrides => byte-identical default path
    assert build_overrides() == {}
    assert build_overrides(mode="hard")["world"]["opponent_cash_mult"] > 1.0
    ov = build_overrides(world_size="1x8", notebook_cap=500)
    assert ov["world"]["divisions"] == 1 and ov["world"]["clubs_per_division"] == 8
    assert ov["world"]["rounds"] == 14
    assert ov["stops"]["notebook_max_chars"] == 500


def test_market_strategy_default_is_scripted():
    g = Game(seed=1, years=1)
    assert getattr(g.world.params.market_ai, "strategy", "scripted") == "scripted"
