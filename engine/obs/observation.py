"""Observation layer: the ONLY external read path (DESIGN §2.1 iron rule 1).

Every value leaving the engine passes through here. Rules (I2):
- ability is coarse band + confidence letter, never a point estimate;
- lists sorted by (name, id) — no truth-ordered side channels;
- money quantized to 0.1M, ratings to 0.1.
"""

from __future__ import annotations

from engine.core.params import CENTS_PER_M
from engine.domain.league import league_table
from engine.sim import scouting


def money_m(cents: int) -> float:
    return round(cents / CENTS_PER_M, 1)


def _morale_band(v: float) -> str:
    if v >= 70:
        return "high"
    if v >= 45:
        return "ok"
    return "low"


def _fatigue_band(v: float) -> str:
    if v > 85:
        return "exhausted"
    if v > 70:
        return "tired"
    if v > 40:
        return "ok"
    return "fresh"


def player_view(world, viewer_cid: str, pid: str) -> dict | None:
    p = world.players.get(pid)
    if p is None:
        return None
    own = p.club_id == viewer_cid
    sd = world.params.world.season_settlement_day
    months = p.contract_months_left(world.date.year, world.date.day, sd) \
        if p.club_id else 0
    d = {
        "ability": scouting.ability_band(world, viewer_cid, p),
        "age": p.age(world.date.year),
        "club": world.clubs[p.club_id].name if p.club_id else None,
        "club_id": p.club_id,
        "contract_months_left": months,
        "form": round(p.form, 1),
        "injury_days": p.injury_days if p.injury_days > 0 else 0,
        "listed": p.listed,
        "name": p.name,
        "nationality": p.nationality,
        "pid": p.pid,
        "position": p.pos,
        "season_stats": {"apps": p.season_apps, "assists": p.season_assists,
                         "goals": p.season_goals,
                         "minutes": p.season_minutes},
        "youth": p.is_youth,
    }
    if p.is_youth:
        d["potential_stars"] = scouting.perceived_potential_stars(world, viewer_cid, p)
    if own:
        d["fatigue"] = _fatigue_band(p.fatigue)
        d["morale"] = _morale_band(p.morale)
        d["wage_m_per_year"] = money_m(p.wage_cents)
    return d


def squad_view(world, viewer_cid: str) -> list[dict]:
    out = [player_view(world, viewer_cid, p.pid)
           for p in world.seniors_of(viewer_cid)]
    return sorted(out, key=lambda x: (x["name"], x["pid"]))


def youth_view(world, viewer_cid: str) -> list[dict]:
    out = [player_view(world, viewer_cid, p.pid)
           for p in world.youths_of(viewer_cid)]
    return sorted(out, key=lambda x: (x["name"], x["pid"]))


def club_overview(world, viewer_cid: str) -> dict:
    club = world.clubs[viewer_cid]
    from engine.sim.board import player_club_position
    return {
        "academy_level": club.academy_level,
        "board_confidence": int(round(club.board_confidence)),
        "cash_m": money_m(club.cash),
        "division": club.division,
        "facility_projects": [
            {"days_left": p["complete_abs_day"] - world.date.abs_day,
             "kind": p["kind"]} for p in club.facility_projects],
        "formation": club.formation,
        "league_position": player_club_position(world),
        "name": club.name,
        "reputation": round(club.reputation),
        "season_target": club.season_target,
        "stadium_capacity": club.stadium_capacity,
        "style": club.style,
        "training_level": club.training_level,
    }


def league_table_view(world, division: int) -> list[dict]:
    table = league_table(world.division_clubs(division))
    return [{
        "club": c.name, "club_id": c.cid, "drawn": c.td,
        "goal_diff": c.tgf - c.tga, "lost": c.tl, "played": c.tw + c.td + c.tl,
        "points": c.points(), "position": i, "won": c.tw,
    } for i, c in enumerate(table, start=1)]


