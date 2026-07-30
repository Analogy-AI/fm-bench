"""heuristic_v1: disciplined deterministic FM script (DESIGN §10.3).

Wage bill <= 60% of revenue; age-structured squad (renew the young core, sell
the old); buys young value targets in windows; invests in academy when rich;
rotates by fatigue; switches style by opponent table position. Target ~ mid.
"""

from __future__ import annotations

from baselines.base import (band_center, best_lineup_ids, call, data,
                            handle_draft, sorted_by_ability, value_proxy_m)

WAGE_CEIL = 0.60
BUY_WAGE_HEADROOM = 0.52
MAX_BUYS_PER_STOP = 2
OLD_AGE = 32
RENEW_MAX_AGE = 29


def heuristic_v1(game, packet: dict) -> None:
    if handle_draft(game, packet, "balanced"):
        return
    digest = packet["digest"]
    types = packet["stop_types"]

    if game.world.stop_counter <= 1:
        call(game, "set_standing_order",
             {"mult": 0.9, "order_type": "reject_offers_below"})

    _handle_offers(game, packet)

    window_stop = digest.get("window") and any(
        t in types for t in ("WINDOW_SUMMER_PLAN", "WINDOW_WINTER_OPEN",
                             "DEADLINE_SUMMER", "DEADLINE_WINTER",
                             "PRESEASON_BOARD", "OFFER_BATCH"))
    if window_stop:
        _squad_planning(game, digest)

    if "YOUTH_INTAKE" in types:
        _youth(game)

    if any(t in types for t in ("LINEUP_LOCK", "MONTHLY_REPORT",
                                "MIDSEASON_REVIEW", "PRESEASON_BOARD")):
        _matchday_prep(game, digest)

    if "SEASON_SETTLEMENT" in types:
        _invest_if_rich(game)


