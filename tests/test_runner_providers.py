"""Runner production-pass tests: adapter purity, retry policy, key pool,
cost tracking. No network anywhere."""

from __future__ import annotations

import json

import pytest

from runner.costs import CostTracker
from runner.keys import KeyPool, KeyRateLimiter
from runner.providers import (AnthropicAdapter, FatalProviderError,
                              LLMResponse, ProviderAdapter,
                              RetryableProviderError, RetryPolicy, ToolCall,
                              Usage)

TOOLS = [{"name": "get_squad", "input_schema": {"type": "object"}},
         {"name": "advance", "input_schema": {"type": "object"}}]
MSGS = [
    {"role": "user", "text": "DECISION STOP S0001"},
    {"role": "assistant", "text": None, "raw": None,
     "tool_calls": [{"id": "t1", "name": "get_squad", "args": {}}]},
    {"role": "tool_results", "results": [{"id": "t1", "content": "{}"}],
     "note": None},
]


def _bare_anthropic() -> AnthropicAdapter:
    a = AnthropicAdapter.__new__(AnthropicAdapter)  # no client, build only
    a.model = "claude-haiku-4-5-20251001"
    return a


class TestAnthropicRequestBuild:
    def test_cache_breakpoints(self):
        req = _bare_anthropic().build_request(
            system="SYS", messages=MSGS, tools=TOOLS, max_tokens=10,
            temperature=0)
        assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert req["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in req["tools"][0]
        # moving breakpoint: last block of the FINAL message only
        assert req["messages"][-1]["content"][-1]["cache_control"] == \
            {"type": "ephemeral"}
        for m in req["messages"][:-1]:
            for block in m["content"]:
                assert "cache_control" not in block

    def test_byte_stable_prefix(self):
        a = _bare_anthropic()
        r1 = a.build_request(system="SYS", messages=MSGS, tools=TOOLS,
                             max_tokens=10, temperature=0)
        r2 = a.build_request(system="SYS", messages=list(MSGS) + [
            {"role": "user", "text": "another turn"}], tools=TOOLS,
            max_tokens=10, temperature=0)
        # frozen prefix identical byte-for-byte across growing conversations
        assert json.dumps(r1["system"], sort_keys=True) == \
            json.dumps(r2["system"], sort_keys=True)
        assert json.dumps(r1["tools"], sort_keys=True) == \
            json.dumps(r2["tools"], sort_keys=True)

    def test_tool_result_roundtrip(self):
        req = _bare_anthropic().build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=10,
            temperature=0)
        tr = req["messages"][2]["content"][0]
        assert tr["type"] == "tool_result" and tr["tool_use_id"] == "t1"


class _ScriptedAdapter(ProviderAdapter):
    """Raises scripted errors then succeeds; no network."""

    provider = "scripted"

    def __init__(self, errors: list[Exception]):
        sleeps: list[float] = []
        policy = RetryPolicy(retries=4, backoff_base_s=0.1)
        policy.sleep = sleeps.append
        super().__init__("scripted-model", api_key=None, retry=policy)
        self.errors = list(errors)
        self.sleeps = sleeps
        self.calls = 0

    def _request(self, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "raw"

    def _parse(self, raw) -> LLMResponse:
        return LLMResponse(text="ok", tool_calls=[], usage=Usage(1, 1, 0, 0))


class TestRetryPolicy:
    def test_retries_then_succeeds(self):
        a = _ScriptedAdapter([RetryableProviderError("429"),
                              RetryableProviderError("500")])
        resp = a.complete(system="s", messages=[], tools=[], max_tokens=1)
        assert resp.text == "ok" and a.calls == 3 and len(a.sleeps) == 2

    def test_retry_after_header_honored(self):
        a = _ScriptedAdapter([RetryableProviderError("429",
                                                     retry_after_s=7.0)])
        a.complete(system="s", messages=[], tools=[], max_tokens=1)
        assert a.sleeps == [7.0]

    def test_fatal_not_retried(self):
        a = _ScriptedAdapter([FatalProviderError("401")])
        with pytest.raises(FatalProviderError):
            a.complete(system="s", messages=[], tools=[], max_tokens=1)
        assert a.calls == 1

    def test_exhaustion_raises_last(self):
        a = _ScriptedAdapter([RetryableProviderError(f"e{i}")
                              for i in range(9)])
        with pytest.raises(RetryableProviderError):
            a.complete(system="s", messages=[], tools=[], max_tokens=1)
        assert a.calls == 5  # 1 + 4 retries


class TestKeyPool:
    def test_round_robin(self):
        pool = KeyPool(["k1", "k2", "k3"])
        leased = [pool.lease()[0] for _ in range(7)]
        assert leased == ["k1", "k2", "k3", "k1", "k2", "k3", "k1"]

    def test_same_key_shares_limiter(self):
        pool = KeyPool(["k1", "k1", "k2"], rpm_per_key=10)
        _, l1a = pool.lease()
        _, l1b = pool.lease()
        _, l2 = pool.lease()
        assert l1a is l1b and l1a is not l2

    def test_rate_limiter_blocks_then_frees(self):
        clock = {"t": 0.0}
        slept: list[float] = []

        def fake_sleep(s):
            slept.append(s)
            clock["t"] += s

        lim = KeyRateLimiter(rpm=2, clock=lambda: clock["t"],
                             sleep=fake_sleep)
        lim.acquire()
        lim.acquire()
        lim.acquire()  # third within the minute must wait ~60s
        assert slept and sum(slept) >= 59.9


class TestCostTracker:
    def test_graceful_cap_and_hit_rate(self):
        c = CostTracker("claude-haiku-4-5-20251001", spend_cap_usd=0.001)
        assert not c.over_cap()
        c.add(Usage(input_tokens=2000, output_tokens=0,
                    cache_read_tokens=8000, cache_write_tokens=0))
        assert c.over_cap()
        assert c.cache_hit_rate() == pytest.approx(0.8)

    def test_restore(self):
        c1 = CostTracker("claude-haiku-4-5-20251001", None)
        c1.add(Usage(100, 50, 10, 5))
        c2 = CostTracker("claude-haiku-4-5-20251001", None)
        c2.restore(c1.summary())
        assert c2.usd() == pytest.approx(c1.usd())
        assert c2.calls == 1

    def test_unknown_model_flagged(self):
        c = CostTracker("mystery-model-9", None)
        assert c.price_estimated is True
        assert CostTracker("claude-opus-4-8", None).price_estimated is False


class TestLearnedStreamPathConversion:
    def test_forced_stream_429_converts_to_retryable(self, monkeypatch):
        """Regression: once _force_stream is learned (Qwen), the early return
        bypassed exception conversion — a Together 429 escaped as a raw
        openai.RateLimitError and crashed the run instead of backing off."""
        import httpx
        import openai
        from runner.providers import OpenAICompatAdapter

        a = OpenAICompatAdapter("Qwen/Qwen-test", api_key="k",
                                base_url="http://localhost:1")
        a._learned["_force_stream"] = True

        def boom(req):
            raise openai.RateLimitError(
                "429 too many requests",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "http://x")),
                body=None)

        monkeypatch.setattr(a, "_streamed_create", boom)
        with pytest.raises(RetryableProviderError):
            a._request(system="s", messages=[], tools=[],
                       max_tokens=5, temperature=0.0)
