"""Reasoning-fairness pass: every roster seat runs at its provider-recommended
thinking/reasoning capability, and the effective setting is stamped into
adapter_name. Request building only — no network anywhere."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from runner.providers import (AnthropicAdapter, GoogleAdapter,
                              OpenAICompatAdapter, OpenAIResponsesAdapter,
                              adapter_name_for_model, make_adapter)

TOOLS = [{"name": "get_squad", "input_schema": {"type": "object"}}]
MSGS = [{"role": "user", "text": "DECISION STOP S0001"}]


def _anthropic(model) -> AnthropicAdapter:
    a = AnthropicAdapter.__new__(AnthropicAdapter)  # build-only, no client
    a.model = model
    return a


def _compat(model) -> OpenAICompatAdapter:
    a = OpenAICompatAdapter.__new__(OpenAICompatAdapter)
    a.model = model
    return a


class TestAnthropicThinking:
    def test_fable_always_on_sends_no_thinking_param(self):
        req = _anthropic("claude-fable-5").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=1500,
            temperature=0)
        assert "thinking" not in req          # always-on; explicit config 400s
        assert "temperature" not in req       # removed on Fable (hard 400)
        assert req["max_tokens"] == 1500

    def test_adaptive_family_sends_adaptive_no_temperature(self):
        for model in ("claude-opus-4-8", "claude-sonnet-5",
                      "claude-opus-4-7", "claude-sonnet-4-6"):
            req = _anthropic(model).build_request(
                system="S", messages=MSGS, tools=TOOLS, max_tokens=1500,
                temperature=0)
            assert req["thinking"] == {"type": "adaptive"}, model
            assert "budget_tokens" not in req["thinking"]  # rejected on 4.7+
            assert "temperature" not in req, model

    def test_haiku_budget_mode(self):
        req = _anthropic("claude-haiku-4-5-20251001").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=1500,
            temperature=0)
        t = req["thinking"]
        assert t["type"] == "enabled"
        assert t["budget_tokens"] >= 1024
        # visible output budget preserved: cap grows by the thinking budget
        assert req["max_tokens"] == 1500 + t["budget_tokens"]
        # interleaved thinking between tool calls needs the beta on pre-4.6
        assert "interleaved-thinking" in req["extra_headers"]["anthropic-beta"]
        # extended thinking forbids non-default temperature
        assert "temperature" not in req

    def test_budget_minimum_1024(self):
        req = _anthropic("claude-haiku-4-5-20251001").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=100,
            temperature=0)
        assert req["thinking"]["budget_tokens"] == 1024

    def test_unknown_claude_unchanged(self):
        req = _anthropic("claude-3-haiku-20240307").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=500,
            temperature=0)
        assert "thinking" not in req
        assert req["temperature"] == 0       # previous behavior preserved

    def test_thinking_blocks_echoed_verbatim(self):
        """Anthropic requires thinking blocks preserved in assistant turns of
        a tool loop; the raw echo path must keep thinking + signature."""
        raw = [{"type": "thinking", "thinking": "hidden plan",
                "signature": "sig-abc",
                "cache_control": {"type": "ephemeral"}},
               {"type": "tool_use", "id": "t1", "name": "get_squad",
                "input": {}}]
        req = _anthropic("claude-opus-4-8").build_request(
            system="S",
            messages=[{"role": "user", "text": "go"},
                      {"role": "assistant", "text": None, "raw": raw,
                       "tool_calls": [{"id": "t1", "name": "get_squad",
                                       "args": {}}]},
                      {"role": "tool_results",
                       "results": [{"id": "t1", "content": "{}"}],
                       "note": None}],
            tools=TOOLS, max_tokens=1500, temperature=0)
        blocks = req["messages"][1]["content"]
        assert blocks[0]["type"] == "thinking"
        assert blocks[0]["thinking"] == "hidden plan"
        assert blocks[0]["signature"] == "sig-abc"      # signature preserved
        assert "cache_control" not in blocks[0]         # stale cache stripped

    def test_stamp_per_mode(self):
        assert adapter_name_for_model("claude-fable-5") == \
            "anthropic-messages:always-on"
        assert adapter_name_for_model("claude-opus-4-8") == \
            "anthropic-messages:adaptive"
        assert adapter_name_for_model("claude-haiku-4-5-20251001") == \
            "anthropic-messages:budget"


def _http_err(cls, msg, status=400):
    import httpx
    return cls(msg,
               response=httpx.Response(status,
                                       request=httpx.Request("POST",
                                                             "http://x")),
               body=None)


class TestAnthropicHeals:
    def _adapter_with_create(self, create):
        a = AnthropicAdapter("claude-opus-4-8", api_key="k")
        a._client = NS(messages=NS(create=create))
        return a

    def test_thinking_rejection_heals_and_restamps(self):
        import anthropic
        seen = []

        def create(**req):
            seen.append(req)
            if "thinking" in req:
                raise _http_err(anthropic.BadRequestError,
                                "thinking is not supported on this model")
            return NS(content=[], stop_reason="end_turn",
                      usage=NS(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0,
                               cache_creation_input_tokens=0))

        a = self._adapter_with_create(create)
        a._request(system="s", messages=[], tools=[], max_tokens=10,
                   temperature=0.0)
        assert a._learned["_no_thinking"] is True
        assert a.adapter_name == "anthropic-messages:none-healed"
        assert "thinking" not in seen[-1]
        # later builds skip the param without another 400 round-trip
        assert "thinking" not in a.build_request(
            system="s", messages=[], tools=[], max_tokens=10, temperature=0)

    def test_all_thinking_truncation_scales_max_tokens(self):
        seen = []

        def create(**req):
            seen.append(req["max_tokens"])
            if len(seen) == 1:
                return NS(content=[NS(type="thinking")],
                          stop_reason="max_tokens",
                          usage=NS(input_tokens=1, output_tokens=10,
                                   cache_read_input_tokens=0,
                                   cache_creation_input_tokens=0))
            return NS(content=[NS(type="text", text="ok")],
                      stop_reason="end_turn",
                      usage=NS(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0,
                               cache_creation_input_tokens=0))

        a = self._adapter_with_create(create)
        a._request(system="s", messages=[], tools=[], max_tokens=100,
                   temperature=0.0)
        assert seen == [100, 400]
        assert a._learned["_max_tokens_mult"] == 4


class TestGeminiThinking:
    def _req(self, model, monkeypatch=None):
        return GoogleAdapter(model, api_key="k").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=512,
            temperature=0.0)

    def test_gemini3_explicit_thinking_level(self):
        req = self._req("gemini-3-flash-preview")
        cfg = req["generationConfig"]
        assert cfg["thinkingConfig"] == {"thinkingLevel": "high"}
        assert "temperature" not in cfg     # Gemini 3: keep default (1.0)

    def test_gemini35_default_medium(self):
        cfg = self._req("gemini-3.5-flash")["generationConfig"]
        assert cfg["thinkingConfig"] == {"thinkingLevel": "medium"}
        assert "temperature" not in cfg

    def test_gemini2x_unchanged(self):
        cfg = self._req("gemini-2.5-flash")["generationConfig"]
        assert "thinkingConfig" not in cfg
        assert cfg["temperature"] == 0.0

    def test_env_override_and_stamp(self, monkeypatch):
        monkeypatch.setenv("FMBENCH_GEMINI_THINKING_LEVEL", "low")
        cfg = self._req("gemini-3.5-flash")["generationConfig"]
        assert cfg["thinkingConfig"] == {"thinkingLevel": "low"}
        assert adapter_name_for_model("gemini-3.5-flash") == \
            "google-genai:thinking-low"
        monkeypatch.delenv("FMBENCH_GEMINI_THINKING_LEVEL")
        assert adapter_name_for_model("gemini-3-flash-preview") == \
            "google-genai:thinking-high"
        assert adapter_name_for_model("gemini-2.5-flash") == "google-genai"
        a = GoogleAdapter("gemini-3.5-flash", api_key="k")
        assert a.adapter_name == "google-genai:thinking-medium"


class TestCompatReasoningKnobs:
    def test_together_hybrids_get_reasoning_enabled(self):
        for model in ("deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3.7-Max",
                      "zai-org/GLM-5.2", "moonshotai/Kimi-K2.6"):
            req = _compat(model).build_request(
                system="S", messages=MSGS, tools=TOOLS, max_tokens=100,
                temperature=0)
            assert req["extra_body"] == {"reasoning": {"enabled": True}}, model
            # vendors recommend default temperature with thinking on
            assert "temperature" not in req, model

    def test_llama_and_grok_unchanged_but_stamped(self):
        req = _compat("meta-llama/Llama-3.3-70B-Instruct-Turbo").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=100,
            temperature=0)
        assert "extra_body" not in req and req["temperature"] == 0
        req = _compat("grok-4.5").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=100,
            temperature=0)
        assert "extra_body" not in req and "reasoning_effort" not in req

    def test_muse_chat_escape_hatch_gets_effort(self):
        req = _compat("muse-spark-1.1").build_request(
            system="S", messages=MSGS, tools=TOOLS, max_tokens=100,
            temperature=0)
        assert req["reasoning_effort"] == "high"
        assert "temperature" not in req

    def test_stamps(self):
        assert adapter_name_for_model("deepseek-ai/DeepSeek-V4-Pro") == \
            "openai-chat:reasoning-on"
        assert adapter_name_for_model("moonshotai/Kimi-K2.6") == \
            "openai-chat:reasoning-on"
        assert adapter_name_for_model("grok-4.5") == \
            "openai-chat:reasoning-builtin"
        assert adapter_name_for_model(
            "meta-llama/Llama-3.3-70B-Instruct-Turbo") == \
            "openai-chat:non-reasoning"
        # unknown/base_url models keep the bare stamp (no silent claims)
        assert adapter_name_for_model("gpt-4o") == "openai-chat"
        assert adapter_name_for_model("some-local-model",
                                      base_url="http://x") == "openai-chat"

    def test_reasoning_trace_echoed_on_assistant_turn(self):
        msgs = [{"role": "user", "text": "go"},
                {"role": "assistant", "text": None,
                 "raw": {"reasoning_content": "let me check the squad"},
                 "tool_calls": [{"id": "c1", "name": "get_squad",
                                 "args": {}}]},
                {"role": "tool_results",
                 "results": [{"id": "c1", "content": "{}"}], "note": None}]
        req = _compat("deepseek-ai/DeepSeek-V4-Pro").build_request(
            system="S", messages=msgs, tools=TOOLS, max_tokens=100,
            temperature=0)
        assistant = req["messages"][2]   # [0]=system, [1]=user
        assert assistant["role"] == "assistant"
        assert assistant["reasoning_content"] == "let me check the squad"
        assert assistant["tool_calls"][0]["id"] == "c1"

    def test_parse_captures_reasoning_for_echo(self):
        a = _compat("zai-org/GLM-5.2")
        resp = NS(choices=[NS(message=NS(content="done", tool_calls=None,
                                         reasoning="thought hard"))],
                  usage=NS(prompt_tokens=10, completion_tokens=5,
                           prompt_tokens_details=None))
        parsed = a._parse(resp)
        assert parsed.raw == {"reasoning": "thought hard"}

    def test_parse_without_reasoning_keeps_raw_none(self):
        a = _compat("meta-llama/Llama-3.3-70B-Instruct-Turbo")
        resp = NS(choices=[NS(message=NS(content="done", tool_calls=None))],
                  usage=NS(prompt_tokens=10, completion_tokens=5,
                           prompt_tokens_details=None))
        assert a._parse(resp).raw is None

    def test_knob_rejection_heals_and_restamps(self):
        import openai
        seen = []

        def create(**req):
            seen.append(req)
            if "extra_body" in req:
                raise _http_err(openai.BadRequestError,
                                "unknown parameter: reasoning")
            return NS(choices=[NS(message=NS(content="ok",
                                             tool_calls=None))],
                      usage=NS(prompt_tokens=1, completion_tokens=1,
                               prompt_tokens_details=None))

        a = OpenAICompatAdapter("zai-org/GLM-5.2", api_key="k",
                                base_url="http://localhost:1")
        a._client = NS(chat=NS(completions=NS(create=create)))
        a._request(system="s", messages=[], tools=[], max_tokens=5,
                   temperature=0.0)
        assert a._learned["_no_reasoning_param"] is True
        assert a.adapter_name == "openai-chat:reasoning-default-healed"
        assert "extra_body" not in seen[-1]
        # later requests strip the knob up front (no repeat 400)
        seen.clear()
        a._request(system="s", messages=[], tools=[], max_tokens=5,
                   temperature=0.0)
        assert len(seen) == 1 and "extra_body" not in seen[0]

    def test_streamed_reasoning_deltas_accumulated(self):
        a = OpenAICompatAdapter("Qwen/Qwen3.7-Max", api_key="k",
                                base_url="http://localhost:1")
        chunks = [
            NS(usage=None, choices=[NS(delta=NS(content=None,
                                                reasoning="think ",
                                                tool_calls=None))]),
            NS(usage=None, choices=[NS(delta=NS(content="hi",
                                                reasoning="hard",
                                                tool_calls=None))]),
            NS(usage=NS(prompt_tokens=3, completion_tokens=2,
                        prompt_tokens_details=None), choices=[]),
        ]
        a._client = NS(chat=NS(completions=NS(
            create=lambda **req: iter(chunks))))
        resp = a._streamed_create({"messages": [], "model": a.model})
        parsed = a._parse(resp)
        assert parsed.text == "hi"
        assert parsed.raw == {"reasoning": "think hard"}


class TestMuseRouting:
    def test_muse_routes_to_responses_with_high_effort(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_META_CHAT", raising=False)
        monkeypatch.delenv("FMBENCH_META_REASONING_EFFORT", raising=False)
        a = make_adapter("Muse-Spark-1.1", api_key="k")
        assert isinstance(a, OpenAIResponsesAdapter)
        assert a.model == "muse-spark-1.1"          # server id is lowercase
        assert a.adapter_name == "openai-responses:high"
        req = a.build_request(system="S", messages=MSGS, tools=TOOLS,
                              max_tokens=100, temperature=0)
        assert req["reasoning"] == {"effort": "high"}
        assert "temperature" not in req             # reasoning model
        assert adapter_name_for_model("Muse-Spark-1.1") == \
            "openai-responses:high"

    def test_meta_chat_escape_hatch(self, monkeypatch):
        monkeypatch.setenv("FMBENCH_META_CHAT", "1")
        a = make_adapter("Muse-Spark-1.1", api_key="k")
        assert type(a) is OpenAICompatAdapter
        assert a.adapter_name == "openai-chat:reasoning-high"
        assert adapter_name_for_model("Muse-Spark-1.1") == \
            "openai-chat:reasoning-high"

    def test_llama_still_compat(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_META_CHAT", raising=False)
        a = make_adapter("meta-llama/Llama-3.3-70B-Instruct-Turbo",
                         api_key="k")
        assert type(a) is OpenAICompatAdapter
        assert a.adapter_name == "openai-chat:non-reasoning"

    def test_gpt56_effort_env_does_not_leak_to_muse(self, monkeypatch):
        monkeypatch.delenv("FMBENCH_META_CHAT", raising=False)
        monkeypatch.setenv("FMBENCH_OPENAI_REASONING_EFFORT", "low")
        monkeypatch.setenv("FMBENCH_META_REASONING_EFFORT", "medium")
        a = make_adapter("Muse-Spark-1.1", api_key="k")
        assert a.adapter_name == "openai-responses:medium"


class TestRosterStampCoverage:
    """Every roster seat's stamp names its effective reasoning setting —
    pools with different settings can never be merged silently."""

    EXPECTED = {
        "claude-fable-5": "anthropic-messages:always-on",
        "claude-opus-4-8": "anthropic-messages:adaptive",
        "claude-haiku-4-5-20251001": "anthropic-messages:budget",
        "claude-sonnet-5": "anthropic-messages:adaptive",
        "gpt-5.6-sol": "openai-responses:medium",
        "gpt-5.6-terra": "openai-responses:medium",
        "gemini-3.5-flash": "google-genai:thinking-medium",
        "gemini-3-flash-preview": "google-genai:thinking-high",
        "grok-4.5": "openai-chat:reasoning-builtin",
        "deepseek-ai/DeepSeek-V4-Pro": "openai-chat:reasoning-on",
        "moonshotai/Kimi-K2.6": "openai-chat:reasoning-on",
        "Qwen/Qwen3.7-Max": "openai-chat:reasoning-on",
        "zai-org/GLM-5.2": "openai-chat:reasoning-on",
        "Muse-Spark-1.1": "openai-responses:high",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo": "openai-chat:non-reasoning",
    }

    def test_all_15_seats(self, monkeypatch):
        for var in ("FMBENCH_OPENAI_CHAT", "FMBENCH_META_CHAT",
                    "FMBENCH_OPENAI_REASONING_EFFORT",
                    "FMBENCH_META_REASONING_EFFORT",
                    "FMBENCH_GEMINI_THINKING_LEVEL"):
            monkeypatch.delenv(var, raising=False)
        for model, stamp in self.EXPECTED.items():
            assert adapter_name_for_model(model) == stamp, model

    def test_stamps_match_constructed_adapters(self, monkeypatch):
        for var in ("FMBENCH_OPENAI_CHAT", "FMBENCH_META_CHAT",
                    "FMBENCH_OPENAI_REASONING_EFFORT",
                    "FMBENCH_META_REASONING_EFFORT",
                    "FMBENCH_GEMINI_THINKING_LEVEL"):
            monkeypatch.delenv(var, raising=False)
        for model, stamp in self.EXPECTED.items():
            a = make_adapter(model, api_key="k")
            assert a.adapter_name == stamp, model
