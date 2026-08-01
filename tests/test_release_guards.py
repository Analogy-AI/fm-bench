"""Guards against silent misconfiguration that still replays bit-identically.

The dangerous class of bug here is not a crash: it is a run that quietly
plays a DIFFERENT world than the caller asked for, produces a plausible
score, and passes replay verification -- because replay reproduces
whatever was actually run. External harnesses (Open Track, MCP) construct
the engine directly and never see argparse's `choices=`, so validation has
to live in the engine.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.domain.worldgen import STRATA_T1_RANK, generate_world
from engine.core.params import load_params
from engine.game import Game


def _params():
    return load_params(None)


@pytest.mark.parametrize("bad", ["C00001", "BOGUS", "", "Mid", "medium"])
def test_unknown_player_club_raises(bad):
    """A typo must not silently become the default 'mid' club."""
    with pytest.raises(ValueError, match="unknown player_club"):
        generate_world(1, _params(), bad, 5)
    with pytest.raises(ValueError, match="unknown player_club"):
        Game(seed=1, years=5, player_club=bad)


@pytest.mark.parametrize("strata", sorted(STRATA_T1_RANK))
def test_valid_strata_still_accepted(strata):
    g = Game(seed=1, years=5, player_club=strata)
    assert g.world.player_club_id is not None


def test_strata_select_distinct_clubs():
    """Regression: the three strata must not collapse onto one club --
    that is what the old silent fallback looked like from outside."""
    picked = {s: Game(seed=1, years=5, player_club=s).world.player_club_id
              for s in sorted(STRATA_T1_RANK)}
    assert len(set(picked.values())) == len(picked), picked


def test_all_ai_world_still_allows_none():
    """player_club=None is the headless all-AI world, not a bad value."""
    w = generate_world(1, _params(), None, 5)
    assert w.player_club_id is None


def test_unknown_model_id_is_flagged(capsys, monkeypatch):
    """An unrecognized model id routes to the OpenAI-compatible catch-all;
    that is intended, but it must be announced, not silent."""
    from runner.providers import adapter_name_for_model

    assert adapter_name_for_model("some-new-model") == "unknown"
    assert adapter_name_for_model("claude-haiku-4-5") != "unknown"
    assert adapter_name_for_model("gpt-5") != "unknown"

    import run_benchmark as rb
    monkeypatch.setattr("runner.keys.find_api_key", lambda provider: None)
    with pytest.raises(RuntimeError, match="no openai API key"):
        rb._run_one(agent="claude", seed=1, years=1, model="some-new-model",
                    spend_cap_usd=None, club="mid", verbose=False)
    out = capsys.readouterr().out
    assert "WARNING" in out and "some-new-model" in out


def test_known_model_id_is_not_flagged(capsys, monkeypatch):
    import run_benchmark as rb
    monkeypatch.setattr("runner.keys.find_api_key", lambda provider: None)
    with pytest.raises(RuntimeError, match="no anthropic API key"):
        rb._run_one(agent="claude", seed=1, years=1,
                    model="claude-haiku-4-5",
                    spend_cap_usd=None, club="mid", verbose=False)
    assert "WARNING" not in capsys.readouterr().out
