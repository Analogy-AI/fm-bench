"""Minimal web shell for FM Bench: play a single-player Benchmark Mode game
in the browser. Stdlib only — wraps one Game session behind a JSON API and
serves web/static/.

    .venv/bin/python run_web.py [--port 8777]

Endpoints:
    POST /api/new   {seed, years}          -> new game, returns state
    GET  /api/state                        -> current stop packet + header + budgets
    POST /api/tool  {name, args}           -> Game.call_tool (incl. advance)
    GET  /api/meta                         -> tool schemas (for form generation)

The UI is a client like any other: every read is observation-filtered and
every action goes through the same tool registry the LLM runner uses.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine.actions.commands import tool_schemas
from engine.game import Game
from engine.obs import observation as obs

STATIC_DIR = Path(__file__).parent / "web" / "static"
MIME = {".css": "text/css", ".html": "text/html", ".js": "text/javascript",
        ".svg": "image/svg+xml"}


class Session:
    """One game at a time, guarded by a lock (UI is single-player)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.game: Game | None = None
        self.packet: dict | None = None   # last stop packet
        self.result: dict | None = None   # settlement breakdown once over
        self.recorder = None              # RunRecorder — human runs are
        self.run_id: str | None = None    # recorded exactly like agent runs

    def new_game(self, seed: int, years: int) -> dict:
        # Human play writes the SAME crash-safe run artifacts as agent runs
        # (results/runs/<id>/: manifest + actions.jsonl) — replayable,
        # reconstructable via runner.manifest.rebuild_game, and usable as
        # human-baseline data. Every state-mutating action is persisted the
        # moment it succeeds.
        from runner.manifest import RunRecorder, new_run_id
        # close out a prior unfinished run before starting a new one, so its
        # manifest doesn't sit at status "running" forever (looks crashed)
        if self.recorder is not None and self.result is None:
            self.recorder.manifest["status"] = "abandoned"
            self.recorder._write_manifest()
        self.run_id = new_run_id("human", seed, "web")
        self.recorder = RunRecorder(self.run_id, {
            "agent": "human", "club": "mid", "model": None,
            "seed": seed, "years": years, "param_overrides": None})
        self.game = Game(seed=seed, years=years, player_club="mid")
        self.result = None
        self.packet = self.game.start()
        self._absorb_game_over(self.packet)
        return self.state()

    def _absorb_game_over(self, packet: dict) -> None:
        if packet and packet.get("game_over"):
            self.result = packet["result"]
            if self.recorder is not None:
                self.recorder.finish(self.result)

    def state(self) -> dict:
        if self.game is None:
            return {"started": False}
        g = self.game
        header = None
        if not g.world.settled:
            header = obs.club_overview(g.world, g.world.player_club_id)
        return {
            "budgets": {
                "offer_budget": g.world.params.stops.offer_budget,
                "offers_used": g.offers_this_stop,
                "queries_used": g.queries_this_stop,
                "query_budget": g.world.params.stops.query_budget,
            },
            "game_date": str(g.world.date),
            "game_over": g.world.settled,
            "header": header,
            "packet": self.packet,
            "result": self.result,
            "run_id": self.run_id,
            "started": True,
            "years": g.world.years,
        }

    def call_tool(self, name: str, args: dict) -> dict:
        if self.game is None:
            return {"ok": False,
                    "error": {"code": "no_game", "hint": "start a new game first"}}
        from engine.actions.commands import TOOLS
        stop_id = (self.packet or {}).get("stop_id")
        envelope = self.game.call_tool(name, args)
        if envelope.get("ok") and self.recorder is not None:
            spec = TOOLS.get(name)
            if name == "advance":
                # human stops carry no API cost; zeros keep the schema uniform
                self.recorder.stop_end(stop_id,
                                       {"api_calls": 0, "cache_hit_rate": 0.0,
                                        "cost_usd": 0.0},
                                       {"tool_errors": 0})
            elif spec is not None and spec["kind"] in ("action", "note"):
                self.recorder.action(stop_id, name, dict(args or {}))
        if name == "advance" and envelope.get("ok"):
            self.packet = envelope["data"]
            self._absorb_game_over(self.packet)
        return envelope


SESSION = Session()


class Handler(BaseHTTPRequestHandler):
    server_version = "FMBenchWeb/0.5"

    # -- helpers ---------------------------------------------------------------

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet: only errors reach the console
        pass

    # -- routes ----------------------------------------------------------------

    def do_GET(self):
        if self.path == "/api/state":
            with SESSION.lock:
                self._json(SESSION.state())
        elif self.path == "/api/meta":
            self._json({"tools": tool_schemas()})
        elif self.path.startswith("/api/"):
            self._json({"error": "unknown endpoint", "ok": False}, 404)
        else:
            self._static(self.path.split("?")[0])

    def do_POST(self):
        body = self._read_body()
        if self.path == "/api/new":
            seed = int(body.get("seed") or 42)
            years = int(body.get("years") or 5)
            years = max(1, min(years, 20))
            with SESSION.lock:
                self._json(SESSION.new_game(seed, years))
        elif self.path == "/api/tool":
            name = body.get("name") or ""
            args = body.get("args") or {}
            if not isinstance(args, dict):
                self._json({"error": {"code": "bad_args",
                                      "hint": "args must be an object"},
                            "ok": False}, 400)
                return
            with SESSION.lock:
                self._json(SESSION.call_tool(name, args))
        else:
            self._json({"error": "unknown endpoint", "ok": False}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"FM Bench web shell: http://localhost:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
