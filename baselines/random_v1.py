"""random_v1: uniform choices over legal-ish actions. Target score ~ 0."""

from __future__ import annotations

import random

from baselines.base import call, data, handle_draft

FORMATIONS = ["3-5-2", "4-3-3", "4-4-2"]
STYLES = ["gegenpress", "lowblock", "possession"]


def random_v1(game, packet: dict) -> None:
    if handle_draft(game, packet, "random"):
        return
    rng = random.Random(f"{game.world.seed}:{packet['stop_id']}:random_v1")

    # answer pending offers at random
    for offer in packet.get("pending_offers", []):
        if offer["direction"] == "incoming":
            act = rng.choice(["accept", "reject", "ignore"])
            if act != "ignore":
                call(game, "respond_to_offer",
                     {"action": act, "offer_id": offer["oid"]})
        elif offer["status"] == "countered":
            act = rng.choice(["accept_counter", "withdraw", "ignore"])
            if act != "ignore":
                call(game, "respond_to_offer",
                     {"action": act, "offer_id": offer["oid"]})

    n_actions = rng.randint(0, 3)
    for _ in range(n_actions):
        kind = rng.choice(["bid", "list", "tactics"])
        if kind == "tactics":
            call(game, "set_tactics", {"formation": rng.choice(FORMATIONS),
                                       "style": rng.choice(STYLES)})
        elif kind == "list":
            squad = data(game, "get_squad") or []
            if squad:
                p = rng.choice(squad)
                call(game, "list_player",
                     {"listed": rng.random() < 0.5, "player_id": p["pid"]})
        elif kind == "bid" and packet["digest"].get("window"):
            market = data(game, "get_transfer_market") or []
            cash = packet["digest"].get("cash_m", 0)
            targets = [v for v in market if v["status"] != "free_agent"]
            if targets and cash > 1:
                t = rng.choice(targets)
                call(game, "make_transfer_offer",
                     {"amount_m": round(rng.uniform(0.5, max(1.0, cash * 0.8)), 1),
                      "player_id": t["pid"]})
