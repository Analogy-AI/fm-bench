"""Byte-deterministic prompt assembly (DESIGN §11.2): sorted keys, no
timestamps, frozen system prefix so prompt caching survives across stops."""

from __future__ import annotations

import json
import pathlib

RULES_PATH = pathlib.Path(__file__).resolve().parent.parent / "rules.md"

ROLE_PREAMBLE = """You are playing FM Bench as the manager of your club. \
The full game manual follows. Play to maximize your final composite score \
over the whole run. At each decision stop: read the packet, query what you \
need, act, keep your notebook current, then call `advance` to end the stop. \
Each stop is a fresh conversation — your notebook and the game archive are \
your only memory."""


def system_text() -> str:
    rules = RULES_PATH.read_text(encoding="utf-8")
    return f"{ROLE_PREAMBLE}\n\n---\n\n{rules}"


def render_packet(packet: dict) -> str:
    body = json.dumps(packet, ensure_ascii=True, indent=1, sort_keys=True)
    return f"DECISION STOP {packet.get('stop_id', '?')}\n{body}"


def render_tool_result(envelope: dict) -> str:
    return json.dumps(envelope, ensure_ascii=True, sort_keys=True)


SOFT_REMINDER = ("Reminder: you have used many rounds this stop. Converge: "
                 "make your remaining decisions and call `advance`.")
