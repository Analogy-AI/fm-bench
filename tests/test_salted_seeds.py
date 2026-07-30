"""Salted official seeds: anti-fingerprinting derivation + big-seed support."""

from __future__ import annotations

import runner.manifest as manifest_mod
from run_benchmark import derive_salted_seed, run_benchmark
from runner.providers import LLMResponse, ToolCall, Usage

CHEAP = {"world": {"divisions": 1, "clubs_per_division": 8, "rounds": 14}}


def test_derivation_deterministic_and_salt_sensitive():
    a = derive_salted_seed(1, "salt-A")
    assert a == derive_salted_seed(1, "salt-A")      # deterministic
    assert a != derive_salted_seed(2, "salt-A")      # nonce matters
    assert a != derive_salted_seed(1, "salt-B")      # salt matters
    assert 0 <= a < 2**63                            # engine-safe range


def test_engine_accepts_63bit_seeds():
    # the whole point: effective seeds are huge — the engine must not care
    from engine.game import Game
    big = derive_salted_seed(1, "test-salt")
    g = Game(seed=big, years=1, player_club="mid", param_overrides=CHEAP)
    p = g.start()
    while not p.get("game_over"):
        p = g.advance()
    assert p["result"]["s_final"] is not None


def test_salted_world_differs_from_public_seed_world():
    # nonce 3 salted must NOT be the world of public seed 3 (no fingerprint)
    from engine.domain.worldgen import generate_world
    from engine.core.params import apply_overrides, load_params
    params = apply_overrides(load_params(), CHEAP)
    w_pub = generate_world(3, params, None, 1)
    w_salt = generate_world(derive_salted_seed(3, "test-salt"), params, None, 1)
    names_pub = sorted(p.name for p in list(w_pub.players.values())[:20])
    names_salt = sorted(p.name for p in list(w_salt.players.values())[:20])
    assert names_pub != names_salt


class _AdvanceOnly:
    def complete(self, *, system, messages, tools, max_tokens, temperature=0.0):
        return LLMResponse(text=None,
                           usage=Usage(input_tokens=10, output_tokens=1),
                           tool_calls=[ToolCall(id="a", name="advance", args={})],
                           raw=None)


def test_salted_results_carry_nonces_not_effective_seeds(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "RUNS_DIR", tmp_path / "runs")
    import run_benchmark as rb
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setenv("FMBENCH_WORLD_SALT", "unit-test-salt")
    out = run_benchmark(agent="claude", model="claude-haiku-4-5-20251001",
                        years=1, seeds=[7], verbose=False, salted=True,
                        adapter_factory=lambda m: _AdvanceOnly(),
                        param_overrides=CHEAP, spend_cap_usd=None)
    assert out["salted"] is True
    assert out["seeds"] == [7]                       # nonce, not the HMAC value
    assert out["per_seed"][0]["seed"] == 7           # shareable file: nonce only
