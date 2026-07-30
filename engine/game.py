"""Game session: the engine-level API that runner / MCP / UI all wrap.

    game = Game(seed=42, years=5, player_club="mid")
    packet = game.start()
    game.call_tool("get_squad", {})
    packet = game.advance()          # or game.call_tool("advance", {})
    ...
    result = game.final_result()

All-AI world sim (no player club): Game(seed, years, player_club=None)
then game.simulate_headless().
"""

from __future__ import annotations

from engine.actions.commands import TOOLS
from engine.actions.validate import ValidationError, validate_args
from engine.core.params import apply_overrides, load_params
from engine.domain.worldgen import generate_world
from engine.persist.eventlog import EventLog
from engine.persist.snapshot import state_hash
from engine.sim import board
from engine.sim import stops as stops_mod
from engine.sim.tick import tick
from score.composite import compute as compute_score

MAX_DAYS_WITHOUT_STOP = 800  # hard safety against scheduler bugs


class Game:
    def __init__(self, seed: int, years: int = 5,
                 player_club: str | None = "mid",
                 params_path: str | None = None, record: bool = True,
                 ablations: list[str] | None = None,
                 param_overrides: dict | None = None):
        self.params = apply_overrides(load_params(params_path), param_overrides)
        self.world = generate_world(seed, self.params, player_club, years)
        # run config, not world state: never serialized, no hash impact when
        # empty. Engine-level switches only ("no-insolvency-warnings");
        # runner-level switches (notebook/history) never reach the engine.
        self.world.ablations = tuple(sorted(ablations or ()))
        self.world.event_log = EventLog()
        self.world.log_event("game_start", {
            "player_club": self.world.player_club_id, "seed": seed,
            "years": years})
        self.action_log: list[dict] = [] if record else None
        self.queries_this_stop = 0
        self.offers_this_stop = 0
        self.started = False
        # all-AI draft world (headless calibration): no seat will ever submit,
        # so resolve immediately — every club drafts by script. League Mode
        # uses its own driver and never passes through Game.
        if self.world.draft_open and self.world.player_club_id is None:
            from engine.sim import draft as draft_mod
            draft_mod.resolve(self.world)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> dict:
        if self.started:
            raise RuntimeError("game already started")
        self.started = True
        if self.world.player_club_id is None:
            raise RuntimeError("all-AI game: use simulate_headless()")
        if self.world.draft_open:
            # v0.3 draft phase: stop 0, before any day ticks. The agent
            # queries get_draft_pool, calls submit_draft, then advance.
            self.queries_this_stop = 0
            self.offers_this_stop = 0
            return stops_mod.build_packet(self.world, ["DRAFT"])
        board.opening_briefing(self.world)
        return self.advance(initial=True)

    def advance(self, initial: bool = False) -> dict:
        """Simulate until the next decision stop (or settlement)."""
        world = self.world
        if world.draft_open:
            from engine.actions.validate import ValidationError
            from engine.sim import draft as draft_mod
            if world.player_club_id not in world.draft_submissions:
                raise ValidationError(
                    "draft_pending",
                    "submit your draft picks (submit_draft) before advancing")
            draft_mod.resolve(world)
            board.opening_briefing(world)
        self.queries_this_stop = 0
        self.offers_this_stop = 0
        for _ in range(MAX_DAYS_WITHOUT_STOP):
            if world.settled:
                return self._game_over_packet()
            tick(world)
            if world.settled:
                return self._game_over_packet()
            types = stops_mod.due_stop_types(world)
            if types:
                return stops_mod.build_packet(world, types)
        raise RuntimeError("no decision stop reached — scheduler bug")

    def simulate_headless(self) -> dict:
        """All-AI world simulation for calibration; returns summary."""
        world = self.world
        seasons = []
        for _ in range(world.years * 360):
            tick(world)
            if world.date.day == world.params.world.season_settlement_day + 1:
                from engine.domain.league import league_table
                t1 = league_table(world.division_clubs(1))
                seasons.append({
                    "avg_ca": round(world.avg_league_ca(), 1),
                    "t1_champion": t1[0].name,
                    "t1_champion_pts": t1[0].points(),
                    "year": world.date.year,
                })
            if world.date.year > world.years:
                break
        return {"final_hash": state_hash(world), "seasons": seasons}

    # -- tool dispatch ----------------------------------------------------------

    def call_tool(self, name: str, args: dict | None = None) -> dict:
        args = args or {}
        world = self.world
        envelope_base = {
            "game_date": str(world.date),
            "stop_id": world.current_stop_id,
        }
        if world.settled and name != "advance":
            return {**envelope_base, "ok": False,
                    "error": {"code": "game_over",
                              "hint": "the run has been settled"}}
        spec = TOOLS.get(name)
        if spec is None:
            return {**envelope_base, "ok": False,
                    "error": {"code": "unknown_tool",
                              "hint": "no such tool"}}
        if name == "advance":
            stop_id_at_call = world.current_stop_id
            try:
                packet = self.advance()
            except ValidationError as e:
                return {**envelope_base, "ok": False,
                        "error": {"code": e.code, "hint": e.hint}}
            # logged only on success: a rejected advance (e.g. draft_pending)
            # must not enter the replay-driving action log
            if self.action_log is not None:
                self.action_log.append({"args": {}, "stop_id":
                                        stop_id_at_call, "tool": "advance"})
            return {**envelope_base, "data": packet, "ok": True,
                    "game_date": str(world.date)}
        if spec["kind"] == "query":
            budget = world.params.stops.query_budget
            if self.queries_this_stop >= budget:
                return {**envelope_base, "ok": False,
                        "error": {"code": "query_budget_exceeded",
                                  "hint": "no queries left this stop; the budget "
                                          "resets after advance"}}
            self.queries_this_stop += 1
        if name in ("make_transfer_offer", "respond_to_offer"):
            # offers are metered separately (X1/G3: no free probing oracle)
            budget = world.params.stops.offer_budget
            if self.offers_this_stop >= budget:
                return {**envelope_base, "ok": False,
                        "error": {"code": "offer_budget_exceeded",
                                  "hint": "no negotiation moves left this stop; "
                                          "the budget resets after advance"}}
            self.offers_this_stop += 1
        try:
            validate_args(spec["schema"], args)
            result = spec["handler"](world, args)
        except ValidationError as e:
            result = {"error": {"code": e.code, "hint": e.hint}, "ok": False}
        if spec["kind"] in ("action", "note") and result.get("ok"):
            if self.action_log is not None:
                self.action_log.append({"args": args, "stop_id":
                                        world.current_stop_id, "tool": name})
            world.recent_actions.append({
                "stop_id": world.current_stop_id, "summary": _summarize(name, args),
                "tool": name})
            world.recent_actions = world.recent_actions[-30:]
            world.log_event("action", {"args": args, "tool": name})
        return {**envelope_base, **result}

    # -- results ---------------------------------------------------------------

    def _game_over_packet(self) -> dict:
        result = self.final_result()
        return {"game_over": True, "result": result,
                "stop_id": self.world.current_stop_id}

    def final_result(self) -> dict:
        if not self.world.settled:
            # synthetic log record so abandoned runs replay to the same
            # settlement point (audit chain must cover early quits — X4/M3)
            if self.action_log is not None:
                self.action_log.append({"args": {}, "stop_id":
                                        self.world.current_stop_id,
                                        "tool": "abandon"})
            self.world.settle("abandoned")
        breakdown = compute_score(self.world)
        breakdown["event_chain_hash"] = (self.world.event_log.chain_hash()
                                         if self.world.event_log else None)
        breakdown["final_state_hash"] = state_hash(self.world)
        breakdown["seed"] = self.world.seed
        breakdown["stops_total"] = self.world.stop_counter
        return breakdown


def _summarize(name: str, args: dict) -> str:
    parts = [f"{k}={args[k]}" for k in sorted(args)][:3]
    return f"{name}({', '.join(parts)})"
