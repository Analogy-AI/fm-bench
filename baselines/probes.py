"""Exploit-probe policies: scripted attacks that must NOT pay off.

These are regression anchors for known degenerate strategies, run by the
probe tests (pytest -m probe) and reported alongside — never inside — the
legitimate ladder.

slim_squad_v1 is the "haiku playbook" from the 3y-track leak investigation:
strip the roster to the bare minimum, bank the wage savings, iron-man the
best XI and never buy. Before calibration v0.3 (dead fatigue + expectation
sandbag + free free-agents) this beat the oracle on the 3y track. With depth
activated and expectations floored it must lose to heuristic_v1 on any track.
"""

from __future__ import annotations

from baselines.base import best_lineup_ids, call, data, sorted_by_ability, handle_draft

SLIM_TARGET = 15


def slim_squad_v1(game, packet: dict) -> None:
    if handle_draft(game, packet, "floor"):
        return
    types = packet["stop_types"]

    # iron-man lineup: best XI by believed ability, fatigue deliberately ignored
    squad = data(game, "get_squad") or []
    fit = [p for p in squad if p.get("injury_days", 0) == 0]
    ids = [p["pid"] for p in sorted_by_ability(fit)[:11]]
    if len(ids) == 11:
        call(game, "set_lineup", {"player_ids": ids})
    call(game, "set_tactics", {"formation": "4-3-3", "style": "possession"})

    # reject every incoming bid (keep the XI intact), decline all negotiations
    inbox = data(game, "get_inbox") or {}
    for o in inbox.get("pending_offers", []):
        if o.get("direction") == "incoming" and o.get("status") == "pending":
            call(game, "respond_to_offer",
                 {"action": "reject", "offer_id": o["oid"]})

    # the exploit: shed everyone outside the XI+4 down to the minimum
    if any(t in types for t in ("WINDOW_SUMMER_PLAN", "WINDOW_WINTER_OPEN",
                                "MONTHLY_REPORT", "PRESEASON_BOARD")):
        squad = data(game, "get_squad") or []
        ranked = sorted_by_ability(squad)
        for view in ranked[SLIM_TARGET:][::-1]:
            call(game, "release_player", {"player_id": view["pid"]})

    # keep the core alive: renew expiring members of the XI, pay the counter
    squad = data(game, "get_squad") or []
    for view in sorted_by_ability(squad)[:11]:
        if view.get("contract_months_left", 99) <= 9:
            wage = view.get("wage_m_per_year", 1.0)
            env = call(game, "offer_contract",
                       {"player_id": view["pid"], "wage_m_per_year": wage,
                        "years": 3})
            d = env.get("data") or {}
            counter = d.get("counter_wage_m")
            if not d.get("accepted") and counter:
                call(game, "offer_contract",
                     {"player_id": view["pid"], "wage_m_per_year": counter})
