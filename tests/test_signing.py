"""Open Track HMAC signing + replay verification (runner.signing)."""

from __future__ import annotations

import json

import pytest

import runner.manifest as manifest_mod
from engine.game import Game
from runner.llm_agent import LLMAgent
from runner.manifest import RunRecorder, new_run_id
from runner.providers import LLMResponse, ToolCall, Usage
from runner.signing import (canonical_payload, official_deviations, sign_run,
                            verify_integrity, verify_replay, verify_submission)

SECRET = "test-open-track-secret"
CHEAP_WORLD = {"world": {"divisions": 1, "clubs_per_division": 8, "rounds": 14}}


class _AdvanceOnly:
    """Deterministic pseudo-LLM: immediately advances every stop (no actions)."""

    def complete(self, *, system, messages, tools, max_tokens, temperature=0.0):
        return LLMResponse(text=None, usage=Usage(input_tokens=5, output_tokens=1),
                           tool_calls=[ToolCall(id="adv", name="advance", args={})],
                           raw=None)


@pytest.fixture
def recorded_run(tmp_path, monkeypatch):
    """Play a short game through LLMAgent+RunRecorder into a temp runs dir;
    return the run directory path."""
    monkeypatch.setattr(manifest_mod, "RUNS_DIR", tmp_path)
    run_id = new_run_id("claude", 3, "mock")
    game = Game(seed=3, years=1, player_club="mid", param_overrides=CHEAP_WORLD)
    # param_overrides is baked into the config so rebuild_game replays the SAME
    # (1x8) world instead of the 1x16 engine default.
    rec = RunRecorder(run_id, {"agent": "claude", "club": "mid", "model": "mock",
                               "seed": 3, "years": 1,
                               "param_overrides": CHEAP_WORLD})
    llm = LLMAgent(model="mock", api_key="k", adapter=_AdvanceOnly(),
                   recorder=rec, verbose=False)
    llm.play(game)
    return tmp_path / run_id


def test_sign_and_verify_integrity_roundtrip(recorded_run):
    sig = sign_run(recorded_run, SECRET)
    assert len(sig["digest"]) == 64
    assert verify_integrity(recorded_run, SECRET)["ok"]


def test_tamper_actions_breaks_integrity(recorded_run):
    sign_run(recorded_run, SECRET)
    ap = recorded_run / "actions.jsonl"
    ap.write_text(ap.read_text() + json.dumps({"type": "action", "tool": "x",
                  "args": {}, "stop_id": "S9999"}) + "\n")
    res = verify_integrity(recorded_run, SECRET)
    assert not res["ok"] and "payload mismatch" in res["reason"]


def test_wrong_secret_breaks_integrity(recorded_run):
    sign_run(recorded_run, SECRET)
    res = verify_integrity(recorded_run, "not-the-secret")
    assert not res["ok"] and "HMAC" in res["reason"]


def test_no_signature(recorded_run):
    assert not verify_integrity(recorded_run, SECRET)["ok"]


def test_canonical_payload_binds_score_and_log(recorded_run):
    p = canonical_payload(recorded_run)
    assert "actions_sha256" in p and "s_final" in p and p["algo"] == "HMAC-SHA256"


def test_official_gate_flags_nonofficial_run(recorded_run):
    # the fixture plays 1y on an overridden 1x8 world -> both must be flagged;
    # integrity+replay alone would still pass (that's the gap this gate closes)
    off = official_deviations(recorded_run)
    assert not off["ok"]
    assert any("years" in d for d in off["deviations"])
    assert any("param_overrides" in d for d in off["deviations"])
    sign_run(recorded_run, SECRET)
    assert verify_submission(recorded_run, SECRET)["ok"]          # non-official OK
    assert not verify_submission(recorded_run, SECRET, official=True)["ok"]


def test_replay_reproduces_claimed_score(recorded_run):
    res = verify_replay(recorded_run)
    assert res["ok"], res
    assert abs(float(res["replayed"]) - float(res["claimed"])) < 1e-6


def test_replay_rejects_edited_action_log(recorded_run):
    # flip a stop_end into a bogus action -> replay diverges / fails
    ap = recorded_run / "actions.jsonl"
    lines = [json.loads(x) for x in ap.read_text().splitlines() if x.strip()]
    lines.insert(0, {"type": "action", "tool": "release_player",
                     "args": {"player_id": "P_FAKE"}, "stop_id": "S0001"})
    ap.write_text("\n".join(json.dumps(x, sort_keys=True) for x in lines) + "\n")
    assert not verify_replay(recorded_run)["ok"]


def test_verify_submission_requires_both(recorded_run):
    sign_run(recorded_run, SECRET)
    assert verify_submission(recorded_run, SECRET)["ok"]
    # integrity ok but replay tampered -> overall fail
    ap = recorded_run / "actions.jsonl"
    ap.write_text(ap.read_text().replace('"advance"', '"advance"', 1))
    # (integrity still keyed to signed payload; re-sign to isolate replay gate)
    sign_run(recorded_run, SECRET)
    ap.write_text(ap.read_text() + json.dumps({"type": "action",
                  "tool": "release_player", "args": {"player_id": "P_FAKE"},
                  "stop_id": "S0001"}) + "\n")
    out = verify_submission(recorded_run, SECRET)
    assert not out["ok"]  # integrity now fails (log edited after signing)
