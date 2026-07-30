"""Decision-stop scheduler: fixed calendar + queued event stops -> stop packet.

Everything in a packet is observation-filtered (built via engine.obs) — packets
are what the runner/UI render, so no truth may pass through here.
"""

from __future__ import annotations

from engine.obs import observation as obs
from engine.sim.board import player_club_position


def due_stop_types(world) -> list[str]:
    if world.player_club_id is None:
        return []
    types: list[str] = []
    fixed = world.params.stops.fixed
    ftype = fixed.get(world.date.day)
    if ftype is not None:
        types.append(ftype)
    # offer batch: pending offers waiting and no batch stop recently
    pending = [o for o in world.market.offers.values()
               if o.status in ("pending", "countered")
               and o.seller_cid == world.player_club_id]
    if pending and (world.date.abs_day - world.last_offer_batch_day
                    >= world.params.stops.offer_batch_days):
        types.append("OFFER_BATCH")
    for t in world.queued_stop_types:
        if t not in types:
            types.append(t)
    return types


def build_packet(world, types: list[str]) -> dict:
    world.stop_counter += 1
    world.season_stops += 1
    stop_id = f"S{world.stop_counter:04d}"
    world.current_stop_id = stop_id
    world.queued_stop_types = []
    if "OFFER_BATCH" in types or "DEADLINE_SUMMER" in types or \
            "DEADLINE_WINTER" in types:
        world.last_offer_batch_day = world.date.abs_day

    cid = world.player_club_id
    club = world.clubs[cid]
    inbox = world.inbox
    world.inbox = []

    packet = {
        "action_required": [i for i in inbox if i["action_required"]],
        "date": {"day": world.date.day, "month": world.date.month,
                 "year": world.date.year},
        "digest": _digest(world, club),
        "inbox": inbox,
        "notebook": world.notebook,
        "pending_offers": obs.pending_offers_view(world),
        "recent_actions": world.recent_actions[-10:],
        "stop_id": stop_id,
        "stop_types": types,
    }
    stop_event: dict = {"stop_id": stop_id, "types": types}
    if world.league is not None:  # benchmark event stream stays byte-identical
        stop_event["cid"] = cid
    world.log_event("stop", stop_event)
    return packet


def _digest(world, club) -> dict:
    pos = player_club_position(world)
    nxt = _next_fixture(world, club)
    wage_bill = world.annual_wage_bill(club.cid)
    revenue = max(club.revenue_last_season, club.rev_ytd, 1)
    injuries = sum(1 for p in world.seniors_of(club.cid) if p.injury_days > 0)
    return {
        "board_confidence": int(round(club.board_confidence)),
        "burn_trend": obs.burn_trend(club, revenue),
        "cash_m": obs.money_m(club.cash),
        "division": club.division,
        "injured_players": injuries,
        "league_position": pos,
        "next_fixture": nxt,
        "points": club.points(),
        "season_target": club.season_target,
        "squad_size": len(world.seniors_of(club.cid)),
        "wage_ratio": round(wage_bill / revenue, 3),
        "warnings": _warnings(world, club, wage_bill, revenue),
        "window": world.window_kind(),
        "year_of_run": f"{world.date.year}/{world.years}",
    }


def _warnings(world, club, wage_bill: int, revenue: int) -> list[str]:
    e = world.params.economy
    out = []
    if wage_bill / revenue > e.wage_ratio_warning:
        out.append("wage_ratio_critical")
    if club.cash < 0:
        out.append("negative_cash")
    if club.board_confidence < world.params.board.fire_vote_below:
        out.append("board_confidence_critical")
    if club.in_administration:
        out.append("in_administration")
    return out


def _next_fixture(world, club) -> dict | None:
    w = world.params.world
    for rnd in range(w.rounds):
        day = w.first_match_day + rnd * w.match_interval_days
        if day <= world.date.day:
            continue
        for home, away in world.fixtures[club.division][rnd]:
            if club.cid in (home, away):
                return {"day": day, "home": home == club.cid,
                        "opponent": world.clubs[away if home == club.cid else home].name,
                        "round": rnd + 1}
    return None
