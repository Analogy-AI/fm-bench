"""Provider adapters: one small interface so any tool-calling LLM can play.

    adapter = make_adapter("claude-haiku-4-5-20251001", api_key=key)
    resp = adapter.complete(system=..., messages=..., tools=..., ...)

Neutral message format (provider-agnostic, owned by the runner):
    {"role": "user", "text": str}
    {"role": "assistant", "text": str | None,
     "tool_calls": [{"id": str, "name": str, "args": dict}],
     "raw": <provider-native content, reused verbatim when provider matches>}
    {"role": "tool_results", "results": [{"id": str, "content": str}],
     "note": str | None}

Adding a provider = subclass ProviderAdapter, implement _request() and
_parse(), register a routing rule in make_adapter (~50 lines).
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field

DEFAULT_TIMEOUT_S = 2000.0  # was 120/300/600: under the arena's no-skip rule
# a seat NEVER misses a stop, so the read timeout must accommodate the slowest
# legitimate generation. 120s deterministically killed GLM-5.2's long turns
# (endpoint alive, tiny requests 0.6s); 300s still cut them off — every attempt
# timed out, an infinite crash-resume loop; at 600s a legitimate call was
# observed brushing the ~10min window. Owner decision 2026-07-22: 2000s, and
# a timeout halts for a human retry/skip call instead of looping (0 retries).
# Timeout is harness patience, not game semantics.
DEFAULT_RETRIES = 5
BACKOFF_BASE_S = 1.5
BACKOFF_CAP_S = 45.0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    raw: object = None  # provider-native assistant content, for verbatim resend


def _is_content_filter(msg: str) -> bool:
    """A provider content-moderation false positive is request-specific and
    transient (the next stop's different text usually passes), so it must NOT
    kill a seat for the whole run. Match the common provider phrasings."""
    m = msg.lower()
    return any(s in m for s in (
        "data_inspection_failed", "inappropriate content", "content_filter",
        "content management policy", "content policy", "flagged",
        "responsibleai", "content filter"))


class FatalProviderError(RuntimeError):
    """Non-retryable (auth, bad request, unknown model)."""


class RetryableProviderError(RuntimeError):
    def __init__(self, msg: str, retry_after_s: float | None = None):
        super().__init__(msg)
        self.retry_after_s = retry_after_s


class ProviderTimeoutError(RetryableProviderError):
    """Read timeout: the provider accepted the request but no complete
    response arrived within timeout_s (or the wall-clock watchdog fired).
    Owner rule 2026-07-22: one timeout at 600s IS the alert condition —
    complete() must not burn retries on it. Subclassing
    RetryableProviderError keeps the controller's exhausted-stop path
    unchanged (STOP-FAILURE -> crash -> lossless resume); the arena_watch
    sentinel then halts the supervisor and pages the owner, who decides
    retry vs skip."""


class ContentPolicyError(RetryableProviderError):
    """Provider content-moderation rejection (e.g. Together/Alibaba
    data_inspection_failed). Retryable so complete() WAITS and re-attempts a
    few times, but it must NEVER be silently skipped: if it persists, the
    seat's controller re-raises it to crash the run (recovered by
    bit-identical resume, exactly the 1v15 behavior), and every occurrence is
    ALERTED so a content-review event can never contaminate the data unseen."""


@dataclass
class RetryPolicy:
    retries: int = DEFAULT_RETRIES
    backoff_base_s: float = BACKOFF_BASE_S
    backoff_cap_s: float = BACKOFF_CAP_S
    sleep = staticmethod(time.sleep)  # injectable for tests

    def delay(self, attempt: int, retry_after_s: float | None) -> float:
        if retry_after_s is not None:
            return min(retry_after_s, self.backoff_cap_s)
        base = min(self.backoff_base_s * (2 ** attempt), self.backoff_cap_s)
        return base + random.uniform(0, base / 2)


class ProviderAdapter:
    """Shared retry/timeout shell; subclasses do wire formats only."""

    provider = "abstract"
    #: wire-path identity, stamped into manifests/results so consumers can
    #: tell (e.g.) reasoning-capable openai-responses runs apart from legacy
    #: openai-chat runs that self-healed to reasoning_effort=none
    adapter_name = "abstract"

    def __init__(self, model: str, api_key: str | None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 retry: RetryPolicy | None = None,
                 rate_limiter=None):
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retry = retry or RetryPolicy()
        self.rate_limiter = rate_limiter  # optional KeyRateLimiter

    def complete(self, *, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int, temperature: float = 0.0) -> LLMResponse:
        last: Exception | None = None
        for attempt in range(self.retry.retries + 1):
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            try:
                return self._parse(self._call_with_watchdog(
                    system=system, messages=messages, tools=tools,
                    max_tokens=max_tokens, temperature=temperature))
            except RetryableProviderError as e:
                last = e
                if isinstance(e, ProviderTimeoutError):
                    break  # owner rule: a read timeout pages immediately, 0 retries
                if attempt >= self.retry.retries:
                    break
                self.retry.sleep(self.retry.delay(attempt, e.retry_after_s))
        if isinstance(last, ContentPolicyError):
            # content review must never pass unnoticed: alert on every
            # exhausted case, in solo and league alike, then re-raise (the
            # run crashes and bit-identical resume re-attempts, never skips)
            print(f"🚨 CONTENT-REVIEW ALERT: model={self.model} provider "
                  f"content moderation did not clear after "
                  f"{self.retry.retries + 1} attempts: {str(last)[:160]}",
                  flush=True)
        raise last  # exhausted

    def _call_with_watchdog(self, **kw):
        """Hard wall-clock cap around _request. The SDK `timeout` is per-read
        and a stalled stream (server sends a chunk then hangs) can block
        forever, freezing a shared-world league day on one seat (the overnight
        arena hangs). Run the request in a worker thread and abandon it after
        a hard deadline, raising RetryableProviderError so complete()/the
        league controller retries or skips. The abandoned thread is a daemon;
        its socket read dies with the process, and no result is consumed."""
        import concurrent.futures as _cf
        deadline = max(self.timeout_s * 2.0, 90.0)
        ex = _cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self._request, **kw)
        try:
            return fut.result(timeout=deadline)
        except _cf.TimeoutError:
            raise ProviderTimeoutError(
                f"wall-clock watchdog: no response in {deadline:.0f}s") from None
        except (FatalProviderError, RetryableProviderError):
            raise  # already classified by the adapter's own mapping
        except Exception as e:
            # RAW transport faults leak past the SDKs' exception mapping (a
            # streamed read dying mid-body surfaces as httpx.RemoteProtocolError,
            # not APIConnectionError) — one of these crashed a whole league
            # process. They are transient by nature: classify retryable so the
            # in-process retry loop absorbs them instead of a crash+resume
            # cycle. Anything that is NOT a known transport family still
            # propagates: a genuine code bug must stay loud, never retried.
            mod = (type(e).__module__ or "").split(".")[0]
            if isinstance(e, OSError) or mod in (
                    "httpx", "httpcore", "h11", "anyio", "urllib3"):
                raise RetryableProviderError(
                    f"transport: {type(e).__name__}: {e}") from e
            raise
        finally:
            ex.shutdown(wait=False)

    # subclass surface -----------------------------------------------------------
    def _request(self, *, system, messages, tools, max_tokens, temperature):
        raise NotImplementedError

    def _parse(self, raw) -> LLMResponse:
        raise NotImplementedError


# --- Anthropic ------------------------------------------------------------------

#: interleaved thinking between tool calls on pre-4.6 models (haiku-4-5 etc.)
#: needs this beta header; adaptive-thinking models get it automatically.
_ANTHROPIC_INTERLEAVED_BETA = "interleaved-thinking-2025-05-14"


def _anthropic_thinking_mode(model: str) -> str:
    """Provider-recommended thinking config per model family (verified against
    Anthropic docs 2026-07-14):

    - always-on: Fable/Mythos 5 — thinking cannot be configured; sending
      {"type": "disabled"} or budget_tokens is a hard 400. Omit the param.
    - adaptive:  4.6+/5 family — {"type": "adaptive"} is the only on-mode;
      budget_tokens is REJECTED (400). Opus 4.7/4.8 run WITHOUT thinking when
      the param is omitted, so it must be sent explicitly.
    - budget:    pre-4.6 models (haiku-4-5, sonnet-4-5, opus-4-5) — thinking
      needs {"type": "enabled", "budget_tokens": N>=1024}; adaptive is not
      supported there.
    - none:      unknown claude ids — leave the request unchanged (previous
      behavior) rather than risk a 400 on an unrecognized family.
    """
    m = model.lower()
    if m.startswith(("claude-fable", "claude-mythos")):
        return "always-on"
    if m.startswith(("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                     "claude-sonnet-5", "claude-sonnet-4-6")):
        return "adaptive"
    if m.startswith(("claude-haiku-4-5", "claude-sonnet-4-5",
                     "claude-opus-4-5")):
        return "budget"
    return "none"



def _cache_ctl() -> dict:
    """Anthropic cache breakpoint. Default 5-min ephemeral; set
    FMBENCH_ANTHROPIC_CACHE_TTL=1h for long-lived caching (2x write price,
    1h TTL) — essential in the 16-seat arena, where one full round of seats
    between a seat's consecutive turns exceeds the 5-minute default TTL and
    would turn every turn into a cache miss."""
    import os as _os
    ttl = _os.environ.get("FMBENCH_ANTHROPIC_CACHE_TTL")
    return {"type": "ephemeral", "ttl": "1h"} if ttl == "1h" \
        else {"type": "ephemeral"}

class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API with aggressive prompt caching.

    Cache breakpoints (4 allowed; 3 used):
      1. system block           — frozen for the whole run
      2. last tool schema       — frozen for the whole run
      3. last block of the final request message — MOVING breakpoint: each
         call re-reads the whole prior conversation from cache and writes
         only the new turn (incremental caching; measured 16% -> ~90%+
         cache-read on multi-turn stops).
    """

    provider = "anthropic"
    adapter_name = "anthropic-messages"

    #: Claude 4.7+/5 family removed sampling params — sending temperature is a
    #: hard 400 ("`temperature` is deprecated for this model"). Older models
    #: (haiku-4-5, sonnet-4-x, opus-4-6 and earlier) still accept it.
    _NO_TEMPERATURE_PREFIXES = ("claude-fable", "claude-mythos",
                                "claude-opus-4-7", "claude-opus-4-8",
                                "claude-sonnet-5")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import anthropic  # hard dep already installed
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=self.api_key, timeout=self.timeout_s, max_retries=0)
        self._no_temperature = self.model.startswith(
            self._NO_TEMPERATURE_PREFIXES)
        self._thinking_mode = _anthropic_thinking_mode(self.model)
        # thinking mode in the stamp: pools with thinking on/off must never be
        # mixed silently (same rule as OpenAIResponsesAdapter's effort stamp)
        self.adapter_name = f"anthropic-messages:{self._thinking_mode}"
        # server quirks learned from rejections (mirrors the OpenAI adapters)
        self._learned: dict = {}

    # -- wire building (pure; unit-tested without network) -----------------------

    def build_request(self, *, system, messages, tools, max_tokens,
                      temperature) -> dict:
        sys_blocks = [{"cache_control": _cache_ctl(),
                       "text": system, "type": "text"}]
        wire_tools = [dict(t) for t in tools]
        if wire_tools:
            wire_tools[-1] = {**wire_tools[-1],
                              "cache_control": _cache_ctl()}
        wire_msgs = [self._to_wire(m) for m in messages]
        _mark_last_block(wire_msgs)
        req = {"max_tokens": max_tokens, "messages": wire_msgs,
               "model": self.model, "system": sys_blocks,
               "tools": wire_tools}
        # lazy so build_request stays a pure function of the model (tests
        # construct via __new__); the learned flag from self-heal still wins
        no_temp = getattr(self, "_no_temperature", None)
        if no_temp is None:
            no_temp = self.model.startswith(self._NO_TEMPERATURE_PREFIXES)
        mode = getattr(self, "_thinking_mode", None) \
            or _anthropic_thinking_mode(self.model)
        if getattr(self, "_learned", None) and self._learned.get("_no_thinking"):
            mode = "none"
        if mode == "adaptive":
            # 4.6+/5 family: {"type": "adaptive"} is the recommended (and only)
            # on-mode; Opus 4.7/4.8 run WITHOUT thinking if this is omitted
            req["thinking"] = {"type": "adaptive"}
        elif mode == "budget":
            # pre-4.6 models: extended thinking needs an explicit token budget
            # (min 1024). Budget tokens bill as output and share max_tokens, so
            # grow the cap to keep the VISIBLE output budget meaningful (same
            # rationale as the OpenAI adapters' _max_tokens_mult). Interleaved
            # thinking between tool calls needs the beta header on these models
            # (adaptive-thinking models get it automatically).
            budget = max(1024, max_tokens)
            req["max_tokens"] = max_tokens + budget
            req["thinking"] = {"budget_tokens": budget, "type": "enabled"}
            req["extra_headers"] = {
                "anthropic-beta": _ANTHROPIC_INTERLEAVED_BETA}
        # "always-on" (Fable/Mythos): thinking runs adaptively with NO param;
        # any explicit config other than {"type": "adaptive"} is a 400.
        # temperature: removed on 4.7+/5 (hard 400), and extended thinking
        # forbids non-default temperature on the older models — omit it
        # whenever a thinking config is sent.
        if not no_temp and mode == "none":
            req["temperature"] = temperature
        return req

    def _to_wire(self, m: dict) -> dict:
        role = m["role"]
        if role == "user":
            return {"role": "user",
                    "content": [{"text": m["text"], "type": "text"}]}
        if role == "assistant":
            if m.get("raw") is not None:
                return {"role": "assistant", "content": _strip_cache(m["raw"])}
            content = []
            if m.get("text"):
                content.append({"text": m["text"], "type": "text"})
            for tc in m.get("tool_calls", []):
                content.append({"id": tc["id"], "input": tc["args"],
                                "name": tc["name"], "type": "tool_use"})
            return {"role": "assistant", "content": content}
        if role == "tool_results":
            content = [{"content": r["content"], "tool_use_id": r["id"],
                        "type": "tool_result"} for r in m["results"]]
            if m.get("note"):
                content.append({"text": m["note"], "type": "text"})
            return {"role": "user", "content": content}
        raise ValueError(f"unknown role {role!r}")

    # -- request/parse ------------------------------------------------------------

    @staticmethod
    def _has_visible_output(resp) -> bool:
        return any(getattr(b, "type", None) in ("text", "tool_use")
                   for b in resp.content or [])

    def _create_with_budget_heal(self, req: dict):
        """Thinking bills as output and shares max_tokens; when a response is
        cut at max_tokens with nothing visible (all thinking), scale the budget
        (x4, capped x16 like the OpenAI adapters) and remember the multiplier."""
        mult = self._learned.get("_max_tokens_mult")
        if mult:
            req["max_tokens"] = req["max_tokens"] * mult
        resp = self._client.messages.create(**req)
        if (getattr(resp, "stop_reason", None) == "max_tokens"
                and not self._has_visible_output(resp)):
            prev = self._learned.get("_max_tokens_mult", 1)
            if prev < 16:
                self._learned["_max_tokens_mult"] = prev * 4
                req["max_tokens"] = req["max_tokens"] * 4
                resp = self._client.messages.create(**req)
        return resp

    def _request(self, **kw):
        a = self._anthropic
        try:
            req = self.build_request(**kw)
            try:
                return self._create_with_budget_heal(req)
            except a.BadRequestError as e:
                # self-heal for models beyond the known prefix list that also
                # reject sampling params; remember so later calls skip the 400
                if "temperature" in str(e) and "temperature" in req:
                    req.pop("temperature")
                    self._no_temperature = True
                    return self._create_with_budget_heal(req)
                # a model outside the verified families rejected the thinking
                # config: drop it, remember, and re-stamp so result pools can't
                # silently mix thinking-on and thinking-off runs
                if "thinking" in str(e) and "thinking" in req:
                    req.pop("thinking")
                    req.pop("extra_headers", None)
                    self._learned["_no_thinking"] = True
                    self.adapter_name = "anthropic-messages:none-healed"
                    return self._create_with_budget_heal(req)
                raise
        except a.RateLimitError as e:
            raise RetryableProviderError(str(e),
                                         retry_after_s=_retry_after(e)) from e
        except a.APIConnectionError as e:
            if isinstance(e, a.APITimeoutError):
                raise ProviderTimeoutError(f"timeout: {e}") from e
            raise RetryableProviderError(f"connection: {e}") from e
        except a.APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 529:
                raise RetryableProviderError(f"status {e.status_code}") from e
            if _is_content_filter(str(e)):
                raise ContentPolicyError(f"content-filter: {e}") from e
            if e.status_code in (401, 403):
                raise FatalProviderError(f"auth {e.status_code}: {e}") from e
            # any other per-request status (400 malformed, 404, 422, ...) is
            # NOT a dead account: skip this stop and retry. Only genuine
            # account failures (auth, quota) may disable a seat — the rulebase
            # guarantee against board-firing a seat for a technical outage.
            raise RetryableProviderError(f"status {e.status_code}: {e}") from e

    def _parse(self, resp) -> LLMResponse:
        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name,
                                      args=dict(block.input or {})))
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)
        return LLMResponse(text="\n".join(text_parts) or None,
                           tool_calls=calls, usage=usage, raw=resp.content)


