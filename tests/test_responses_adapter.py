"""OpenAI Responses adapter tests: request building, reasoning-item echo,
parsing, error conversion, routing. No network — the SDK client is faked."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from runner.providers import (FatalProviderError, OpenAICompatAdapter,
                              OpenAIResponsesAdapter, RetryableProviderError,
                              adapter_name_for_model, make_adapter)

TOOLS = [{"name": "get_squad", "description": "squad view",
          "input_schema": {"type": "object", "properties": {}}},
         {"name": "advance", "input_schema": {"type": "object"}}]

# raw output items as a previous Responses call would have produced them
# (reasoning item with encrypted_content + the function_call it authorized)
RAW_ITEMS = [
    {"type": "reasoning", "id": "rs_1", "summary": [],
     "encrypted_content": "gAAAAB-opaque"},
    {"type": "function_call", "id": "fc_1", "call_id": "call_1",
     "name": "get_squad", "arguments": "{}", "status": "completed"},
]

MSGS = [
    {"role": "user", "text": "DECISION STOP S0001"},
    {"role": "assistant", "text": None, "raw": RAW_ITEMS,
     "tool_calls": [{"id": "call_1", "name": "get_squad", "args": {}}]},
    {"role": "tool_results", "results": [{"id": "call_1", "content": "{}"}],
     "note": "converge now"},
]


def _bare(model="gpt-5.6-sol") -> OpenAIResponsesAdapter:
    a = OpenAIResponsesAdapter.__new__(OpenAIResponsesAdapter)  # build only
    a.model = model
    return a


class TestRequestBuild:
    def test_tools_flattened(self):
        req = _bare().build_request(system="SYS", messages=MSGS, tools=TOOLS,
                                    max_tokens=100, temperature=0)
        for t in req["tools"]:
            assert t["type"] == "function"
            assert "function" not in t          # no chat.completions nesting
            assert "name" in t and "parameters" in t
        assert req["tools"][0]["name"] == "get_squad"
        assert req["tools"][0]["parameters"] == TOOLS[0]["input_schema"]

    def test_temperature_omitted_for_reasoning_families(self):
        for model in ("gpt-5.6-sol", "gpt-5.6-terra", "o4-mini", "gpt-5"):
            req = _bare(model).build_request(
                system="S", messages=[], tools=[], max_tokens=10,
                temperature=0)
            assert "temperature" not in req
        # non-reasoning model routed here manually keeps its temperature
        req = _bare("gpt-4o").build_request(
            system="S", messages=[], tools=[], max_tokens=10, temperature=0.3)
        assert req["temperature"] == 0.3

    def test_reasoning_and_stateless_replay_params(self):
        req = _bare().build_request(system="S", messages=[], tools=[],
                                    max_tokens=10, temperature=0)
        assert req["reasoning"]["effort"] in ("minimal", "low", "medium",
                                              "high")
        assert req["store"] is False
        assert req["include"] == ["reasoning.encrypted_content"]
        assert req["instructions"] == "S"
        assert req["max_output_tokens"] == 10

    def test_raw_items_replayed_verbatim(self):
        req = _bare().build_request(system="S", messages=MSGS, tools=TOOLS,
                                    max_tokens=10, temperature=0)
        items = req["input"]
        assert items[0] == {"content": "DECISION STOP S0001", "role": "user"}
        # the assistant turn is its raw output items, byte-for-byte
        assert items[1] == RAW_ITEMS[0] and items[2] == RAW_ITEMS[1]
        # tool result round-trips on call_id; note becomes a user message
        assert items[3] == {"call_id": "call_1", "output": "{}",
                            "type": "function_call_output"}
        assert items[4] == {"content": "converge now", "role": "user"}

    def test_assistant_without_raw_rebuilt_from_neutral_fields(self):
        msgs = [{"role": "assistant", "text": "thinking aloud", "raw": None,
                 "tool_calls": [{"id": "c9", "name": "advance",
                                 "args": {"k": 1}}]}]
        req = _bare().build_request(system="S", messages=msgs, tools=[],
                                    max_tokens=10, temperature=0)
        assert req["input"][0] == {"content": "thinking aloud",
                                   "role": "assistant"}
        fc = req["input"][1]
        assert fc["type"] == "function_call" and fc["call_id"] == "c9"
        assert json.loads(fc["arguments"]) == {"k": 1}


def _fake_response(*, status="completed", reasoning_tokens=64, cached=200):
    output = [
        NS(type="reasoning", id="rs_9", summary=[],
           encrypted_content="enc-blob"),
        NS(type="message", id="msg_9", role="assistant", status="completed",
           content=[NS(type="output_text", text="checking squad",
                       annotations=[])]),
        NS(type="function_call", id="fc_9", call_id="call_9",
           name="get_squad", arguments='{"view": "full"}',
           status="completed"),
    ]
    usage = NS(input_tokens=1000, output_tokens=150,
               input_tokens_details=NS(cached_tokens=cached),
               output_tokens_details=NS(reasoning_tokens=reasoning_tokens))
    return NS(status=status, output=output, usage=usage,
              incomplete_details=None)


class TestParse:
    def test_function_call_items_to_toolcalls(self):
        resp = _bare()._parse(_fake_response())
        assert resp.text == "checking squad"
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert (tc.id, tc.name, tc.args) == ("call_9", "get_squad",
                                             {"view": "full"})

    def test_usage_mapping_including_cached(self):
        u = _bare()._parse(_fake_response(cached=200)).usage
        assert u.input_tokens == 800          # prompt minus cached
        assert u.cache_read_tokens == 200
        assert u.output_tokens == 150         # includes reasoning tokens
        assert u.cache_write_tokens == 0      # OpenAI doesn't bill writes

    def test_raw_preserves_reasoning_for_echo(self):
        resp = _bare()._parse(_fake_response())
        reasoning = [i for i in resp.raw if i.get("type") == "reasoning"]
        assert reasoning and reasoning[0]["encrypted_content"] == "enc-blob"
        # and the echo path replays them untouched
        req = _bare().build_request(
            system="S", messages=[{"role": "assistant", "raw": resp.raw}],
            tools=[], max_tokens=10, temperature=0)
        assert req["input"][0] == reasoning[0]

    def test_malformed_arguments_dont_crash(self):
        r = _fake_response()
        r.output[2].arguments = "{not json"
        resp = _bare()._parse(r)
        assert resp.tool_calls[0].args == {}


def _adapter_with_fake_create(create):
    a = OpenAIResponsesAdapter("gpt-5.6-sol", api_key="k")
    a._client = NS(responses=NS(create=create))
    return a


def _http_err(cls, msg, status):
    import httpx
    return cls(msg, response=httpx.Response(
        status, request=httpx.Request("POST", "http://x")), body=None)


class TestErrorConversion:
    def test_429_converts_to_retryable(self):
        import openai

        def boom(**req):
            raise _http_err(openai.RateLimitError, "429 slow down", 429)

        a = _adapter_with_fake_create(boom)
        with pytest.raises(RetryableProviderError):
            a._request(system="s", messages=[], tools=[], max_tokens=5,
                       temperature=0.0)

    def test_quota_exhaustion_is_fatal(self):
        import openai

        def boom(**req):
            raise _http_err(openai.RateLimitError,
                            "insufficient_quota: out of credit", 429)

        a = _adapter_with_fake_create(boom)
        with pytest.raises(FatalProviderError):
            a._request(system="s", messages=[], tools=[], max_tokens=5,
                       temperature=0.0)

    def test_500_retryable_400_fatal(self):
        import openai

        def err500(**req):
            raise _http_err(openai.InternalServerError, "boom", 500)

        with pytest.raises(RetryableProviderError):
            _adapter_with_fake_create(err500)._request(
                system="s", messages=[], tools=[], max_tokens=5,
                temperature=0.0)

        def err400(**req):
            raise _http_err(openai.BadRequestError, "bad schema", 400)

        with pytest.raises(FatalProviderError):
            _adapter_with_fake_create(err400)._request(
                system="s", messages=[], tools=[], max_tokens=5,
                temperature=0.0)

    def test_connection_error_retryable(self):
        import httpx
        import openai

        def boom(**req):
            raise openai.APIConnectionError(
                request=httpx.Request("POST", "http://x"))

        a = _adapter_with_fake_create(boom)
        with pytest.raises(RetryableProviderError):
            a._request(system="s", messages=[], tools=[], max_tokens=5,
                       temperature=0.0)


class TestBudgetHeal:
    def test_reasoning_exhaustion_scales_budget(self):
        """status=incomplete with zero visible output -> x4 budget retry,
        multiplier remembered for later calls."""
        seen = []

        def create(**req):
            seen.append(req["max_output_tokens"])
            if len(seen) == 1:
                return NS(status="incomplete", output=[
                    NS(type="reasoning", id="rs", summary=[],
                       encrypted_content="e")], usage=None,
                    incomplete_details=NS(reason="max_output_tokens"))
            return _fake_response()

        a = _adapter_with_fake_create(create)
        resp = a.complete(system="s", messages=[], tools=[], max_tokens=100)
        assert seen == [100, 400]
        assert a._learned["_max_tokens_mult"] == 4
        assert resp.tool_calls


class TestRouting:
    def test_gpt56_routes_to_responses(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_OPENAI_CHAT", raising=False)
        a = make_adapter("gpt-5.6-sol", api_key="k")
        assert isinstance(a, OpenAIResponsesAdapter)
        assert a.adapter_name.startswith("openai-responses:")
        assert isinstance(make_adapter("gpt-5.6-terra", api_key="k"),
                          OpenAIResponsesAdapter)

    def test_other_openai_models_stay_on_chat(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_OPENAI_CHAT", raising=False)
        for model in ("gpt-4o", "gpt-5-nano", "o4-mini"):
            a = make_adapter(model, api_key="k")
            assert type(a) is OpenAICompatAdapter

    def test_env_escape_hatch_forces_chat(self, monkeypatch):
        monkeypatch.setenv("FMBENCH_OPENAI_CHAT", "1")
        a = make_adapter("gpt-5.6-sol", api_key="k")
        assert type(a) is OpenAICompatAdapter

    def test_base_url_still_compat(self, monkeypatch):
        # Together/xAI/Meta paths are untouched even for gpt-ish ids
        monkeypatch.delenv("FMBENCH_OPENAI_CHAT", raising=False)
        a = make_adapter("gpt-5.6-sol", api_key="k",
                         base_url="http://localhost:1")
        assert type(a) is OpenAICompatAdapter

    def test_adapter_name_helper_matches_routing(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_OPENAI_CHAT", raising=False)
        monkeypatch.delenv("FMBENCH_OPENAI_REASONING_EFFORT", raising=False)
        assert adapter_name_for_model("gpt-5.6-sol") == \
            "openai-responses:medium"
        assert adapter_name_for_model("gpt-4o") == "openai-chat"
        assert adapter_name_for_model("claude-sonnet-5") == \
            "anthropic-messages:adaptive"
        assert adapter_name_for_model("gemini-3.5-flash") == \
            "google-genai:thinking-medium"
        assert adapter_name_for_model("Qwen/Qwen3.7-Max") == \
            "openai-chat:reasoning-on"
        monkeypatch.setenv("FMBENCH_OPENAI_CHAT", "1")
        assert adapter_name_for_model("gpt-5.6-sol") == "openai-chat"
