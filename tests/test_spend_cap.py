"""Spend cap is cumulative across the whole run_benchmark invocation.

Regression for the 2026-07-03 incident: a 3-seed run authorized at $40
spent $69 because each seed got a fresh full cap. The cap must span all
seeds x repeats: trip during seed N => seed N settles via the abandon
path, seeds > N never start, and the summary carries explicit markers.
"""

from __future__ import annotations

from run_benchmark import run_benchmark
from runner.providers import LLMResponse, ToolCall, Usage


class AdvanceAdapter:
    """Always advances; every call costs $0.01 (10k input tokens at
    haiku pricing). Shared across seeds so we can count total calls."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, tools, max_tokens, temperature):
        self.calls += 1
        return LLMResponse(
            text=None,
            tool_calls=[ToolCall(id=f"t{self.calls}", name="advance",
                                 args={})],
            usage=Usage(input_tokens=10_000))


def test_cap_is_cumulative_across_seeds(tmp_path, monkeypatch):
    import run_benchmark as rb
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path)
    import runner.manifest as manifest
    monkeypatch.setattr(manifest, "RUNS_DIR", tmp_path / "runs",
                        raising=False)

    adapter = AdvanceAdapter()
    # a 2y game is ~25-30 stops => ~$0.25-0.30 per seed at $0.01/call.
    # Cap $0.40: seed 1 completes under cap, seed 2 trips mid-run,
    # seed 3 must never start.
    summary = run_benchmark(
        agent="claude", seeds=[1, 2, 3], years=2,
        model="claude-haiku-4-5-test", spend_cap_usd=0.40,
        club="mid", verbose=False, run_label="cap_test",
        adapter_factory=lambda model: adapter)

    assert summary["aborted_by_spend_cap"] is True
    ran_seeds = [r["seed"] for r in summary["per_seed"]]
    assert ran_seeds == [1, 2], f"seed 3 must be skipped, got {ran_seeds}"
    assert summary["seeds_skipped"] == [{"repeat": 0, "seed": 3}]

    seed1, seed2 = summary["per_seed"]
    assert not seed1.get("aborted_by_spend_cap")
    assert seed2["aborted_by_spend_cap"] is True
    assert seed2["settle_reason"] == "abandoned"

    # cumulative spend stopped within one call of the cap
    total = summary["cost_usd_total"]
    assert 0.40 <= total <= 0.42, total


def test_cap_none_never_trips(tmp_path, monkeypatch):
    import run_benchmark as rb
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path)
    import runner.manifest as manifest
    monkeypatch.setattr(manifest, "RUNS_DIR", tmp_path / "runs",
                        raising=False)

    adapter = AdvanceAdapter()
    summary = run_benchmark(
        agent="claude", seeds=[1], years=2,
        model="claude-haiku-4-5-test", spend_cap_usd=None,
        club="mid", verbose=False, run_label="cap_none_test",
        adapter_factory=lambda model: adapter)
    assert "aborted_by_spend_cap" not in summary
    assert summary["per_seed"][0]["settle_reason"] != "abandoned"
