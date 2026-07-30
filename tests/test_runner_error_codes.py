"""Per-error-code observability (autopsy rec 3): the runner must persist
error-code counts per stop in stops.jsonl and cumulatively in diagnostics."""

from __future__ import annotations

import json

import pytest

from engine.game import Game
from runner import manifest as manifest_mod
from runner.llm_agent import LLMAgent
from runner.manifest import RunRecorder
from runner.providers import LLMResponse, ToolCall, Usage


class ErroringAdapter:
    """First stop: one unknown tool + one over-budget-free bad arg; then advance."""

    def __init__(self):
        self.first = True

    def complete(self, *, system, messages, tools, max_tokens,
                 temperature=0.0) -> LLMResponse:
        usage = Usage(input_tokens=10, output_tokens=5)
        if self.first and len(messages) == 1:
            self.first = False
            return LLMResponse(text=None, tool_calls=[
                ToolCall(id="e1", name="get_player",
                         args={"player_id": "NOPE"}),
                ToolCall(id="e2", name="get_player",
                         args={"player_id": "NOPE"}),
            ], usage=usage, raw=None)
        return LLMResponse(text=None, tool_calls=[
            ToolCall(id="a", name="advance", args={})], usage=usage, raw=None)


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def test_error_codes_persisted(runs_dir):
    rec = RunRecorder("errcodes", {"agent": "claude", "club": "mid",
                                   "model": "claude-haiku-4-5-20251001",
                                   "seed": 3, "spend_cap_usd": None,
                                   "years": 2})
    agent = LLMAgent(model="claude-haiku-4-5-20251001", api_key="unused",
                     verbose=False, adapter=ErroringAdapter(), recorder=rec)
    result = agent.play(Game(seed=3, years=2, player_club="mid"))
    codes = result["diagnostics"]["error_codes"]
    assert sum(codes.values()) == result["diagnostics"]["tool_errors"] == 2
    stops = [json.loads(l) for l in
             (runs_dir / "errcodes" / "stops.jsonl").read_text().splitlines()]
    assert sum(codes.values()) == sum(
        sum(s["error_codes"].values()) for s in stops)
    first = stops[0]["error_codes"]
    assert sum(first.values()) == 2 and all(
        isinstance(k, str) for k in first)