def _mark_last_block(wire_msgs: list[dict]) -> None:
    """Moving conversation breakpoint: cache everything up to and including
    the final message of this request."""
    if not wire_msgs:
        return
    content = wire_msgs[-1]["content"]
    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            content[-1] = {**last, "cache_control": _cache_ctl()}


def _strip_cache(raw_content) -> list:
    """Native SDK blocks resent verbatim, minus any stale cache_control."""
    out = []
    for block in raw_content:
        if hasattr(block, "model_dump"):
            d = block.model_dump(exclude_none=True)
        else:
            d = dict(block)
        d.pop("cache_control", None)
        out.append(d)
    return out


def _retry_after(err) -> float | None:
    try:
        v = err.response.headers.get("retry-after")
        return float(v) if v else None
    except Exception:
        return None


# --- OpenAI-compatible (OpenAI, local servers via base_url) ----------------------

def _meta_reasoning_effort() -> str:
    """Reasoning effort for Meta's Muse Spark models. Meta's agent guidance
    recommends an explicit high effort for agentic/coding work; the model
    reasons at a model-chosen level when the knob is omitted and rejects
    attempts to disable reasoning entirely."""
    return os.environ.get("FMBENCH_META_REASONING_EFFORT", "high")


def _compat_reasoning(model: str) -> tuple[dict, str | None]:
    """Per-model reasoning knobs for the chat.completions path.

    Returns (request params, adapter_name suffix). Sources (2026-07-14):
    - Together-served hybrids (DeepSeek V4, Qwen 3.7, GLM 5.2, Kimi K2.6):
      reasoning runs ON by default; the unified `reasoning` knob (extra_body)
      pins it explicitly so serving-default drift can't silently change what
      a run measured. Servers that reject the knob are healed back to the
      (still reasoning-on) default and re-stamped.
    - grok-4.x: reasoning is always on with no accepted knob; xAI rejects
      effort-style params on grok-4 — nothing to send, stamp only.
    - muse-spark (chat.completions escape hatch; primary route is the
      Responses adapter): documented `reasoning_effort` knob.
    - meta-llama/Llama-3.x: non-reasoning by design — documented in the stamp.
    Unknown models: no knob, stamp unchanged (previous behavior).
    """
    m = model.lower()
    if m.startswith(("deepseek-ai/deepseek-v4", "qwen/qwen3.7",
                     "zai-org/glm-5.2", "moonshotai/kimi-k2.6",
                     "minimaxai/minimax-m")):  # M-series: reasoning family,
        # Together serving default ON — pinned explicitly like the others
        return {"extra_body": {"reasoning": {"enabled": True}}}, "reasoning-on"
    if m.startswith("grok-4"):
        return {}, "reasoning-builtin"
    if m.startswith("muse"):
        effort = _meta_reasoning_effort()
        return {"reasoning_effort": effort}, f"reasoning-{effort}"
    if m.startswith("meta-llama/llama-3"):
        return {}, "non-reasoning"
    return {}, None