def fixtures_view(world, viewer_cid: str) -> list[dict]:
    club = world.clubs[viewer_cid]
    w = world.params.world
    out = []
    for rnd in range(w.rounds):
        day = w.first_match_day + rnd * w.match_interval_days
        for home, away in world.fixtures[club.division][rnd]:
            if viewer_cid in (home, away):
                mid = f"M{world.date.year:02d}R{rnd:02d}{home}"
                played = day <= world.date.day and mid in world.match_reports
                entry = {"day": day, "home": home == viewer_cid,
                         "opponent": world.clubs[away if home == viewer_cid else home].name,
                         "opponent_id": away if home == viewer_cid else home,
                         "round": rnd + 1}
                if played:
                    r = world.match_reports[mid]
                    entry["result"] = f"{r['home_goals']}-{r['away_goals']}"
                out.append(entry)
    return out


def match_report_view(world, viewer_cid: str, mid: str) -> dict | None:
    r = world.match_reports.get(mid)
    if r is None:
        return None
    players = {}
    for pid, stats in sorted(r["players"].items()):
        p = world.players.get(pid)
        players[pid] = {"assists": stats["assists"], "goals": stats["goals"],
                        "name": p.name if p else pid,
                        "rating": stats["rating"]}
    return {
        "away": world.clubs[r["away"]].name, "away_goals": r["away_goals"],
        "goals": [{"minute": g["minute"],
                   "scorer": world.players[g["scorer"]].name
                   if g["scorer"] in world.players else g["scorer"],
                   "side": g["side"]} for g in r["goals"]],
        "home": world.clubs[r["home"]].name, "home_goals": r["home_goals"],
        "mid": r["mid"], "players": players, "round": r["round"] + 1,
        "year": r["year"],
    }


def transfer_market_view(world, viewer_cid: str) -> list[dict]:
    out = []
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id == viewer_cid or p.is_youth:
            continue
        sd = world.params.world.season_settlement_day
        months = p.contract_months_left(world.date.year, world.date.day, sd) \
            if p.club_id else 0
        if not (p.listed or p.club_id is None or months < 12):
            continue
        view = player_view(world, viewer_cid, pid)
        view["status"] = ("free_agent" if p.club_id is None
                          else "listed" if p.listed else "expiring")
        if p.club_id is None:
            # v0.3: free agents demand a signing fee — disclosed up front so
            # neither humans nor agents are ambushed at contract time
            fee = int(world.params.market_ai.free_agent_fee_ratio
                      * scouting.perceived_value(world, viewer_cid, p))
            view["signing_fee_m"] = money_m(fee)
        out.append(view)
    return sorted(out, key=lambda x: (x["name"], x["pid"]))


