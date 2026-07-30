"""LLM runner: one fresh conversation per decision stop (DESIGN §11.2).

Provider-agnostic: the model plays through runner.providers adapters against
Game.call_tool (or a league SeatHandle). The `advance` tool ends the stop;
the outer loop performs the actual advance.

Spend-cap semantics: the cap is checked AFTER a response's tool calls have
been executed and logged, so a cap hit never drops committed work — the stop
finishes cleanly, then BudgetExceeded aborts the run and the game settles.
"""

from __future__ import annotations

from engine.actions.commands import TOOLS, tool_schemas
from engine.actions.validate import ValidationError
from runner.costs import BudgetExceeded, CostTracker, RunSuspended
from runner.memory import DEFAULT_RETRIEVAL_K, retrieve_notebook
from runner.providers import make_adapter
from runner.render import (SOFT_REMINDER, render_packet, render_tool_result,
                           system_text)

# Harness limits. These are DEFAULTS: they can be overridden per-run (CLI /
# run_benchmark / tier preset) so the "final"/official harness can run them
# higher without handicapping the model. Whatever value an official tier uses
# must stay fixed + documented — the harness is frozen, not per-model tuned.
DEFAULT_SOFT_TURN_LIMIT = 25   # round at which the "converge" reminder fires
DEFAULT_HARD_TURN_LIMIT = 40   # hard cap on rounds per decision stop (backstop)
DEFAULT_MAX_TOKENS = 1500      # output-token cap per LLM call
DRAFT_STOP_MIN_TOKENS = 6000   # per-call floor on the one-time DRAFT stop
EMPTY_TURN_MAX_TOKENS = 32000  # escalation ceiling for dead-turn retries


def _dead_turn(resp) -> bool:
    """No tool calls and no usable text. Bare/unclosed think-tags count as
    dead: some Together-served models leak reasoning as literal '<think>'
    text and truncate to exactly that when the budget runs out (Qwen3.7-Max
    on the 50-card draft stop) — non-empty text, zero content."""
    if resp.tool_calls:
        return False
    t = (resp.text or "").replace("<think>", "").replace("</think>", "")
    return not t.strip()

# Back-compat aliases (older imports / tests may reference these names).
SOFT_TURN_LIMIT = DEFAULT_SOFT_TURN_LIMIT
HARD_TURN_LIMIT = DEFAULT_HARD_TURN_LIMIT
MAX_TOKENS = DEFAULT_MAX_TOKENS

# runner-level ablation switches -> tools hidden from the schema AND blocked
# if called anyway ("no-insolvency-warnings" is engine-level, see Game)
ABLATION_HIDDEN_TOOLS = {
    "no-notebook": ("append_note", "rewrite_notes"),
    "no-history": ("get_history",),
}