class OpenAICompatAdapter(ProviderAdapter):
    provider = "openai"
    adapter_name = "openai-chat"

    def __init__(self, *args, base_url: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            import openai
        except ImportError as e:
            raise FatalProviderError(
                "openai package not installed — `pip install openai` to run "
                "gpt-*/base_url models") from e
        self._openai = openai
        self._client = openai.OpenAI(api_key=self.api_key, base_url=base_url,
                                     timeout=self.timeout_s, max_retries=0)
        # params learned from server rejections (e.g. reasoning_effort for
        # gpt-5.x function-tool calls); reused on every later request so the
        # discovery cost is paid once per run, not per call.
        self._learned: dict = {}
        # per-model reasoning knobs + stamp (see _compat_reasoning)
        self._reasoning_params, suffix = _compat_reasoning(self.model)
        if suffix:
            self.adapter_name = f"openai-chat:{suffix}"

    def build_request(self, *, system, messages, tools, max_tokens,
                      temperature) -> dict:
        wire = [{"content": system, "role": "system"}]
        for m in messages:
            role = m["role"]
            if role == "user":
                wire.append({"content": m["text"], "role": "user"})
            elif role == "assistant":
                entry = {"content": m.get("text") or None, "role": "assistant"}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"function": {"arguments": json.dumps(tc["args"],
                                                              sort_keys=True),
                                      "name": tc["name"]},
                         "id": tc["id"], "type": "function"}
                        for tc in m["tool_calls"]]
                if isinstance(m.get("raw"), dict):
                    # echo the reasoning trace verbatim under the key the
                    # server returned it on (reasoning / reasoning_content):
                    # DeepSeek/GLM/Kimi tool loops expect the assistant turn
                    # that made the tool call to carry its reasoning
                    entry.update(m["raw"])
                wire.append(entry)
            elif role == "tool_results":
                for r in m["results"]:
                    wire.append({"content": r["content"], "role": "tool",
                                 "tool_call_id": r["id"]})
                if m.get("note"):
                    wire.append({"content": m["note"], "role": "user"})
        wire_tools = [{"function": {"description": t.get("description", ""),
                                    "name": t["name"],
                                    "parameters": t["input_schema"]},
                       "type": "function"} for t in tools]
        req = {"max_completion_tokens": max_tokens, "messages": wire,
               "model": self.model, "tools": wire_tools}
        # lazy so build_request stays pure for __new__-built test instances
        rp = getattr(self, "_reasoning_params", None)
        if rp is None:
            rp = _compat_reasoning(self.model)[0]
        req.update(rp)
        # OpenAI reasoning families (gpt-5*, o*) accept only the default
        # temperature; sending 0 is a hard 400. Models we explicitly run with
        # reasoning knobs also keep the server-default temperature — vendors
        # (DeepSeek, Moonshot, Meta) recommend ~1.0 with thinking on, and a
        # forced 0 degrades or collapses the reasoning trace.
        if not rp and not self.model.lower().startswith(
                ("gpt-5", "o1", "o3", "o4")):
            req["temperature"] = temperature
        return req

    def _streamed_create(self, req: dict):
        """Streaming fallback for servers that require stream=true (e.g. some
        Together-served models). Accumulates deltas into an object shaped like
        a non-streaming ChatCompletion so _parse stays oblivious."""
        from types import SimpleNamespace as NS
        req = dict(req)
        req["stream"] = True
        req["stream_options"] = {"include_usage": True}
        content: list = []
        reasoning_accum: dict[str, list] = {}
        tool_accum: dict = {}
        usage = None
        for chunk in self._client.chat.completions.create(**req):
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                content.append(delta.content)
            for rk in ("reasoning", "reasoning_content"):
                rv = getattr(delta, rk, None)
                if rv:
                    reasoning_accum.setdefault(rk, []).append(rv)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = tool_accum.setdefault(
                    tc.index, {"id": None, "name": None, "args": []})
                if tc.id:
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"].append(fn.arguments)
        calls = [NS(id=s["id"] or f"call_{i}",
                    function=NS(name=s["name"], arguments="".join(s["args"])))
                 for i, s in sorted(tool_accum.items())]
        reasoning = {k: "".join(v) for k, v in reasoning_accum.items()}
        msg = NS(content="".join(content) or None, tool_calls=calls or None,
                 **reasoning)
        if usage is None:  # server sent no usage frame; zeros beat a crash
            usage = NS(prompt_tokens=0, completion_tokens=0,
                       prompt_tokens_details=None)
        return NS(choices=[NS(message=msg)], usage=usage)

    def _request(self, **kw):
        o = self._openai
        req = self.build_request(**kw)
        # apply learned server quirks; keys starting with "_" are internal
        req.update({k: v for k, v in self._learned.items()
                    if not k.startswith("_")})
        if self._learned.get("_no_temperature"):
            req.pop("temperature", None)
        if self._learned.get("_no_reasoning_param"):
            req.pop("extra_body", None)
            req.pop("reasoning_effort", None)
        # reasoning models burn output budget on hidden reasoning tokens; a
        # learned multiplier keeps the VISIBLE output budget meaningful.
        mult = self._learned.get("_max_tokens_mult")
        if mult:
            tk = ("max_completion_tokens" if "max_completion_tokens" in req
                  else "max_tokens")
            req[tk] = req[tk] * mult
        try:
            # learned-streaming early path must stay INSIDE the conversion
            # umbrella: a 429 here crashed Qwen runs twice (2026-07-14)
            if self._learned.get("_force_stream"):
                return self._streamed_create(req)
            try:
                return self._client.chat.completions.create(**req)
            except o.BadRequestError as e:
                msg = str(e)
                # some models only serve streaming responses
                if "streaming_required" in msg or '"stream"' in msg:
                    resp = self._streamed_create(req)
                    self._learned["_force_stream"] = True
                    return resp
                # reasoning ate the whole output budget before any visible
                # content: scale the budget (x4, capped x16) and remember it
                if "max_tokens or model output limit" in msg:
                    prev = self._learned.get("_max_tokens_mult", 1)
                    if prev < 16:
                        self._learned["_max_tokens_mult"] = prev * 4
                        tk = ("max_completion_tokens"
                              if "max_completion_tokens" in req
                              else "max_tokens")
                        req[tk] = req[tk] * 4
                        return self._client.chat.completions.create(**req)
                # older servers want max_tokens instead
                if "max_completion_tokens" in msg:
                    req["max_tokens"] = req.pop("max_completion_tokens")
                    return self._client.chat.completions.create(**req)
                # some families reject any explicit temperature — remember it
                # so every later call skips the wasted 400 round-trip
                if "temperature" in msg and "temperature" in req:
                    req.pop("temperature")
                    self._learned["_no_temperature"] = True
                    return self._client.chat.completions.create(**req)
                # a server rejected our explicit reasoning knob (Together
                # hybrids / Meta): drop it, remember, re-stamp — reasoning
                # stays at the serving default (documented ON for these seats)
                if "reasoning" in msg \
                        and getattr(self, "_reasoning_params", None) \
                        and ("extra_body" in req or "reasoning_effort" in req):
                    req.pop("extra_body", None)
                    req.pop("reasoning_effort", None)
                    self._learned["_no_reasoning_param"] = True
                    self.adapter_name = "openai-chat:reasoning-default-healed"
                    return self._client.chat.completions.create(**req)
                # gpt-5.6 chat.completions rejects function tools while
                # reasoning is active; probe the ladder of efforts once and
                # remember what the server accepts (Responses-API migration is
                # the long-term fix for reasoning+tools on these models).
                if "reasoning" in msg or "Function tools" in msg:
                    for effort in ("none", "minimal", "low"):
                        try:
                            req2 = dict(req)
                            req2["reasoning_effort"] = effort
                            resp = self._client.chat.completions.create(**req2)
                            self._learned["reasoning_effort"] = effort
                            return resp
                        except o.BadRequestError:
                            continue
                raise
        except o.RateLimitError as e:
            if "insufficient_quota" in str(e):
                # out of credit: no amount of retrying will help
                raise FatalProviderError(str(e)) from e
            raise RetryableProviderError(str(e)) from e
        except o.APIConnectionError as e:
            if isinstance(e, o.APITimeoutError):
                raise ProviderTimeoutError(f"timeout: {e}") from e
            raise RetryableProviderError(f"connection: {e}") from e
        except o.APIStatusError as e:
            if getattr(e, "status_code", 0) >= 500:
                raise RetryableProviderError(f"status {e.status_code}") from e
            if _is_content_filter(str(e)):
                # a content-moderation false positive (Together/Alibaba
                # data_inspection_failed on the game-state text) is
                # request-specific, not a dead provider: skip THIS stop and
                # play on. Classifying it fatal permanently idled a seat,
                # which then got board-fired to death (Qwen in the arena).
                raise ContentPolicyError(f"content-filter: {e}") from e
            if getattr(e, "status_code", 0) in (401, 403):
                raise FatalProviderError(f"auth: {e}") from e
            raise RetryableProviderError(f"status: {e}") from e

    def _parse(self, resp) -> LLMResponse:
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        u = resp.usage
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        usage = Usage(input_tokens=(u.prompt_tokens or 0) - cached,
                      output_tokens=u.completion_tokens or 0,
                      cache_read_tokens=cached, cache_write_tokens=0)
        # raw = the reasoning trace (if any), keyed as the server returned it,
        # so build_request can echo it on the assistant turn of the tool loop
        raw = None
        for rk in ("reasoning", "reasoning_content"):
            rv = getattr(msg, rk, None)
            if isinstance(rv, str) and rv:
                raw = {rk: rv}
                break
        return LLMResponse(text=msg.content, tool_calls=calls, usage=usage,
                           raw=raw)


