"""--parallel-seeds: identical results to sequential; cumulative shared cap."""

from __future__ import annotations

import runner.manifest as manifest_mod
from run_benchmark import run_benchmark
from runner.providers import LLMResponse, ToolCall, Usage

CHEAP = {"world": {"divisions": 1, "clubs_per_division": 8, "rounds": 14}}


class _AdvanceOnly:
    def complete(self, *, system, messages, tools, max_tokens, temperature=0.0):
        return LLMResponse(text=None,
                           usage=Usage(input_tokens=1000, output_tokens=100),
                           tool_calls=[ToolCall(id="a", name="advance", args={})],
                           raw=None)


def _bench(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(manifest_mod, "RUNS_DIR", tmp_path / "runs")
    import run_benchmark as rb
    monkeypatch.setattr(rb, "RESULTS_DIR", tmp_path / "results")
    return run_benchmark(agent="claude", model="claude-haiku-4-5-20251001",
                         years=1, verbose=False,
                         adapter_factory=lambda m: _AdvanceOnly(),
                         param_overrides=CHEAP, **kw)


def test_parallel_matches_sequential(tmp_path, monkeypatch):
    seq = _bench(tmp_path / "a", monkeypatch, seeds=[3, 5], spend_cap_usd=None)
    par = _bench(tmp_path / "b", monkeypatch, seeds=[3, 5], spend_cap_usd=None,
                 parallel_seeds=2)
    seq_scores = {r["seed"]: r["s_final"] for r in seq["per_seed"]}
    par_scores = {r["seed"]: r["s_final"] for r in par["per_seed"]}
    assert seq_scores == par_scores  # independent worlds: parallelism is pure


def test_parallel_shared_cap_is_cumulative(tmp_path, monkeypatch):
    # cap sized so the POOL trips partway: with ~$0.0016/call at haiku prices
    # and dozens of calls per 1y run, $0.02 lets neither seed finish cleanly
    out = _bench(tmp_path / "c", monkeypatch, seeds=[3, 5],
                 spend_cap_usd=0.02, parallel_seeds=2)
    assert out.get("aborted_by_spend_cap")  # shared pool tripped across threads
    total = out["cost_usd_total"]
    assert total < 0.06  # bounded near the cap, not 2x a per-seed cap
