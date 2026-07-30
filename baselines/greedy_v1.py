"""greedy_v1: buy the most expensive-looking players affordable, no long-term
planning. No renewals, no sales strategy, no youth, no rotation concept.

Reckless but not suicidal (DESIGN §12): it chases stars and overpays, ignores
age/contracts/academy entirely, but respects a hard cash floor and a wage
ceiling so it usually survives 3+ seasons. Target rung: clearly above random,
clearly below heuristic."""

from __future__ import annotations

from baselines.base import band_center, best_lineup_ids, call, data, sorted_by_ability, handle_draft

MAX_BUYS_PER_STOP = 2
MAX_ATTEMPTS_PER_STOP = 8   # offer budget is 10/stop; leave room for counters
REJECT_SKIP = 12            # flat rejection = priced out; jump down the list
WAGE_RATIO_CEILING = 0.85   # reckless: right at the wage-warning line
CASH_FLOOR_FRAC = 0.15      # never spend below 15% of last-season revenue
CASH_FLOOR_MIN_M = 8.0


def _cash_floor_m(fin: dict) -> float:
    rev = fin.get("revenue_last_season_m") or 0.0
    return max(CASH_FLOOR_MIN_M, CASH_FLOOR_FRAC * rev)


def _can_spend(fin: dict) -> bool:
    return (fin.get("wage_ratio", 9.9) < WAGE_RATIO_CEILING
            and not fin.get("in_administration"))


def greedy_v1(game, packet: dict) -> None:
    if handle_draft(game, packet, "stars"):
        return
    digest = packet["digest"]

    # counters on our outgoing bids: accept only within the spending envelope
    fin = data(game, "get_finances") or {}
    floor = _cash_floor_m(fin)
    for offer in packet.get("pending_offers", []):
        if offer["direction"] == "outgoing" and offer["status"] == "countered":
            counter = offer.get("counter_amount_m") or 1e18
            cash = fin.get("cash_m", 0.0)
            if _can_spend(fin) and counter <= cash - floor:
                call(game, "respond_to_offer",
                     {"action": "accept_counter", "offer_id": offer["oid"]})
                fin = data(game, "get_finances") or fin
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": offer["oid"]})
    # incoming offers are ignored (greedy never sells)

    if digest.get("window"):
        _splurge(game)

    if "LINEUP_LOCK" in packet["stop_types"] or "MIDSEASON_REVIEW" in packet["stop_types"]:
        squad = data(game, "get_squad") or []
        ids = best_lineup_ids(squad, avoid_exhausted=False)  # no rotation concept
        if len(ids) == 11:
            call(game, "set_lineup", {"player_ids": ids})
        # fixed flashy style, never adapted to the opponent (no planning)
        call(game, "set_tactics", {"formation": "4-3-3", "style": "possession"})


def _splurge(game) -> None:
    """Walk the market from the biggest perceived star downward. A flat
    rejection means the seller's class is out of our league — skip a chunk of
    the list instead of burning the offer budget on untouchable names."""
    market = sorted_by_ability(data(game, "get_transfer_market") or [])
    buys = attempts = idx = 0
    while idx < len(market) and buys < MAX_BUYS_PER_STOP \
            and attempts < MAX_ATTEMPTS_PER_STOP:
        view = market[idx]
        fin = data(game, "get_finances") or {}
        if not _can_spend(fin):
            break
        cash = fin.get("cash_m", 0.0)
        floor = _cash_floor_m(fin)
        budget = cash - floor
        if budget < 2:
            break

        if view["status"] == "free_agent":
            # star free agents only; wage pegged to perceived class, capped
            wage = min(round(max(0.5, band_center(view) / 60.0), 1),
                       max(0.5, fin.get("wage_bill_m", 0.0) * 0.15))
            call(game, "offer_contract",
                 {"player_id": view["pid"], "wage_m_per_year": wage, "years": 3})
            buys += 1
            idx += 1
            continue

        # spend at most half of the remaining envelope on one star;
        # open under the cap so an accepted counter still fits it
        target_cap = budget * 0.5
        bid = round(max(1.0, target_cap * 0.6), 1)
        env = call(game, "make_transfer_offer",
                   {"amount_m": bid, "player_id": view["pid"]})
        attempts += 1
        if not env.get("ok"):
            idx += 1
            continue
        status = env["data"].get("status") if env.get("data") else None
        if status == "countered":
            counter = env["data"].get("counter_amount_m") or 1e18
            if counter <= target_cap:
                call(game, "respond_to_offer",
                     {"action": "accept_counter", "offer_id": env["data"]["oid"]})
                buys += 1
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": env["data"]["oid"]})
            idx += 1
        elif status == "accepted":
            buys += 1
            idx += 1
        else:  # rejected outright: priced out at this tier
            idx += REJECT_SKIP