# --- OpenAI Responses API (gpt-5.6+: reasoning + function tools together) ---------

def _responses_reasoning_effort() -> str:
    """Default reasoning effort for the Responses adapter. 'medium' is the
    server default for the gpt-5 family; overridable per-invocation without
    code changes (recorded in the adapter_name stamp for comparability)."""
    return os.environ.get("FMBENCH_OPENAI_REASONING_EFFORT", "medium")


class OpenAIResponsesAdapter(ProviderAdapter):
    """OpenAI Responses API (client.responses.create) for reasoning models.

    Why it exists: gpt-5.6 on chat.completions rejects function tools while
    reasoning is active, so OpenAICompatAdapter self-heals those runs down to
    reasoning_effort='none' — the benchmark then measures the model WITHOUT
    its reasoning. The Responses API supports reasoning + function tools
    together.

    Stateless replay design (mirrors GoogleAdapter's raw-parts echo): every
    request sends store=False + include=['reasoning.encrypted_content']; the
    response's raw output items (reasoning / message / function_call) are
    stored on LLMResponse.raw and replayed VERBATIM on later turns of the
    same stop. Dropping the reasoning items (or their encrypted_content)
    breaks the tool loop: the server can no longer bind the function_call to
    the reasoning that produced it.
    """

    provider = "openai"
    adapter_name = "openai-responses"

    #: reasoning families reject explicit sampling params (same rule the
    #: compat adapter applies on chat.completions); muse-spark is served via
    #: this adapter with reasoning always on, so it keeps the default too
    _NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4", "muse")

    def __init__(self, *args, base_url: str | None = None,
                 reasoning_effort: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            import openai
        except ImportError as e:
            raise FatalProviderError(
                "openai package not installed — `pip install openai` to run "
                "gpt-* models") from e
        self._openai = openai
        self._client = openai.OpenAI(api_key=self.api_key, base_url=base_url,
                                     timeout=self.timeout_s, max_retries=0)
        self._reasoning_effort = reasoning_effort \
            or _responses_reasoning_effort()
        # effort in the stamp: an effort override changes what was measured
        # just as much as the wire path does
        self.adapter_name = f"openai-responses:{self._reasoning_effort}"
        # server quirks learned from rejections; internal keys start with "_"
        self._learned: dict = {}

    # -- wire building (pure; unit-tested without network) -----------------------

    def build_request(self, *, system, messages, tools, max_tokens,
                      temperature) -> dict:
        items: list = []
        for m in messages:
            role = m["role"]
            if role == "user":
                items.append({"content": m["text"], "role": "user"})
            elif role == "assistant":
                if m.get("raw") is not None:
                    # replay the model's own output items verbatim, including
                    # reasoning items (+ encrypted_content): required so the
                    # server can pair each function_call with its reasoning
                    items.extend(m["raw"])
                    continue
                if m.get("text"):
                    items.append({"content": m["text"], "role": "assistant"})
                for tc in m.get("tool_calls", []):
                    items.append({"arguments": json.dumps(tc["args"],
                                                          sort_keys=True),
                                  "call_id": tc["id"], "name": tc["name"],
                                  "type": "function_call"})
            elif role == "tool_results":
                for r in m["results"]:
                    items.append({"call_id": r["id"], "output": r["content"],
                                  "type": "function_call_output"})
                if m.get("note"):
                    items.append({"content": m["note"], "role": "user"})
            else:
                raise ValueError(f"unknown role {role!r}")
        # Responses tools are FLAT (no nested "function" wrapper)
        wire_tools = [{"description": t.get("description", ""),
                       "name": t["name"], "parameters": t["input_schema"],
                       "strict": False, "type": "function"} for t in tools]
        effort = getattr(self, "_reasoning_effort", None) \
            or _responses_reasoning_effort()
        req = {"include": ["reasoning.encrypted_content"],
               "input": items, "instructions": system,
               "max_output_tokens": max_tokens, "model": self.model,
               "reasoning": {"effort": effort}, "store": False,
               "tools": wire_tools}
        if not self.model.lower().startswith(self._NO_TEMPERATURE_PREFIXES):
            req["temperature"] = temperature
        return req

    # -- request/parse ------------------------------------------------------------

    @staticmethod
    def _has_visible_output(resp) -> bool:
        for item in resp.output or []:
            t = getattr(item, "type", None)
            if t == "function_call":
                return True
            if t == "message":
                for c in getattr(item, "content", None) or []:
                    if getattr(c, "text", None):
                        return True
        return False

    def _create_with_budget_heal(self, req: dict):
        """Reasoning burns max_output_tokens before any visible content; when
        a response comes back incomplete with nothing usable, scale the budget
        (x4, capped x16 like the compat adapter) and remember the multiplier."""
        resp = self._client.responses.create(**req)
        if (getattr(resp, "status", None) == "incomplete"
                and not self._has_visible_output(resp)):
            prev = self._learned.get("_max_tokens_mult", 1)
            if prev < 16:
                self._learned["_max_tokens_mult"] = prev * 4
                req["max_output_tokens"] = req["max_output_tokens"] * 4
                resp = self._client.responses.create(**req)
        return resp

    def _request(self, **kw):
        o = self._openai
        req = self.build_request(**kw)
        if self._learned.get("_no_temperature"):
            req.pop("temperature", None)
        if self._learned.get("_no_encrypted_reasoning"):
            req.pop("include", None)
            req["store"] = True  # server-side items keep the echo resolvable
        mult = self._learned.get("_max_tokens_mult")
        if mult:
            req["max_output_tokens"] = req["max_output_tokens"] * mult
        try:
            try:
                return self._create_with_budget_heal(req)
            except o.BadRequestError as e:
                msg = str(e)
                # some families reject any explicit temperature — remember it
                if "temperature" in msg and "temperature" in req:
                    req.pop("temperature")
                    self._learned["_no_temperature"] = True
                    return self._create_with_budget_heal(req)
                # org/endpoint without encrypted reasoning support: fall back
                # to stored responses so the verbatim item echo still resolves
                if "encrypted_content" in msg and "include" in req:
                    req.pop("include")
                    req["store"] = True
                    self._learned["_no_encrypted_reasoning"] = True
                    return self._create_with_budget_heal(req)
                raise FatalProviderError(msg) from e
        except o.RateLimitError as e:
            if "insufficient_quota" in str(e):
                # out of credit: no amount of retrying will help
                raise FatalProviderError(str(e)) from e
            raise RetryableProviderError(str(e),
                                         retry_after_s=_retry_after(e)) from e
        except o.APIConnectionError as e:
            if isinstance(e, o.APITimeoutError):
                raise ProviderTimeoutError(f"timeout: {e}") from e
            raise RetryableProviderError(f"connection: {e}") from e
        except o.APIStatusError as e:
            if getattr(e, "status_code", 0) >= 500:
                raise RetryableProviderError(f"status {e.status_code}") from e
            if _is_content_filter(str(e)):
                raise ContentPolicyError(f"content-filter: {e}") from e
            if getattr(e, "status_code", 0) in (401, 403):
                raise FatalProviderError(f"auth: {e}") from e
            raise RetryableProviderError(f"status: {e}") from e

    def _parse(self, resp) -> LLMResponse:
        text_parts, calls = [], []
        for item in resp.output or []:
            t = getattr(item, "type", None)
            if t == "message":
                for c in getattr(item, "content", None) or []:
                    if getattr(c, "type", None) == "output_text" and c.text:
                        text_parts.append(c.text)
            elif t == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=item.call_id, name=item.name,
                                      args=args))
        u = resp.usage
        cached = 0
        if u is not None:
            details = getattr(u, "input_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            usage = Usage(input_tokens=(u.input_tokens or 0) - cached,
                          output_tokens=u.output_tokens or 0,
                          cache_read_tokens=cached, cache_write_tokens=0)
        else:
            usage = Usage()
        # raw = the response's output items dumped verbatim (reasoning items
        # keep their encrypted_content) so build_request can echo them
        return LLMResponse(text="\n".join(text_parts) or None,
                           tool_calls=calls, usage=usage,
                           raw=_dump_output_items(resp.output))


