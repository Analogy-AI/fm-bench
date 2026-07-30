"""Gemini adapter: wire mapping, parsing, error mapping, routing, keys, prices.

All tests are offline — build_request/_parse are pure, HTTP mapping is tested
via the factored _map_http_error helper.
"""

import os
import unittest
from unittest import mock

from runner.costs import CostTracker
from runner.keys import find_api_key, provider_for_model
from runner.providers import (FatalProviderError, GoogleAdapter,
                              RetryableProviderError, ToolCall,
                              _gemini_schema, make_adapter)

TOOLS = [{
    "name": "get_squad",
    "description": "List squad players.",
    "input_schema": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "additionalProperties": False,
        "type": "object",
        "properties": {
            "sort": {"type": "string", "enum": ["age", "wage"],
                     "default": "age"},
            "filters": {"type": "object", "additionalProperties": False,
                        "properties": {"pos": {"type": "string"}}},
        },
        "required": ["sort"],
    },
}]


def adapter() -> GoogleAdapter:
    return GoogleAdapter("gemini-2.5-flash", api_key="test-key")


class TestBuildRequest(unittest.TestCase):
    def test_system_tools_and_config(self):
        req = adapter().build_request(
            system="You are a manager.", messages=[{"role": "user",
                                                    "text": "hi"}],
            tools=TOOLS, max_tokens=512, temperature=0.0)
        self.assertEqual(req["systemInstruction"],
                         {"parts": [{"text": "You are a manager."}]})
        self.assertEqual(req["generationConfig"],
                         {"maxOutputTokens": 512, "temperature": 0.0})
        decls = req["tools"][0]["functionDeclarations"]
        self.assertEqual(decls[0]["name"], "get_squad")
        params = decls[0]["parameters"]
        self.assertNotIn("$schema", params)
        self.assertNotIn("additionalProperties", params)
        self.assertNotIn("default", params["properties"]["sort"])
        self.assertNotIn("additionalProperties",
                         params["properties"]["filters"])
        self.assertEqual(params["required"], ["sort"])
        self.assertEqual(params["properties"]["sort"]["enum"],
                         ["age", "wage"])

    def test_every_functioncall_part_carries_thought_signature(self):
        # Gemini 3 enforces a thoughtSignature on EVERY functionCall part.
        # Turns truncated by max_tokens can emit a functionCall without one
        # (so even verbatim raw echo 400s), and the no-raw fallback never had
        # one: both must be stamped with Google's documented bypass value.
        msgs = [
            {"role": "user", "text": "go"},
            {"role": "assistant", "text": None,
             "tool_calls": [{"id": "g0", "name": "get_squad",
                             "args": {"sort": "age"}}],
             "raw": [{"thoughtSignature": "real-sig",
                      "functionCall": {"name": "get_squad",
                                       "args": {"sort": "age"}}},
                     {"functionCall": {"name": "get_squad",
                                       "args": {"sort": "wage"}}}]},
            {"role": "tool_results",
             "results": [{"id": "g0", "content": "{}"}], "note": None},
            {"role": "assistant", "text": None,
             "tool_calls": [{"id": "g1", "name": "get_squad",
                             "args": {"sort": "age"}}], "raw": None},
            {"role": "tool_results",
             "results": [{"id": "g1", "content": "{}"}], "note": None},
        ]
        req = adapter().build_request(system="s", messages=msgs, tools=TOOLS,
                                      max_tokens=64, temperature=0.0)
        fc_parts = [p for c in req["contents"] if c["role"] == "model"
                    for p in c["parts"] if "functionCall" in p]
        self.assertEqual(len(fc_parts), 3)
        self.assertTrue(all("thoughtSignature" in p for p in fc_parts))
        # a model-generated signature is echoed verbatim, never overwritten
        self.assertEqual(fc_parts[0]["thoughtSignature"], "real-sig")
        self.assertEqual(fc_parts[1]["thoughtSignature"],
                         GoogleAdapter._DUMMY_SIG)
        self.assertEqual(fc_parts[2]["thoughtSignature"],
                         GoogleAdapter._DUMMY_SIG)

    def test_tool_results_matched_by_name_via_id_map(self):
        msgs = [
            {"role": "user", "text": "go"},
            {"role": "assistant", "text": "checking",
             "tool_calls": [{"id": "g0", "name": "get_squad",
                             "args": {"sort": "age"}}], "raw": None},
            {"role": "tool_results",
             "results": [{"id": "g0", "content": "{\"players\": []}"}],
             "note": "budget low"},
        ]
        req = adapter().build_request(system="s", messages=msgs, tools=TOOLS,
                                      max_tokens=64, temperature=0.0)
        model_turn = req["contents"][1]
        self.assertEqual(model_turn["role"], "model")
        self.assertEqual(model_turn["parts"][0], {"text": "checking"})
        self.assertEqual(model_turn["parts"][1]["functionCall"]["name"],
                         "get_squad")
        result_turn = req["contents"][2]
        self.assertEqual(result_turn["role"], "user")
        fr = result_turn["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "get_squad")  # name, not id
        self.assertEqual(fr["response"], {"content": "{\"players\": []}"})
        self.assertEqual(result_turn["parts"][1], {"text": "budget low"})

    def test_empty_assistant_turn_gets_placeholder_part(self):
        req = adapter().build_request(
            system="s", messages=[{"role": "assistant", "text": None,
                                   "tool_calls": [], "raw": None}],
            tools=[], max_tokens=8, temperature=0.0)
        self.assertEqual(req["contents"][0]["parts"], [{"text": ""}])
        self.assertNotIn("tools", req)


class TestParse(unittest.TestCase):
    def test_function_calls_text_and_usage(self):
        raw = {"candidates": [{"content": {"parts": [
                   {"text": "reasoning...", "thought": True},
                   {"text": "Signing him."},
                   {"functionCall": {"name": "offer_contract",
                                     "args": {"player_id": "P1"}}},
                   {"functionCall": {"name": "advance", "args": {}}},
               ]}}],
               "usageMetadata": {"promptTokenCount": 1000,
                                 "cachedContentTokenCount": 800,
                                 "candidatesTokenCount": 50,
                                 "thoughtsTokenCount": 200}}
        resp = adapter()._parse(raw)
        self.assertEqual(resp.text, "Signing him.")  # thought part excluded
        self.assertEqual([type(c) for c in resp.tool_calls],
                         [ToolCall, ToolCall])
        self.assertEqual(resp.tool_calls[0].id, "g0")
        self.assertEqual(resp.tool_calls[1].id, "g1")
        self.assertEqual(resp.tool_calls[0].args, {"player_id": "P1"})
        self.assertEqual(resp.usage.input_tokens, 200)   # prompt - cached
        self.assertEqual(resp.usage.cache_read_tokens, 800)
        self.assertEqual(resp.usage.output_tokens, 250)  # incl. thoughts
        # raw now carries the model parts verbatim so Gemini 3.x
        # thoughtSignatures can be echoed back on later turns
        self.assertIsInstance(resp.raw, list)

    def test_empty_or_blocked_candidate(self):
        resp = adapter()._parse({"candidates": [],
                                 "usageMetadata": {"promptTokenCount": 10}})
        self.assertIsNone(resp.text)
        self.assertEqual(resp.tool_calls, [])
        self.assertEqual(resp.usage.input_tokens, 10)


class TestErrorMapping(unittest.TestCase):
    def test_429_retryable_with_retry_after(self):
        e = GoogleAdapter._map_http_error(429, "7.5", "quota")
        self.assertIsInstance(e, RetryableProviderError)
        self.assertEqual(e.retry_after_s, 7.5)

    def test_5xx_retryable_4xx_fatal(self):
        self.assertIsInstance(GoogleAdapter._map_http_error(503, None, ""),
                              RetryableProviderError)
        self.assertIsInstance(GoogleAdapter._map_http_error(400, None, "bad"),
                              FatalProviderError)

    def test_thought_signature_400_is_retryable_not_fatal(self):
        # 2026-07-22 arena incident: this 400 is OUR wire bug, not a dead
        # account. Fatal would silently idle a healthy seat (contamination);
        # retryable crashes for lossless resume and pages via the sentinel.
        e = GoogleAdapter._map_http_error(
            400, None, "Function call is missing a thought_signature in "
                       "functionCall parts.")
        self.assertIsInstance(e, RetryableProviderError)
        self.assertNotIsInstance(e, FatalProviderError)


class TestRoutingKeysPrices(unittest.TestCase):
    def test_gemini_routes_to_google_adapter(self):
        a = make_adapter("gemini-2.5-flash", api_key="k")
        self.assertIsInstance(a, GoogleAdapter)

    def test_provider_for_model(self):
        self.assertEqual(provider_for_model("claude-haiku-4-5"), "anthropic")
        self.assertEqual(provider_for_model("gemini-2.5-pro"), "google")
        self.assertEqual(provider_for_model("gpt-5-mini"), "openai")

    def test_google_key_from_env(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g-key"},
                             clear=False):
            self.assertEqual(find_api_key("google"), "g-key")
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "g2"},
                             clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertEqual(find_api_key("google"), "g2")

    def test_gemini_prices_present_and_estimated(self):
        t = CostTracker("gemini-2.5-flash", spend_cap_usd=None)
        self.assertTrue(t.price_estimated)
        t2 = CostTracker("claude-sonnet-4-6", spend_cap_usd=None)
        self.assertFalse(t2.price_estimated)


if __name__ == "__main__":
    unittest.main()