def burn_trend(club, revenue: int) -> str:
    """Qualitative month-over-month net-burn tier. Digit-free by design:
    any real manager knows whether the club is bleeding cash and whether
    it is getting worse — parity demands the agent does too."""
    h = club.net_history
    if len(h) < 2:
        return "too early to read a monthly trend"
    slope = h[-1] - h[-2]  # net change over the last month
    monthly_rev = max(revenue // 12, 1)
    if slope >= 0:
        tier = "income currently covers spending"
    elif slope > -monthly_rev // 4:
        tier = "spending slightly above income"
    elif slope > -monthly_rev:
        tier = "spending well above income"
    else:
        tier = "burning cash at an unsustainable rate"
    if len(h) >= 3 and slope < 0:
        prev_slope = h[-2] - h[-3]
        if slope < prev_slope:
            tier += ", and accelerating"
        elif slope > prev_slope:
            tier += ", but easing"
    return tier


def finances_view(world, viewer_cid: str) -> dict:
    club = world.clubs[viewer_cid]
    wage_bill = world.annual_wage_bill(viewer_cid)
    revenue = max(club.revenue_last_season, club.rev_ytd, 1)
    return {
        "burn_trend": burn_trend(club, revenue),
        "cash_m": money_m(club.cash),
        "cost_ytd_m": money_m(club.cost_ytd),
        "in_administration": club.in_administration,
        "parachute_years_left": club.parachute_years_left,
        "revenue_last_season_m": money_m(club.revenue_last_season),
        "revenue_ytd_m": money_m(club.rev_ytd),
        "wage_bill_m": money_m(wage_bill),
        "wage_ratio": round(wage_bill / revenue, 3),
    }


def pending_offers_view(world) -> list[dict]:
    cid = world.player_club_id
    out = []
    for oid in sorted(world.market.offers):
        o = world.market.offers[oid]
        if o.status not in ("pending", "countered"):
            continue
        if cid not in (o.seller_cid, o.buyer_cid):
            continue
        p = world.players.get(o.player_id)
        out.append({
            "amount_m": money_m(o.amount),
            "buyer": world.clubs[o.buyer_cid].name,
            "counter_amount_m": money_m(o.counter_amount) if o.counter_amount else None,
            "direction": "incoming" if o.seller_cid == cid else "outgoing",
            "expires_in_days": max(0, o.expires_abs_day - world.date.abs_day),
            "oid": o.oid,
            "player": p.name if p else o.player_id,
            "player_id": o.player_id,
            "status": o.status,
        })
    return out


def history_view(world, viewer_cid: str, topic: str, year: int | None) -> dict:
    """Public archive queries; scope matches obs filtering (no truth)."""
    if topic == "honors":
        rows = [h for h in world.honors if year is None or h["year"] == year]
        return {"honors": [{"club": world.clubs[h["cid"]].name,
                            "code": h["code"], "year": h["year"]}
                           for h in rows]}
    if topic == "transfers":
        rows = [t for t in world.market.transfer_log
                if year is None or t["year"] == year]
        # v0.3 sealed market: clearing prices are never published (decision
        # #9) — the archive shows fees only on the viewer's OWN deals; every
        # other row reads "who acquired whom", amount undisclosed.
        from engine.sim.market_ai import sealed as _sealed
        hide = _sealed(world)
        return {"transfers": [{
            "buyer": world.clubs[t["buyer"]].name,
            "fee_m": (money_m(t["fee"])
                      if not hide or viewer_cid in (t["buyer"], t["seller"])
                      else None),
            "player": world.players[t["player"]].name
            if t["player"] in world.players else t["player"],
            "seller": world.clubs[t["seller"]].name,
            "year": t["year"]} for t in rows[-100:]]}
    if topic == "seasons":
        rows = [e for e in (world.event_log.entries if world.event_log else [])
                if e["type"] == "season_settled"
                and (year is None or e["data"]["year"] == year)]
        # t2_champion is None in single-division worlds (the 1x16 standard);
        # found live by the first LLM player to query season history.
        def _club_name(cid):
            return world.clubs[cid].name if cid in world.clubs else None
        return {"seasons": [{"t1_champion": _club_name(e["data"]["t1_champion"]),
                             "t2_champion": _club_name(e["data"]["t2_champion"]),
                             "year": e["data"]["year"]} for e in rows]}
    return {"error": "unknown_topic"}


def draft_pool_view(world, cid: str) -> list[dict]:
    """v0.3 draft: template cards through the viewer's own scout (bands only,
    never truth — same I1/I2 machinery as every other ability read)."""
    cards = []
    for tid in sorted(world.draft_templates):
        tpl = world.draft_templates[tid]
        meta = world.draft_meta[tid]
        cards.append({
            "age": tpl.age(1),
            "band": scouting.ability_band(world, cid, tpl),
            "contract_years": meta["contract_years"],
            "id": tid,
            "pos": tpl.pos,
            "potential_stars": scouting.perceived_potential_stars(world, cid, tpl),
            "price_m": money_m(meta["price_cents"]),
            "tier": meta["tier"],
            "wage_m_per_year": money_m(tpl.wage_cents),
        })
    return cards