def _dump_output_items(output) -> list | None:
    """SDK output items -> plain dicts resendable verbatim as input items."""
    if not output:
        return None
    items = []
    for item in output:
        if hasattr(item, "model_dump"):
            items.append(item.model_dump(exclude_none=True, mode="json"))
        elif isinstance(item, dict):
            items.append(dict(item))
        else:  # test fakes (SimpleNamespace)
            items.append(dict(vars(item)))
    return items


# --- Google (Gemini, REST via stdlib) ---------------------------------------------

# JSON-schema keys Gemini's function declarations accept; everything else
# (additionalProperties, $schema, strict, ...) is rejected by the API.
_GEMINI_SCHEMA_KEYS = frozenset({
    "anyOf", "description", "enum", "format", "items", "maximum", "minimum",
    "nullable", "properties", "required", "type"})


def _gemini_schema(schema: object) -> object:
    """Recursively strip JSON-schema fields Gemini rejects."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items()
                if k in _GEMINI_SCHEMA_KEYS or k not in
                ("additionalProperties", "$schema", "strict", "title",
                 "default", "examples")}
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    return schema


def _gemini_thinking_level(model: str) -> str | None:
    """Gemini 3.x thinking is ALWAYS ON (cannot be disabled); the knob is
    generationConfig.thinkingConfig.thinkingLevel. We pin the documented
    server default explicitly so the measured configuration can't drift when
    Google changes defaults: gemini-3.5-* defaults to "medium", gemini-3-*
    (incl. -preview) to "high". Overridable per-invocation via env (recorded
    in the adapter_name stamp for comparability). 2.x and older models keep
    the previous implicit behavior (returns None -> no thinkingConfig sent).
    """
    m = model.lower()
    if m.startswith("gemini-3.5"):
        default = "medium"
    elif m.startswith("gemini-3"):
        default = "high"
    else:
        return None
    return os.environ.get("FMBENCH_GEMINI_THINKING_LEVEL", default)


class GoogleAdapter(ProviderAdapter):
    """Gemini generateContent over REST (urllib; no SDK dependency).

    Gemini quirks handled here:
    - tool results are matched by function NAME, not call id, and responses
      carry no ids — synthetic ids (g0, g1, ...) are minted per response and
      an id->name map is rebuilt from prior assistant turns when wiring
      functionResponse parts;
    - thinking tokens are billed as output and reported separately
      (thoughtsTokenCount) — folded into output_tokens;
    - implicit caching reports cachedContentTokenCount, billed at the
      cache-read rate.
    """

    provider = "google"
    adapter_name = "google-genai"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        level = _gemini_thinking_level(self.model)
        if level is not None:
            # thinking level in the stamp, same rule as the other adapters
            self.adapter_name = f"google-genai:thinking-{level}"

    # -- wire building (pure; unit-tested without network) -----------------------

    #: Google's documented bypass value for functionCall parts that carry no
    #: model-generated thoughtSignature (custom/transplanted history). Gemini
    #: 3 enforces a signature on EVERY functionCall part; a turn truncated by
    #: max_tokens can emit a functionCall WITHOUT one, so even verbatim echo
    #: 400s (2026-07-22 arena incident, gemini-3-flash misclassified FATAL).
    _DUMMY_SIG = "context_engineering_is_the_way_to_go"

    @classmethod
    def _ensure_sig(cls, part: dict) -> dict:
        if "functionCall" in part and "thoughtSignature" not in part:
            return {**part, "thoughtSignature": cls._DUMMY_SIG}
        return part

    def build_request(self, *, system, messages, tools, max_tokens,
                      temperature) -> dict:
        id_to_name: dict[str, str] = {}
        contents = []
        for m in messages:
            role = m["role"]
            if role == "user":
                contents.append({"parts": [{"text": m["text"]}],
                                 "role": "user"})
            elif role == "assistant":
                for tc in m.get("tool_calls", []):
                    id_to_name[tc["id"]] = tc["name"]
                if m.get("raw") is not None:
                    # replay the model's own parts verbatim — Gemini 3.x
                    # functionCall parts carry a thoughtSignature that MUST be
                    # echoed back or the API 400s (found live, 20y pilot).
                    # _ensure_sig covers truncated turns whose functionCall
                    # never got a signature (verbatim echo still 400s).
                    contents.append({"parts": [self._ensure_sig(p)
                                               for p in m["raw"]],
                                     "role": "model"})
                    continue
                parts = []
                if m.get("text"):
                    parts.append({"text": m["text"]})
                for tc in m.get("tool_calls", []):
                    parts.append(self._ensure_sig(
                        {"functionCall": {"args": tc["args"],
                                          "name": tc["name"]}}))
                contents.append({"parts": parts or [{"text": ""}],
                                 "role": "model"})
            elif role == "tool_results":
                parts = []
                for r in m["results"]:
                    parts.append({"functionResponse": {
                        "name": id_to_name.get(r["id"], r["id"]),
                        "response": {"content": r["content"]}}})
                if m.get("note"):
                    parts.append({"text": m["note"]})
                contents.append({"parts": parts, "role": "user"})
            else:
                raise ValueError(f"unknown role {role!r}")
        gen_cfg: dict = {"maxOutputTokens": max_tokens}
        level = _gemini_thinking_level(self.model)
        if level is not None:
            # Gemini 3.x: pin the thinking level explicitly (no default drift)
            # and DO NOT send a low temperature — Google's docs recommend
            # keeping the default (1.0) on Gemini 3; lower values degrade
            # reasoning and can cause looping.
            gen_cfg["thinkingConfig"] = {"thinkingLevel": level}
        else:
            gen_cfg["temperature"] = temperature
        req = {"contents": contents,
               "generationConfig": gen_cfg,
               "systemInstruction": {"parts": [{"text": system}]}}
        if tools:
            req["tools"] = [{"functionDeclarations": [
                {"description": t.get("description", ""), "name": t["name"],
                 "parameters": _gemini_schema(t["input_schema"])}
                for t in tools]}]
        return req

    @staticmethod
    def _map_http_error(status: int, retry_after: str | None,
                        body_snippet: str) -> Exception:
        if status == 429:
            after = None
            try:
                after = float(retry_after) if retry_after else None
            except ValueError:
                pass
            return RetryableProviderError(f"gemini 429: {body_snippet}",
                                          retry_after_s=after)
        if status >= 500:
            return RetryableProviderError(f"gemini {status}: {body_snippet}")
        if "thought_signature" in body_snippet:
            # Gemini 3 signature-echo enforcement (2026-07-22 incident): this
            # is OUR wire bug, never a dead account. Fatal here silently idles
            # a healthy seat (contamination); retryable means worst case is
            # crash -> lossless resume -> sentinel pages the owner.
            return RetryableProviderError(f"gemini {status}: {body_snippet}")
        return FatalProviderError(f"gemini {status}: {body_snippet}")

    # -- request/parse ------------------------------------------------------------

    def _request(self, **kw):
        import urllib.error
        import urllib.request
        url = f"{self.BASE_URL}/models/{self.model}:generateContent"
        payload = json.dumps(self.build_request(**kw)).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key or ""})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise self._map_http_error(
                e.code, e.headers.get("retry-after"), body) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if isinstance(e, TimeoutError) or isinstance(
                    getattr(e, "reason", None), TimeoutError):
                raise ProviderTimeoutError(f"gemini timeout: {e}") from e
            raise RetryableProviderError(f"gemini connection: {e}") from e

    def _parse(self, raw: dict) -> LLMResponse:
        candidates = raw.get("candidates") or []
        parts = []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts, calls = [], []
        for part in parts:
            if part.get("text") and not part.get("thought"):
                text_parts.append(part["text"])
            fc = part.get("functionCall")
            if fc:
                calls.append(ToolCall(id=f"g{len(calls)}", name=fc["name"],
                                      args=dict(fc.get("args") or {})))
        um = raw.get("usageMetadata") or {}
        cached = um.get("cachedContentTokenCount", 0) or 0
        usage = Usage(
            input_tokens=max((um.get("promptTokenCount", 0) or 0) - cached, 0),
            output_tokens=((um.get("candidatesTokenCount", 0) or 0)
                           + (um.get("thoughtsTokenCount", 0) or 0)),
            cache_read_tokens=cached, cache_write_tokens=0)
        # raw = the model's parts verbatim so build_request can echo them
        # (incl. Gemini 3.x thoughtSignature on functionCall parts)
        return LLMResponse(text="\n".join(text_parts) or None,
                           tool_calls=calls, usage=usage, raw=parts or None)


# --- routing ----------------------------------------------------------------------

TOGETHER_BASE_URL = "https://api.together.ai/v1"  # OpenAI-compatible endpoint
META_BASE_URL = "https://api.meta.ai/v1"  # Meta AI API, OpenAI-compatible
# (successor to api.llama.com, which was wound down 2026-07-06; verified live
# 2026-07-14 — /models lists muse-spark-1.1, chat.completions face works)
XAI_BASE_URL = "https://api.x.ai/v1"                # xAI Grok, OpenAI-compatible

# Model families that need the Responses API for reasoning + function tools
# (chat.completions rejects the combination and the compat adapter self-heals
# down to reasoning_effort='none' — a validity problem, not just a quirk).
_OPENAI_RESPONSES_PREFIXES = ("gpt-5.6",)


def _openai_use_responses(model: str) -> bool:
    """gpt-5.6* routes to the Responses adapter unless FMBENCH_OPENAI_CHAT=1
    forces the legacy chat.completions path (comparability escape hatch: old
    results were produced with reasoning silently disabled)."""
    if os.environ.get("FMBENCH_OPENAI_CHAT") == "1":
        return False
    return model.lower().startswith(_OPENAI_RESPONSES_PREFIXES)


def _meta_use_responses(model: str) -> bool:
    """muse-spark* routes to the Responses adapter on api.meta.ai (reasoning
    replay across tool turns, documented effort knob) unless FMBENCH_META_CHAT=1
    forces the legacy chat.completions path (comparability escape hatch)."""
    if os.environ.get("FMBENCH_META_CHAT") == "1":
        return False
    return model.lower().startswith("muse")


def _compat_adapter_name(model: str) -> str:
    params, suffix = _compat_reasoning(model)
    del params
    return (f"{OpenAICompatAdapter.adapter_name}:{suffix}" if suffix
            else OpenAICompatAdapter.adapter_name)


def adapter_name_for_model(model: str, base_url: str | None = None) -> str:
    """Which wire path make_adapter would pick — stamped into manifests and
    results summaries so consumers can distinguish reasoning-capable runs
    from reasoning-disabled/legacy runs without constructing an adapter (no
    key needed). The suffix after ':' is the effective reasoning setting."""
    if base_url is not None:
        return _compat_adapter_name(model)
    if model.startswith("claude"):
        return (f"{AnthropicAdapter.adapter_name}:"
                f"{_anthropic_thinking_mode(model)}")
    if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        if _openai_use_responses(model):
            return (f"{OpenAIResponsesAdapter.adapter_name}:"
                    f"{_responses_reasoning_effort()}")
        return OpenAICompatAdapter.adapter_name
    if model.startswith("gemini"):
        level = _gemini_thinking_level(model)
        return (f"{GoogleAdapter.adapter_name}:thinking-{level}"
                if level else GoogleAdapter.adapter_name)
    if model.lower().startswith("muse") and _meta_use_responses(model):
        return (f"{OpenAIResponsesAdapter.adapter_name}:"
                f"{_meta_reasoning_effort()}")
    if "/" in model or model.lower().startswith(("llama", "muse", "grok")):
        return _compat_adapter_name(model)
    return "unknown"


def make_adapter(model: str, api_key: str | None = None,
                 base_url: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S,
                 retry: RetryPolicy | None = None,
                 rate_limiter=None) -> ProviderAdapter:
    kw = {"retry": retry, "rate_limiter": rate_limiter, "timeout_s": timeout_s}
    if base_url is not None:
        return OpenAICompatAdapter(model, api_key, base_url=base_url, **kw)
    if model.startswith("claude"):
        return AnthropicAdapter(model, api_key, **kw)
    if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        if _openai_use_responses(model):
            return OpenAIResponsesAdapter(model, api_key, **kw)
        return OpenAICompatAdapter(model, api_key, **kw)
    if model.startswith("gemini"):
        return GoogleAdapter(model, api_key, **kw)
    if "/" in model:  # Together (OpenAI-compatible) namespaced id, e.g. deepseek-ai/…
        return OpenAICompatAdapter(model, api_key,
                                   base_url=TOGETHER_BASE_URL, **kw)
    if model.lower().startswith(("llama", "muse")):
        # Meta AI API (Muse Spark / Llama). The server id is lowercase
        # (muse-spark-1.1); normalize so roster casing never 404s. Muse is a
        # reasoning model: route it through the Responses adapter (api.meta.ai
        # serves /v1/responses) so reasoning items replay across tool turns
        # and the documented effort knob applies.
        served = model.lower()
        if _meta_use_responses(served):
            return OpenAIResponsesAdapter(
                served, api_key, base_url=META_BASE_URL,
                reasoning_effort=_meta_reasoning_effort(), **kw)
        return OpenAICompatAdapter(served, api_key,
                                   base_url=META_BASE_URL, **kw)
    if model.lower().startswith("grok"):   # xAI (OpenAI-compatible)
        return OpenAICompatAdapter(model, api_key,
                                   base_url=XAI_BASE_URL, **kw)
    raise FatalProviderError(
        f"cannot route model {model!r}: pass base_url= for OpenAI-compatible "
        "servers, or use a claude-*/gpt-* model id")