def _handle_offers(game, packet: dict) -> None:
    squad = None
    for offer in packet.get("pending_offers", []):
        if offer["direction"] == "incoming":
            if squad is None:
                squad = {p["pid"]: p for p in (data(game, "get_squad") or [])}
            view = squad.get(offer["player_id"])
            if view is None:
                continue
            centers = sorted((band_center(p) for p in squad.values()), reverse=True)
            median = centers[len(centers) // 2] if centers else 0
            keep = band_center(view) >= median and view["age"] < 30
            good_price = offer["amount_m"] >= value_proxy_m(view)
            if (not keep and good_price) or view["age"] >= OLD_AGE:
                call(game, "respond_to_offer",
                     {"action": "accept", "offer_id": offer["oid"]})
            elif not keep:
                call(game, "respond_to_offer",
                     {"action": "counter", "offer_id": offer["oid"],
                      "counter_amount_m": round(value_proxy_m(view) * 1.3, 1)})
            else:
                call(game, "respond_to_offer",
                     {"action": "reject", "offer_id": offer["oid"]})
        elif offer["status"] == "countered":  # outgoing counter on our bid
            fin = data(game, "get_finances") or {}
            counter = offer.get("counter_amount_m") or 1e18
            if counter <= (fin.get("cash_m", 0)) * 0.5:
                call(game, "respond_to_offer",
                     {"action": "accept_counter", "offer_id": offer["oid"]})
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": offer["oid"]})


def _squad_planning(game, digest: dict) -> None:
    squad = data(game, "get_squad") or []
    fin = data(game, "get_finances") or {}
    wage_ratio = fin.get("wage_ratio", 1.0)
    revenue = max(fin.get("revenue_last_season_m", 1.0), 1.0)

    # age structure: list the old
    for p in squad:
        if p["age"] >= OLD_AGE and not p.get("listed"):
            call(game, "list_player", {"listed": True, "player_id": p["pid"]})

    # renew the young core before contracts run down
    centers = sorted((band_center(p) for p in squad), reverse=True)
    median = centers[len(centers) // 2] if centers else 0
    for p in sorted_by_ability(squad):
        if (p.get("contract_months_left", 99) <= 12 and p["age"] <= RENEW_MAX_AGE
                and band_center(p) >= median and wage_ratio < WAGE_CEIL):
            wage = round((p.get("wage_m_per_year") or 0.5) * 1.2, 2)
            env = call(game, "offer_contract",
                       {"player_id": p["pid"], "wage_m_per_year": wage, "years": 3})
            d = env.get("data") or {}
            counter = d.get("counter_wage_m")
            if not d.get("accepted") and counter:
                if (counter - wage) / revenue < 0.03:
                    call(game, "offer_contract",
                         {"player_id": p["pid"], "wage_m_per_year": counter,
                          "years": 3})

    # buy young value if there is room in wages and squad
    if wage_ratio < BUY_WAGE_HEADROOM and digest.get("window"):
        _buy(game, squad)


def _buy(game, squad: list[dict]) -> None:
    market = data(game, "get_transfer_market") or []
    centers = sorted((band_center(p) for p in squad), reverse=True)
    bar = centers[10] if len(centers) > 10 else 0  # beat our 11th player
    targets = [v for v in sorted_by_ability(market)
               if v["age"] <= 26 and band_center(v) > bar]
    buys = 0
    for view in targets:
        if buys >= MAX_BUYS_PER_STOP:
            break
        fin = data(game, "get_finances") or {}
        cash_m = fin.get("cash_m", 0)
        if cash_m < 5 or fin.get("wage_ratio", 1.0) >= BUY_WAGE_HEADROOM:
            break
        if view["status"] == "free_agent":
            env = call(game, "offer_contract",
                       {"player_id": view["pid"], "wage_m_per_year":
                        max(0.3, round(band_center(view) / 60, 1)), "years": 3})
            if (env.get("data") or {}).get("accepted"):
                buys += 1
            continue
        bid = min(round(value_proxy_m(view), 1), round(cash_m * 0.35, 1))
        if bid < 0.5:
            continue
        env = call(game, "make_transfer_offer",
                   {"amount_m": bid, "player_id": view["pid"]})
        d = env.get("data") or {}
        if d.get("status") == "accepted":
            buys += 1
        elif d.get("status") == "countered":
            counter = d.get("counter_amount_m") or 1e18
            if counter <= min(cash_m * 0.4, bid * 1.5):
                env2 = call(game, "respond_to_offer",
                            {"action": "accept_counter", "offer_id": d["oid"]})
                if (env2.get("data") or {}).get("status") == "bought":
                    buys += 1
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": d["oid"]})


def _youth(game) -> None:
    prospects = data(game, "get_youth_academy") or []
    for p in prospects:
        if (p.get("potential_stars") or 0) >= 4:
            call(game, "promote_youth", {"player_id": p["pid"]})


def _matchday_prep(game, digest: dict) -> None:
    squad = data(game, "get_squad") or []
    ids = best_lineup_ids(squad, avoid_exhausted=True)
    if len(ids) == 11:
        call(game, "set_lineup", {"player_ids": ids})

    style = "possession"
    nxt = digest.get("next_fixture")
    pos = digest.get("league_position")
    if nxt and pos:
        table = data(game, "get_league_table") or []
        opp_pos = next((r["position"] for r in table
                        if r["club"] == nxt["opponent"]), None)
        if opp_pos is not None:
            if opp_pos <= pos - 4:      # opponent much stronger
                style = "lowblock"
            elif opp_pos >= pos + 4:    # opponent much weaker
                style = "gegenpress"
    call(game, "set_tactics", {"formation": "4-3-3", "style": style})


def _invest_if_rich(game) -> None:
    fin = data(game, "get_finances") or {}
    if fin.get("cash_m", 0) > 3 * max(fin.get("wage_bill_m", 1), 1):
        overview = data(game, "get_club_overview") or {}
        if overview.get("academy_level", 3) < 3:
            call(game, "invest", {"kind": "academy"})
        elif overview.get("training_level", 3) < 3:
            call(game, "invest", {"kind": "training"})
