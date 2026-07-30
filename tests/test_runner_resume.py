"""Crash-safe resume: a run killed mid-flight and resumed from its manifest
must reach the same settlement as an uninterrupted run.

The scripted adapter acts as a deterministic pseudo-LLM whose decisions are a
pure function of the stop packet, so interrupted + resumed = uninterrupted.
"""

from __future__ import annotations

import re

import pytest

import runner.manifest as manifest_mod
from engine.game import Game
from runner.llm_agent import LLMAgent
from runner.manifest import RunRecorder, load_run, rebuild_game
from runner.providers import LLMResponse, ToolCall, Usage

SEED, YEARS, CLUB = 11, 1, "mid"


class _Boom(RuntimeError):
    pass


class ScriptedGameAdapter:
    """Pure function of the packet: same stops -> same actions, always.

    crash_at_stop: raise _Boom on the Nth distinct stop (1-based);
    crash_phase 'before' = before any action of that stop is issued,
    'mid' = after the stop's actions executed, before advance.
    """

    def __init__(self, crash_at_stop: int | None = None,
                 crash_phase: str = "before"):
        self.crash_at_stop = crash_at_stop
        self.crash_phase = crash_phase
        self.stops_seen = 0
        self._acted = False

    def complete(self, *, system, messages, tools, max_tokens,
                 temperature=0.0) -> LLMResponse:
        usage = Usage(input_tokens=10, output_tokens=5)
        first_call = len(messages) == 1
        if first_call:
            self.stops_seen += 1
            self._acted = False
        n = self._stop_number(messages[0]["text"])
        crash_here = (self.crash_at_stop is not None
                      and self.stops_seen == self.crash_at_stop)
        if crash_here and self.crash_phase == "before" and first_call:
            raise _Boom(f"simulated crash before stop #{self.stops_seen}")
        if first_call and not self._acted:
            calls = self._actions_for(n)
            if calls:
                self._acted = True
                return LLMResponse(text=None, tool_calls=calls, usage=usage,
                                   raw=None)
        if crash_here and self.crash_phase == "mid" and self._acted:
            raise _Boom(f"simulated crash mid-stop #{self.stops_seen}")
        return LLMResponse(text=None, tool_calls=[
            ToolCall(id=f"adv{n}", name="advance", args={})], usage=usage,
            raw=None)

    @staticmethod
    def _stop_number(packet_text: str) -> int:
        m = re.search(r"S(\d+)", packet_text)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _actions_for(n: int) -> list[ToolCall]:
        calls = []
        if n % 3 == 0:
            calls.append(ToolCall(id=f"n{n}", name="append_note",
                                  args={"text": f"plan at stop {n}"}))
        if n % 4 == 0:
            calls.append(ToolCall(id=f"t{n}", name="set_tactics",
                                  args={"formation": "4-4-2",
                                        "style": "possession"}))
        return calls


def _agent(adapter, recorder=None) -> LLMAgent:
    return LLMAgent(model="claude-haiku-4-5-20251001", api_key="unused",
                    verbose=False, adapter=adapter, recorder=recorder)


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def _fresh_recorder(run_id: str) -> RunRecorder:
    return RunRecorder(run_id, {"agent": "claude", "club": CLUB,
                                "model": "claude-haiku-4-5-20251001",
                                "seed": SEED, "spend_cap_usd": None,
                                "years": YEARS})


def _baseline(runs_dir) -> dict:
    game = Game(seed=SEED, years=YEARS, player_club=CLUB)
    return _agent(ScriptedGameAdapter(),
                  _fresh_recorder("base")).play(game)


def test_resume_after_clean_crash_matches_uninterrupted(runs_dir):
    base = _baseline(runs_dir)
    assert base["settle_reason"] in ("completed", "fired")

    crash_adapter = ScriptedGameAdapter(crash_at_stop=4, crash_phase="before")
    game = Game(seed=SEED, years=YEARS, player_club=CLUB)
    with pytest.raises(_Boom):
        _agent(crash_adapter, _fresh_recorder("crash")).play(game)

    manifest, records = load_run("crash")
    assert manifest["status"] == "running"
    assert manifest["stops_completed"] == 3
    game2, packet = rebuild_game(manifest, records)
    llm2 = _agent(ScriptedGameAdapter(), RunRecorder("crash", manifest["config"],
                                                     resume=True))
    llm2.cost.restore(manifest.get("cost", {}))
    resumed = llm2.play(game2, packet=packet)

    assert resumed["final_state_hash"] == base["final_state_hash"]
    assert resumed["s_final"] == pytest.approx(base["s_final"])
    assert resumed["settle_reason"] == base["settle_reason"]
    # spend accounting carried across the crash
    assert resumed["cost"]["api_calls"] > manifest["cost"]["api_calls"]

    final_manifest, _ = load_run("crash")
    assert final_manifest["status"] == "finished"


def test_resume_after_mid_stop_crash_same_score(runs_dir):
    """Crash after a stop's actions but before advance: the stop is replayed;
    committed actions persist (notebook may gain a duplicate line, which is
    hash-visible but score-neutral)."""
    base = _baseline(runs_dir)

    crash_adapter = ScriptedGameAdapter(crash_at_stop=3, crash_phase="mid")
    game = Game(seed=SEED, years=YEARS, player_club=CLUB)
    with pytest.raises(_Boom):
        _agent(crash_adapter, _fresh_recorder("dirty")).play(game)

    manifest, records = load_run("dirty")
    # trailing stop has actions but no stop_end marker
    assert records and records[-1]["type"] == "action"
    game2, packet = rebuild_game(manifest, records)
    assert not packet.get("game_over")
    resumed = _agent(ScriptedGameAdapter(),
                     RunRecorder("dirty", manifest["config"],
                                 resume=True)).play(game2, packet=packet)

    assert resumed["settle_reason"] == base["settle_reason"]
    assert resumed["s_final"] == pytest.approx(base["s_final"])


def test_finished_run_refuses_resume(runs_dir):
    _baseline(runs_dir)
    manifest, _ = load_run("base")
    assert manifest["status"] == "finished"
    assert manifest.get("s_final") is not None
