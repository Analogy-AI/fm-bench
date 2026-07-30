"""Round-3: decision_log keeps the error code on failed tool calls
(the sonnet autopsy had to reverse-engineer blank error envelopes)."""

from __future__ import annotations

from engine.game import Game
from runner.llm_agent import LLMAgent
from runner.providers import LLMResponse, ToolCall, Usage


class OneBadCallAdapter:
    """First stop: issue an invalid action (missing required arg), then advance."""

    def __init__(self):
        self.bad_sent = False

    def complete(self, *, system, messages, tools, max_tokens,
                 temperature=0.0) -> LLMResponse:
        usage = Usage(input_tokens=10, output_tokens=5)
        if not self.bad_sent:
            self.bad_sent = True
            return LLMResponse(text=None, tool_calls=[
                ToolCall(id="bad1", name="release_player", args={})],
                usage=usage, raw=None)
        return LLMResponse(text=None, tool_calls=[
            ToolCall(id="adv", name="advance", args={})], usage=usage, raw=None)


def test_failed_action_logs_error_code():
    game = Game(seed=7, years=1, player_club="mid")
    agent = LLMAgent(model="claude-haiku-4-5-20251001", api_key="unused",
                     verbose=False, adapter=OneBadCallAdapter())
    agent.play(game)
    failed = [d for d in agent.decision_log if d["ok"] is False]
    assert failed, "the invalid call never reached the decision log"
    assert failed[0]["tool"] == "release_player"
    assert failed[0]["result"]["error_code"] == "missing_arg"
