"""Run persistence + resume for LLM benchmark runs.

Layout (results/runs/<run_id>/):
    manifest.json    run config + live cursor (stop count, spend) — atomic
    actions.jsonl    engine-mutating actions + stop_end markers, appended live
    stops.jsonl      per-stop structured log: calls, tokens, cost, errors

Resume contract: every record in actions.jsonl was a SUCCESSFUL engine action
(queries are pure and never logged). rebuild_game() re-applies them against a
fresh Game — deterministic engine ⇒ identical state — advancing exactly at
stop_end markers. A trailing stop without stop_end (the crash point) has its
committed actions re-applied too; the LLM then simply plays that stop again
from the current packet (its prior partial actions persist — honest, no
double-apply, no lost work).
"""

from __future__ import annotations

import json
import os
import pathlib
import time

RUNS_DIR = pathlib.Path(__file__).resolve().parent.parent / "results" / "runs"


class RunRecorder:
    def __init__(self, run_id: str, config: dict, resume: bool = False):
        self.run_id = run_id
        self.dir = RUNS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._actions_path = self.dir / "actions.jsonl"
        self._stops_path = self.dir / "stops.jsonl"
        self._manifest_path = self.dir / "manifest.json"
        if resume:
            self.manifest = json.loads(self._manifest_path.read_text())
            self.manifest["resumed_at"] = _now()
        else:
            from engine.core.version import stamp
            self.manifest = {**stamp(), "config": config, "created_at": _now(),
                             "run_id": run_id, "status": "running",
                             "stops_completed": 0}
        self.manifest["status"] = "running"
        self._write_manifest()
        self._prev_stop_calls = self.manifest.get("cost", {}).get("api_calls", 0)

    # -- LLMAgent hooks ------------------------------------------------------------

    def action(self, stop_id: str | None, tool: str, args: dict) -> None:
        self._append(self._actions_path,
                     {"args": args, "stop_id": stop_id, "tool": tool,
                      "type": "action"})

    def stop_end(self, stop_id: str | None, cost_summary: dict,
                 diagnostics: dict, error_codes: dict | None = None) -> None:
        self._append(self._actions_path,
                     {"stop_id": stop_id, "type": "stop_end"})
        self._append(self._stops_path, {
            "calls_cum": cost_summary["api_calls"],
            "calls_this_stop": cost_summary["api_calls"] - self._prev_stop_calls,
            "cache_hit_rate": cost_summary["cache_hit_rate"],
            "cost_usd_cum": cost_summary["cost_usd"],
            # per-code counts for THIS stop (autopsy observability gap)
            "error_codes": error_codes or {},
            "stop_id": stop_id,
            "tool_errors_cum": diagnostics["tool_errors"],
        })
        self._prev_stop_calls = cost_summary["api_calls"]
        self.manifest["stops_completed"] = \
            self.manifest.get("stops_completed", 0) + 1
        self.manifest["cost"] = cost_summary
        self._write_manifest()

    def turn(self, stop_id: str | None, turn: int, text: str | None,
             tool_calls: list[dict]) -> None:
        """Full-trajectory transcript: one line per LLM turn (visible text +
        tool calls). Separate file from actions.jsonl, which records only
        committed actions and drives replay; this one is for analysis."""
        self._append(self.dir / "transcript.jsonl",
                     {"stop_id": stop_id, "text": text,
                      "tool_calls": tool_calls, "turn": turn,
                      "type": "llm_turn"})

    def suspend(self, cost_summary: dict) -> None:
        """Cap trip in suspend mode: pause WITHOUT settling. The on-disk
        archive stays crash-identical (fully resumable); the distinct status
        tells tooling this run awaits a budget top-up, not a retry."""
        self.manifest["status"] = "suspended"
        self.manifest["cost"] = cost_summary
        self._write_manifest()

    def finish(self, result: dict) -> None:
        self.manifest["status"] = "finished"
        self.manifest["settle_reason"] = result.get("settle_reason")
        self.manifest["s_final"] = result.get("s_final")
        self.manifest["cost"] = result.get("cost", self.manifest.get("cost"))
        self._write_manifest()

    # -- io --------------------------------------------------------------------------

    def _append(self, path: pathlib.Path, record: dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _write_manifest(self) -> None:
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2, sort_keys=True))
        tmp.replace(self._manifest_path)


# --- resume ---------------------------------------------------------------------------


def load_run(run_id: str) -> tuple[dict, list[dict]]:
    d = RUNS_DIR / run_id
    manifest = json.loads((d / "manifest.json").read_text())
    records = []
    actions = d / "actions.jsonl"
    if actions.exists():
        with open(actions, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
    return manifest, records


def rebuild_game(manifest: dict, records: list[dict]):
    """Reconstruct a live Game positioned at the first unplayed stop.

    Returns (game, packet). packet is the stop the LLM should play next; if
    the game settled during reconstruction, packet["game_over"] is True.
    """
    from engine.game import Game

    cfg = manifest["config"]
    game = Game(seed=cfg["seed"], years=cfg["years"],
                player_club=cfg.get("club", "mid"),
                ablations=cfg.get("ablations"),
                # honor world/notebook/mode overrides so a non-default world
                # (e.g. Open Track 1x16) replays bit-identically instead of
                # silently reconstructing against the engine default.
                param_overrides=cfg.get("param_overrides"))
    packet = game.start()
    i = 0
    while i < len(records) and not packet.get("game_over"):
        rec = records[i]
        if rec["type"] == "stop_end":
            if rec.get("stop_id") not in (None, packet.get("stop_id")):
                raise RuntimeError(
                    f"resume mismatch: log stop {rec.get('stop_id')} vs live "
                    f"{packet.get('stop_id')} — engine/params changed?")
            packet = game.advance()
            i += 1
            continue
        # action record: fast-forward live stops that had no actions
        while (not packet.get("game_over")
               and rec["stop_id"] != packet.get("stop_id")):
            packet = game.advance()
        if packet.get("game_over"):
            break
        env = game.call_tool(rec["tool"], rec["args"])
        if not env.get("ok"):
            raise RuntimeError(
                f"resume replay failed at {rec}: {env.get('error')} — "
                "engine/params changed since the original run?")
        i += 1
    return game, packet


def new_run_id(agent: str, seed: int, model: str | None) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    # sanitize: namespaced ids (deepseek-ai/DeepSeek-V4-Pro) carry "/" and
    # dots — a raw slash nests the run directory and breaks globbing
    import re
    tag = re.sub(r"[^A-Za-z0-9_]", "", (model or agent).split("-2")[0])[:16]
    return f"{agent}_s{seed}_{tag}_{stamp}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