class LLMAgent:
    def __init__(self, model: str, api_key: str,
                 spend_cap_usd: float | None = None, verbose: bool = True,
                 base_url: str | None = None, rate_limiter=None,
                 recorder=None, adapter=None, prior_spend_usd: float = 0.0,
                 ablations: list[str] | None = None,
                 max_tokens: int | None = None,
                 soft_turn_limit: int | None = None,
                 hard_turn_limit: int | None = None,
                 memory_mode: str = "flat", spend_pool=None,
                 cap_mode: str = "settle"):
        self.adapter = adapter or make_adapter(
            model, api_key=api_key, base_url=base_url,
            rate_limiter=rate_limiter)
        self.model = model
        self.cost = CostTracker(model, spend_cap_usd,
                                prior_usd=prior_spend_usd, pool=spend_pool)
        if self.cost.price_fallback:
            # unknown model -> conservative fallback billing; caps lose accuracy
            print(f"[runner] WARNING: no price entry for {model!r} — tracking "
                  "at conservative fallback rates; spend-cap accuracy degraded. "
                  "Pin the model in runner/costs.py PRICES before real runs.")
        self.verbose = verbose
        self.max_tokens = max_tokens or DEFAULT_MAX_TOKENS
        self.soft_turn_limit = soft_turn_limit or DEFAULT_SOFT_TURN_LIMIT
        self.hard_turn_limit = hard_turn_limit or DEFAULT_HARD_TURN_LIMIT
        self.memory_mode = memory_mode or "flat"
        self.cap_mode = cap_mode  # "settle": cap trip = final early settle;
        # "suspend": cap trip = pause unscored, resumable with a topped-up cap
        self.recorder = recorder
        self.ablations = tuple(sorted(ablations or ()))
        self.hidden_tools = frozenset(
            name for a in self.ablations
            for name in ABLATION_HIDDEN_TOOLS.get(a, ()))
        self.tools = [t for t in tool_schemas()
                      if t["name"] not in self.hidden_tools]
        self.system = system_text()
        if self.ablations:
            # stable within the run, so prompt caching is unaffected
            self.system += ("\n\n[Ablation notice: this evaluation variant "
                            f"disables: {', '.join(self.ablations)}. Tools "
                            "removed from the list above are unavailable.]")
        if "no-notebook" in self.ablations:
            # retrieval memory is meaningless without a notebook; silently fall
            # back to flat so the ablation prompt isn't contaminated by a
            # notice about a notebook that doesn't exist.
            self.memory_mode = "flat"
        if self.memory_mode == "retrieval":
            # stable within the run -> prompt caching unaffected. Tells the
            # model how its notebook is surfaced so it can tag notes usefully.
            self.system += (
                "\n\n[Memory notice: your notebook is shown relevance-ordered "
                "for the current situation — the notes most relevant to this "
                "stop appear first, the rest below. Nothing is deleted. Tag "
                "notes with #hashtags (e.g. '#finance #board ...') to make "
                "them easy to surface when that topic is live.]")
        self._draft_stop = False
        self._drafted_this_stop = False
        self.diagnostics = {"hard_cutoffs": 0, "implicit_advances": 0,
                            "stops": 0, "tool_errors": 0}
        self.error_codes: dict[str, int] = {}       # cumulative, per error code
        self.stop_error_codes: dict[str, int] = {}  # current stop only
        self.error_tools: dict[str, int] = {}       # cumulative, "tool.code"
        self.decision_log: list[dict] = []

    # -- one full game ---------------------------------------------------------

    def play(self, game, packet: dict | None = None) -> dict:
        """Play to settlement. packet: resume entry point (a live stop packet
        from a reconstructed Game); None starts a fresh run."""
        if packet is None:
            packet = game.start()
        cap_tripped = False
        refused = 0
        try:
            while not packet.get("game_over"):
                self.stop_error_codes = {}
                self.handle_stop(game, packet)
                stop_id = packet.get("stop_id")
                # advance FIRST, record stop_end only on success: a refused
                # advance must not leave a stop_end in the replay log (a
                # replayed stop_end triggers an advance at the same state and
                # would hit the identical refusal — resume would be bricked)
                try:
                    packet = game.advance()
                except ValidationError as e:
                    # the engine refuses to leave the stop (e.g. the v0.3
                    # draft needs a submission). Feed the reason back to the
                    # model; after 2 refusals invoke the harness
                    # anti-deadlock (DESIGN_v0.3 decision #15): an
                    # auto-filled draft submission, logged as a real action.
                    refused += 1
                    self.diagnostics["advance_refused"] = \
                        self.diagnostics.get("advance_refused", 0) + 1
                    if refused <= 2:
                        packet = {**packet, "harness_notice":
                                  f"advance refused: {e.hint}. Complete the "
                                  "required action, then call advance."}
                        continue
                    fill_args = {"picks": [], "auto_fill": True}
                    game.call_tool("submit_draft", fill_args)
                    if self.recorder is not None:
                        # the anti-deadlock submission must land in the
                        # replay-driving action log like any committed
                        # action, or resume replays brick on draft_pending
                        self.recorder.action(stop_id, "submit_draft",
                                             fill_args)
                    self.diagnostics["draft_autofilled"] = 1
                    packet = game.advance()
                refused = 0
                if self.recorder is not None:
                    self.recorder.stop_end(stop_id,
                                           self.cost.summary(),
                                           dict(self.diagnostics),
                                           error_codes=dict(sorted(
                                               self.stop_error_codes.items())))
        except BudgetExceeded as e:
            if self.cap_mode == "suspend":
                # pause WITHOUT settling: no score exists yet, so a budget
                # top-up + --resume continues the same world with zero waste
                if self.recorder is not None:
                    self.recorder.suspend(self.cost.summary())
                if self.verbose:
                    print(f"[runner] {e} — suspending unscored; top up the "
                          "cap and --resume to continue")
                raise RunSuspended(
                    str(e), run_id=getattr(self.recorder, "run_id", None),
                    spent_usd=self.cost.summary()["cost_usd"]) from e
            cap_tripped = True
            if self.verbose:
                print(f"[runner] {e} — settling run early")
        result = game.final_result()
        result["cost"] = self.cost.summary()
        if cap_tripped:
            result["aborted_by_spend_cap"] = True
        result["diagnostics"] = {**self.diagnostics,
                                 "error_codes": dict(sorted(
                                     self.error_codes.items())),
                                 "error_tools": dict(sorted(
                                     self.error_tools.items()))}
        if self.recorder is not None:
            self.recorder.finish(result)
        return result

    # -- one decision stop -----------------------------------------------------

    def _ensure_draft(self, game) -> None:
        """Guarantee a draft submission before leaving a DRAFT stop.

        Both solo and league route through handle_stop, so this belongs here
        (the league driver calls advance itself and never saw play()'s
        refusal nudge — 8/16 arena seats silently bot-drafted before this).
        One corrective nudge, then the DESIGN #15 anti-deadlock auto-fill,
        logged as a real action."""
        if self._drafted_this_stop:
            return
        fill = {"picks": [], "auto_fill": True}
        env = game.call_tool("submit_draft", fill)
        if env.get("ok"):
            self.diagnostics["draft_autofilled"] = \
                self.diagnostics.get("draft_autofilled", 0) + 1
            self._drafted_this_stop = True

    def handle_stop(self, game, packet: dict) -> None:
        self.diagnostics["stops"] += 1
        self._draft_stop = "DRAFT" in (packet.get("stop_types") or ())
        self._drafted_this_stop = False
        # cap check at stop ENTRY: a capped-out seat must not pay even one
        # more LLM call per remaining stop (matters in 20y league runs where
        # hundreds of stops can follow a seat's cap hit)
        self._check_cap()
        if "no-notebook" in self.ablations:
            packet = {k: v for k, v in packet.items() if k != "notebook"}
        elif self.memory_mode == "retrieval" and packet.get("notebook"):
            # runner-side view transform only; engine's world.notebook is
            # untouched, so replay stays bit-identical.
            packet = {**packet, "notebook": retrieve_notebook(
                packet["notebook"], packet, DEFAULT_RETRIEVAL_K)}
        messages: list[dict] = [{"role": "user",
                                 "text": render_packet(packet)}]
        for turn in range(self.hard_turn_limit):
            resp = self._create(messages)
            if self.recorder is not None:
                # full trajectory: the model's visible text + tool calls for
                # EVERY turn land in transcript.jsonl (actions.jsonl only has
                # committed actions; this preserves the reasoning narrative)
                self.recorder.turn(_stop_id(game), turn, resp.text,
                                   [{"args": dict(tc.args or {}),
                                     "name": tc.name}
                                    for tc in resp.tool_calls])
            if not resp.tool_calls:
                self.diagnostics["implicit_advances"] += 1
                if self._draft_stop:
                    self._ensure_draft(game)
                self._check_cap()
                return
            results, advanced = [], False
            for tc in resp.tool_calls:
                if tc.name == "advance":
                    advanced = True
                    results.append({"content":
                                    '{"advancing": true, "ok": true}',
                                    "id": tc.id})
                    continue
                if tc.name in self.hidden_tools:
                    env = {"ok": False, "error": {
                        "code": "tool_disabled_by_ablation",
                        "hint": "this tool is disabled in the current "
                                "evaluation variant"}}
                else:
                    env = game.call_tool(tc.name, dict(tc.args or {}))
                if tc.name == "submit_draft" and env.get("ok"):
                    self._drafted_this_stop = True
                if not env.get("ok"):
                    self.diagnostics["tool_errors"] += 1
                    code = (env.get("error") or {}).get("code", "unknown")
                    self.error_codes[code] = self.error_codes.get(code, 0) + 1
                    self.stop_error_codes[code] = \
                        self.stop_error_codes.get(code, 0) + 1
                    tk = f"{tc.name}.{code}"
                    self.error_tools[tk] = self.error_tools.get(tk, 0) + 1
                self._record(game, tc, env)
                results.append({"content": render_tool_result(env),
                                "id": tc.id})
            if advanced:
                if self._draft_stop and not self._drafted_this_stop:
                    # tried to leave the draft without a squad: nudge once,
                    # then (next advance) auto-fill in _ensure_draft
                    nud = self.diagnostics.get("draft_nudges", 0)
                    if nud < 1:
                        self.diagnostics["draft_nudges"] = nud + 1
                        messages.append({
                            "role": "assistant", "text": resp.text,
                            "tool_calls": [{"args": tc.args, "id": tc.id,
                                            "name": tc.name}
                                           for tc in resp.tool_calls],
                            "raw": resp.raw})
                        messages.append({"note": None, "role": "tool_results",
                            "results": [{"id": tc.id, "content":
                                '{"ok": false, "error": {"code": '
                                '"draft_pending", "hint": "you must call '
                                'submit_draft with your pick list before '
                                'advancing; the draft is your squad for the '
                                'whole run"}}'} for tc in resp.tool_calls
                                if tc.name == "advance"]})
                        continue
                    self._ensure_draft(game)
                self._check_cap()
                return
            messages.append({"role": "assistant", "text": resp.text,
                             "tool_calls": [{"args": tc.args, "id": tc.id,
                                             "name": tc.name}
                                            for tc in resp.tool_calls],
                             "raw": resp.raw})
            note = SOFT_REMINDER if turn == self.soft_turn_limit - 1 else None
            messages.append({"note": note, "results": results,
                             "role": "tool_results"})
            self._check_cap()
        if self._draft_stop:
            self._ensure_draft(game)  # never leave a draft stop unsubmitted
        self.diagnostics["hard_cutoffs"] += 1  # defaults apply; not penalized

    # -- internals ---------------------------------------------------------------

    def _check_cap(self) -> None:
        if self.cost.over_cap():
            raise BudgetExceeded(
                f"spend cap ${self.cost.spend_cap_usd:.2f} exceeded "
                f"at ${self.cost.total_usd():.2f} cumulative")

    def _record(self, game, tc, env) -> None:
        spec = TOOLS.get(tc.name)
        if spec is None:
            return
        if self.recorder is not None and env.get("ok") \
                and spec["kind"] in ("action", "note"):
            self.recorder.action(_stop_id(game), tc.name, dict(tc.args or {}))
        if spec["kind"] != "action" or tc.name in ("append_note",
                                                   "rewrite_notes"):
            return
        result = {k: v for k, v in (env.get("data") or {}).items()
                  if k not in ("lineup_set",)}
        if not env.get("ok") and env.get("error"):
            # keep the error code in the forensic record (round-3: the sonnet
            # autopsy had to reverse-engineer 202 blank error envelopes)
            result = {"error_code": env["error"].get("code")}
        self.decision_log.append({
            "args": dict(tc.args or {}),
            "ok": env.get("ok"),
            "result": result,
            "stop_id": _stop_id(game),
            "tool": tc.name,
        })

    def _create(self, messages):
        # Draft-stop floor: the one-time draft asks for a 50-card portfolio
        # decision + a long array tool call. Under a lean max_tokens
        # (default 1500) prose-planners truncate before calling submit_draft
        # and thinking models return empty turns — observed in the first
        # v0.3 shakedown (5/12 models silently auto-filled). One stop, so
        # the cost of the floor is cents; identical for every model.
        mt = self.max_tokens
        if self._draft_stop:
            mt = max(mt, DRAFT_STOP_MIN_TOKENS)
        for attempt in range(3):
            resp = self.adapter.complete(
                system=self.system, messages=messages, tools=self.tools,
                max_tokens=mt, temperature=0)
            self.cost.add(resp.usage)
            if not _dead_turn(resp) or attempt == 2:
                break
            # dead turn: HTTP-200 but neither text nor tool calls — thinking
            # models can silently exhaust the whole budget in reasoning (no
            # API error, so adapter self-healing never fires; observed on
            # Kimi-K2.6 at 8000 on the 50-card draft stop). Same messages,
            # 4x budget, deterministic at temperature 0.
            mt = min(mt * 4, EMPTY_TURN_MAX_TOKENS)
            self.diagnostics["empty_turn_retries"] = \
                self.diagnostics.get("empty_turn_retries", 0) + 1
        if self.verbose and self.cost.calls % 25 == 0:
            print(f"[runner] calls={self.cost.calls} "
                  f"spend=${self.cost.usd():.2f} "
                  f"total=${self.cost.total_usd():.2f} "
                  f"cache={self.cost.cache_hit_rate():.0%}")
        return resp


def _stop_id(game) -> str | None:
    return getattr(getattr(game, "world", None), "current_stop_id", None)
