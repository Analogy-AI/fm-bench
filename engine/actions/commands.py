"""Tool registry: the ONLY external write path, plus observation-backed queries.

One player mental action = one tool (DESIGN §11.1). The same registry drives
runner, MCP and Web UI. Error hints are code-level only, never numeric (D4).
"""

from __future__ import annotations

from engine.core.params import CENTS_PER_M, M
from engine.domain.transfer import Offer, quantize_ask
from engine.obs import observation as obs
from engine.sim import market_ai, scouting
from engine.actions.validate import ValidationError

TOOLS: dict[str, dict] = {}


def tool(name: str, kind: str, description: str, schema_props: dict,
         required: list[str]):
    def wrap(fn):
        TOOLS[name] = {
            "description": description,
            "handler": fn,
            "kind": kind,
            "schema": {"additionalProperties": False,
                       "properties": schema_props,
                       "required": required, "type": "object"},
        }
        return fn
    return wrap


def _err(code: str, hint: str) -> dict:
    return {"error": {"code": code, "hint": hint}, "ok": False}


def _ok(data) -> dict:
    return {"data": data, "ok": True}


def _own_player(world, pid: str, allow_youth: bool = False):
    p = world.players.get(pid)
    if p is None or p.club_id != world.player_club_id:
        raise ValidationError("not_your_player", "player not found in your club")
    if p.is_youth and not allow_youth:
        raise ValidationError("is_youth", "player is in the youth academy; promote first")
    return p


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

@tool("get_club_overview", "query", "Your club: division, cash, board, facilities, tactics.", {}, [])
def get_club_overview(world, args):
    return _ok(obs.club_overview(world, world.player_club_id))


@tool("get_squad", "query", "Your senior squad with ability bands, form, contracts, wages.", {}, [])
def get_squad(world, args):
    return _ok(obs.squad_view(world, world.player_club_id))


@tool("get_player", "query", "Detailed view of any player by id.",
      {"player_id": {"type": "string"}}, ["player_id"])
def get_player(world, args):
    v = obs.player_view(world, world.player_club_id, args["player_id"])
    if v is None:
        return _err("not_found", "no such player")
    return _ok(v)


@tool("get_league_table", "query", "League standings.",
      {"division": {"maximum": 2, "minimum": 1, "type": "integer"}}, [])
def get_league_table(world, args):
    division = args.get("division") or world.clubs[world.player_club_id].division
    return _ok(obs.league_table_view(world, division))


@tool("get_fixtures", "query", "Your season fixtures and results.", {}, [])
def get_fixtures(world, args):
    return _ok(obs.fixtures_view(world, world.player_club_id))


@tool("get_match_report", "query", "Match report by match id.",
      {"match_id": {"type": "string"}}, ["match_id"])
def get_match_report(world, args):
    v = obs.match_report_view(world, world.player_club_id, args["match_id"])
    if v is None:
        return _err("not_found", "no such match this season or last")
    return _ok(v)


@tool("get_transfer_market", "query",
      "Players available to buy: listed, free agents, expiring contracts.", {}, [])
def get_transfer_market(world, args):
    return _ok(obs.transfer_market_view(world, world.player_club_id))


@tool("get_finances", "query", "Your cash, revenue, wage bill, warnings.", {}, [])
def get_finances(world, args):
    return _ok(obs.finances_view(world, world.player_club_id))


@tool("get_youth_academy", "query", "Your academy prospects with potential stars.", {}, [])
def get_youth_academy(world, args):
    return _ok(obs.youth_view(world, world.player_club_id))


@tool("get_history", "query", "Archive: honors / transfers / seasons.",
      {"topic": {"enum": ["honors", "seasons", "transfers"], "type": "string"},
       "year": {"minimum": 1, "type": "integer"}}, ["topic"])
def get_history(world, args):
    return _ok(obs.history_view(world, world.player_club_id, args["topic"],
                                args.get("year")))


@tool("get_inbox", "query", "Pending offers and open items.", {}, [])
def get_inbox(world, args):
    return _ok({"pending_offers": obs.pending_offers_view(world)})


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

@tool("set_lineup", "action",
      "Set your preferred starting XI (auto-completed if players unavailable).",
      {"player_ids": {"items": {"type": "string"}, "type": "array"}},
      ["player_ids"])
def set_lineup(world, args):
    ids = args["player_ids"]
    if len(ids) != 11 or len(set(ids)) != 11:
        return _err("bad_lineup", "provide exactly 11 distinct player ids")
    for pid in ids:
        _own_player(world, pid)
        if not world.players[pid].available():
            return _err("player_unavailable", "an injured player cannot start")
    world.clubs[world.player_club_id].lineup = list(ids)
    return _ok({"lineup_set": True})


@tool("set_tactics", "action", "Set formation and playing style (enums only).",
      {"formation": {"enum": ["3-5-2", "4-3-3", "4-4-2"], "type": "string"},
       "style": {"enum": ["gegenpress", "lowblock", "possession"],
                 "type": "string"}}, [])
def set_tactics(world, args):
    club = world.clubs[world.player_club_id]
    if "formation" in args:
        club.formation = args["formation"]
    if "style" in args and args["style"] != club.style:
        club.prev_style = club.style
        club.style = args["style"]
        rnd = world.round_for_day(world.date.day)
        nxt = 0
        w = world.params.world
        if world.date.day >= w.first_match_day:
            nxt = min(w.rounds - 1,
                      (world.date.day - w.first_match_day) // w.match_interval_days + 1)
        club.style_switch_round = nxt if rnd is None else rnd
    return _ok({"formation": club.formation, "style": club.style})


@tool("make_transfer_offer", "action",
      "Bid for another club's player (negotiation resolves within this stop).",
      {"amount_m": {"minimum": 0, "type": "number"},
       "player_id": {"type": "string"}}, ["player_id", "amount_m"])
def make_transfer_offer(world, args):
    if not world.is_window_open():
        return _err("window_closed", "transfer window is closed")
    p = world.players.get(args["player_id"])
    if p is None or p.is_youth:
        return _err("not_found", "no such transferable player")
    if p.club_id == world.player_club_id:
        return _err("own_player", "cannot bid for your own player")
    if p.club_id is None:
        return _err("free_agent", "free agents are signed with offer_contract")
    amount = int(args["amount_m"] * CENTS_PER_M)
    club = world.clubs[world.player_club_id]
    if amount > club.cash:
        return _err("insufficient_cash", "offer exceeds available cash")
    if market_ai.sealed(world) and not world.is_controlled(p.club_id):
        # v0.3 sealed market: exposure cap, then queue for day-end resolution
        exposure = market_ai.outstanding_exposure(world, world.player_club_id)
        if exposure + amount > club.cash:
            return _err("exposure_cap",
                        f"your live offers already commit "
                        f"{obs.money_m(exposure)}M; this bid would exceed "
                        "your cash — withdraw an offer or bid less")
        offer = Offer(oid=world.ids.next("O"), player_id=p.pid,
                      buyer_cid=world.player_club_id, seller_cid=p.club_id,
                      amount=amount, created_abs_day=world.date.abs_day,
                      expires_abs_day=world.date.abs_day
                      + world.params.market_ai.offer_ttl_days)
        world.market.offers[offer.oid] = offer
        return _ok({"oid": offer.oid, "status": "queued",
                    "hint": "sealed market: bids on this player resolve "
                            "together at day end; if several clubs bid, the "
                            "highest acceptable bid wins at its own price. "
                            "The outcome arrives in your inbox."})
    offer = Offer(oid=world.ids.next("O"), player_id=p.pid,
                  buyer_cid=world.player_club_id, seller_cid=p.club_id,
                  amount=amount, created_abs_day=world.date.abs_day,
                  expires_abs_day=world.date.abs_day
                  + world.params.market_ai.offer_ttl_days)
    world.market.offers[offer.oid] = offer
    status = market_ai.respond_as_seller(world, offer)
    if status == "accepted":
        market_ai.execute_transfer(world, p.pid, world.player_club_id,
                                   offer.seller_cid, offer.amount)
        return _ok({"oid": offer.oid, "fee_m": obs.money_m(offer.amount),
                    "status": "accepted"})
    if status == "countered":
        return _ok({"counter_amount_m": obs.money_m(offer.counter_amount),
                    "oid": offer.oid, "rounds_used": offer.rounds,
                    "status": "countered"})
    return _ok({"oid": offer.oid, "status": status})


@tool("respond_to_offer", "action",
      "Respond to a pending offer: accept / reject / counter / accept_counter.",
      {"action": {"enum": ["accept", "accept_counter", "counter", "reject",
                           "withdraw"], "type": "string"},
       "counter_amount_m": {"minimum": 0, "type": "number"},
       "offer_id": {"type": "string"}}, ["offer_id", "action"])
def respond_to_offer(world, args):
    offer = world.market.offers.get(args["offer_id"])
    if offer is None or offer.status not in ("pending", "countered"):
        return _err("not_found", "no live offer with that id")
    me = world.player_club_id
    act = args["action"]
    p = world.players.get(offer.player_id)
    if p is None:
        # no state mutation on an error path (replay hygiene); the daily
        # market clearing expires the offer
        return _err("expired", "player no longer exists")

    if offer.seller_cid == me:  # incoming: I am the seller
        if act == "accept":
            market_ai.execute_transfer(world, offer.player_id, offer.buyer_cid,
                                       me, offer.amount)
            offer.status = "accepted"
            return _ok({"fee_m": obs.money_m(offer.amount), "status": "sold"})
        if act == "reject":
            offer.status = "rejected"
            return _ok({"status": "rejected"})
        if act == "counter":
            if "counter_amount_m" not in args:
                return _err("missing_arg", "counter needs counter_amount_m")
            if offer.rounds >= world.params.market_ai.max_rounds:
                offer.status = "rejected"
                return _ok({"status": "rejected_no_rounds_left"})
            offer.rounds += 1
            counter = quantize_ask(int(args["counter_amount_m"] * CENTS_PER_M))
            buyer = world.clubs[offer.buyer_cid]
            cap = int(scouting.perceived_value(world, offer.buyer_cid, p) * 1.15)
            if counter <= min(market_ai._transfer_budget(world, buyer), cap):
                market_ai.execute_transfer(world, offer.player_id,
                                           offer.buyer_cid, me, counter)
                offer.status = "accepted"
                return _ok({"fee_m": obs.money_m(counter),
                            "status": "counter_accepted_sold"})
            offer.status = "rejected"
            return _ok({"status": "counter_rejected_buyer_walked"})
        return _err("bad_action", "sellers may accept, reject or counter")

    if offer.buyer_cid == me:  # outgoing: I am the buyer
        if act == "withdraw":
            if offer.status == "countered":
                # walking away from an engaged seller stiffens this player's
                # ask for the rest of the window (G3: probing is not free)
                world.market.bump_window_ask(
                    offer.player_id, world.params.market_ai.reject_ask_bump)
            offer.status = "withdrawn"
            return _ok({"status": "withdrawn"})
        if act == "accept_counter":
            if offer.status != "countered":
                return _err("no_counter", "there is no counter to accept")
            club = world.clubs[me]
            if offer.counter_amount > club.cash:
                return _err("insufficient_cash", "counter exceeds available cash")
            market_ai.execute_transfer(world, offer.player_id, me,
                                       offer.seller_cid, offer.counter_amount)
            offer.status = "accepted"
            return _ok({"fee_m": obs.money_m(offer.counter_amount),
                        "status": "bought"})
        if act == "counter":
            if "counter_amount_m" not in args:
                return _err("missing_arg", "counter needs counter_amount_m")
            offer.amount = int(args["counter_amount_m"] * CENTS_PER_M)
            offer.status = "pending"
            if market_ai.sealed(world):
                # re-queue: the revised bid resolves at day end like any other
                exposure = market_ai.outstanding_exposure(world, me) - offer.amount
                club = world.clubs[me]
                if exposure + offer.amount > club.cash:
                    offer.status = "countered"  # revert
                    return _err("exposure_cap",
                                "this counter would exceed your cash "
                                "given your other live offers")
                return _ok({"oid": offer.oid, "status": "queued"})
            status = market_ai.respond_as_seller(world, offer)
            if status == "accepted":
                club = world.clubs[me]
                if offer.amount > club.cash:
                    offer.status = "rejected"
                    return _err("insufficient_cash", "offer exceeds available cash")
                market_ai.execute_transfer(world, offer.player_id, me,
                                           offer.seller_cid, offer.amount)
                return _ok({"fee_m": obs.money_m(offer.amount), "status": "bought"})
            if status == "countered":
                return _ok({"counter_amount_m": obs.money_m(offer.counter_amount),
                            "rounds_used": offer.rounds, "status": "countered"})
            return _ok({"status": status})
        return _err("bad_action", "buyers may counter, accept_counter or withdraw")
    return _err("not_yours", "this offer does not involve your club")


@tool("offer_contract", "action",
      "Offer a contract: renew own player, or sign a free agent. "
      "'years' is optional on follow-up offers: it defaults to the term of "
      "your previous offer to this player (so accepting a counter-wage only "
      "needs player_id and the wage).",
      {"player_id": {"type": "string"},
       "wage_m_per_year": {"minimum": 0, "type": "number"},
       "years": {"maximum": 5, "minimum": 1, "type": "integer"}},
      ["player_id", "wage_m_per_year"])
def offer_contract(world, args):
    p = world.players.get(args["player_id"])
    if p is None:
        return _err("not_found", "no such player")
    if p.club_id is not None and p.club_id != world.player_club_id:
        return _err("not_available", "player is under contract elsewhere")
    years = args.get("years") or p.last_offer_years
    if not years:
        return _err("missing_arg",
                    "first offer to a player needs 'years'; later offers "
                    "default to your previous offer's term")
    wage = int(args["wage_m_per_year"] * CENTS_PER_M)
    p.last_offer_years = years
    result = market_ai.renewal_response(world, world.player_club_id, p.pid,
                                        wage, years)
    if not result["accepted"] and "counter_wage" in result:
        result = dict(result)
        result["counter_wage_m"] = obs.money_m(result.pop("counter_wage"))
    if "signing_fee" in result:
        result = dict(result)
        result["signing_fee_m"] = obs.money_m(result.pop("signing_fee"))
    result.setdefault("years_offered", years)
    return _ok(result)


@tool("list_player", "action", "Put a player on / off the transfer list.",
      {"listed": {"type": "boolean"}, "player_id": {"type": "string"}},
      ["player_id", "listed"])
def list_player(world, args):
    p = _own_player(world, args["player_id"])
    p.listed = args["listed"]
    return _ok({"listed": p.listed, "player_id": p.pid})


@tool("release_player", "action",
      "Terminate a contract (severance: half of one year's wage).",
      {"player_id": {"type": "string"}}, ["player_id"])
def release_player(world, args):
    p = _own_player(world, args["player_id"])
    club = world.clubs[world.player_club_id]
    severance = p.wage_cents // 2
    club.cash -= severance
    club.cost_ytd += severance
    p.club_id = None
    p.free_agent_since = world.date.year
    p.listed = False
    world.log_event("released", {"player": p.pid, "severance": severance})
    return _ok({"released": p.pid, "severance_m": obs.money_m(severance)})


@tool("promote_youth", "action", "Promote an academy prospect to the senior squad.",
      {"player_id": {"type": "string"}}, ["player_id"])
def promote_youth(world, args):
    p = _own_player(world, args["player_id"], allow_youth=True)
    if not p.is_youth:
        return _err("not_youth", "player is already in the senior squad")
    if len(world.seniors_of(world.player_club_id)) >= world.params.world.max_squad_size:
        return _err("squad_full", "senior squad is at maximum size")
    p.is_youth = False
    p.wage_cents = M(world.params.wagemodel.youth_wage_M)
    p.contract_end_year = world.date.year + world.params.academy.youth_contract_years
    p.signed_year = world.date.year
    world.log_event("youth_promoted", {"player": p.pid})
    return _ok({"promoted": p.pid})


@tool("invest", "action",
      "Start a facility upgrade: academy, training or stadium expansion.",
      {"kind": {"enum": ["academy", "stadium", "training"], "type": "string"}},
      ["kind"])
def invest(world, args):
    club = world.clubs[world.player_club_id]
    kind = args["kind"]
    if any(pr["kind"] == kind for pr in club.facility_projects):
        return _err("in_progress", "that upgrade is already under construction")
    if kind == "academy":
        if club.academy_level >= 3:
            return _err("max_level", "academy already at maximum level")
        cost = M(world.params.academy.cost_upgrade_M[club.academy_level + 1])
        days = world.params.academy.upgrade_days
    elif kind == "training":
        if club.training_level >= 3:
            return _err("max_level", "training facility already at maximum level")
        cost = M(world.params.training_facility.cost_upgrade_M[club.training_level + 1])
        days = world.params.training_facility.upgrade_days
    else:
        st = world.params.stadium
        new_cap = club.stadium_capacity + st.expand_step
        cost = int(M(st.cost_base_M) * (new_cap / st.cap_ref) ** st.cost_pow)
        days = st.upgrade_days
    if cost > club.cash:
        return _err("insufficient_cash", "not enough cash for this project")
    club.cash -= cost
    club.facility_projects.append({
        "complete_abs_day": world.date.abs_day + days, "cost": cost,
        "kind": kind})
    world.log_event("invest", {"cost": cost, "kind": kind})
    return _ok({"cost_m": obs.money_m(cost), "days": days, "kind": kind})


@tool("set_standing_order", "action",
      "Add or clear a standing order (max 10; single threshold + fixed action).",
      {"mult": {"maximum": 5.0, "minimum": 0.1, "type": "number"},
       "order_type": {"enum": ["accept_offers_above", "clear_all",
                               "reject_offers_below"], "type": "string"}},
      ["order_type"])
def set_standing_order(world, args):
    if args["order_type"] == "clear_all":
        world.standing_orders = []
        return _ok({"orders": []})
    if "mult" not in args:
        return _err("missing_arg", "this order type needs a mult threshold")
    if len(world.standing_orders) >= world.params.stops.max_standing_orders:
        return _err("too_many_orders", "standing order limit reached")
    world.standing_orders.append({"mult": args["mult"],
                                  "type": args["order_type"]})
    return _ok({"orders": world.standing_orders})


# ---------------------------------------------------------------------------
# notebook + control
# ---------------------------------------------------------------------------

@tool("get_draft_pool", "query",
      "Draft phase only: the shared template pool — position, age, your "
      "scout's ability band, potential stars, price, preset wage and "
      "contract length — plus your budget status.", {}, [])
def get_draft_pool(world, args):
    if not world.draft_open:
        return _err("no_draft", "no draft phase is in progress")
    d = world.params.draft
    from engine.sim import draft as draft_mod
    mine = world.draft_submissions.get(world.player_club_id)
    return _ok({
        "budget_m": d.budget_m,
        "min_picks": d.min_picks,
        "pool": obs.draft_pool_view(world, world.player_club_id),
        "rules": "pick >=min_picks templates incl. a goalkeeper within the "
                 "budget; unpicked templates disappear for everyone; leftover "
                 "budget plus the stipend becomes your opening cash",
        "stipend_m": d.stipend_m,
        "submitted_picks": mine,
        "submitted_cost_m": (round(draft_mod.cost_of(world, mine) / CENTS_PER_M, 1)
                             if mine else None),
    })


@tool("submit_draft", "action",
      "Draft phase only: submit your complete pick list of template ids. "
      "A valid list has >= min_picks players including a goalkeeper and "
      "fits the budget; an invalid list is rejected with the reason. "
      "Resubmitting replaces your previous list. Set auto_fill=true to "
      "have an invalid/short list completed from the cheapest tier.",
      {"auto_fill": {"type": "boolean"},
       "picks": {"items": {"type": "string"}, "type": "array"}}, ["picks"])
def submit_draft(world, args):
    if not world.draft_open:
        return _err("no_draft", "no draft phase is in progress")
    from engine.sim import draft as draft_mod
    picks = [str(t) for t in args["picks"]]
    if args.get("auto_fill"):
        picks = draft_mod.auto_fill(world, picks)
    res = draft_mod.submit(world, world.player_club_id, picks)
    return _ok({**res, "status": "submitted",
                "hint": "call advance to lock the draft and start the season"})


@tool("append_note", "note","Append to your private notebook (persists across stops).",
      {"text": {"type": "string"}}, ["text"])
def append_note(world, args):
    cap = world.params.stops.notebook_max_chars       # 0/None = unlimited
    joined = world.notebook + ("\n" if world.notebook else "") + args["text"]
    if cap and len(joined) > cap:
        return _err("notebook_full",
                    "notebook exceeds capacity; use rewrite_notes to compress")
    world.notebook = joined
    world.log_event("note", {"chars": len(world.notebook), "mode": "append"})
    return _ok({"chars_used": len(world.notebook), "chars_cap": cap or None})


@tool("rewrite_notes", "note", "Replace the entire notebook.",
      {"text": {"type": "string"}}, ["text"])
def rewrite_notes(world, args):
    cap = world.params.stops.notebook_max_chars       # 0/None = unlimited
    if cap and len(args["text"]) > cap:
        return _err("notebook_full", "text exceeds notebook capacity")
    world.notebook = args["text"]
    world.log_event("note", {"chars": len(world.notebook), "mode": "rewrite"})
    return _ok({"chars_used": len(world.notebook), "chars_cap": cap or None})


@tool("advance", "control",
      "End this decision stop; simulate until the next stop. Returns the next stop packet.",
      {}, [])
def advance(world, args):  # handled specially by Game; registered for schema
    return _ok({"advancing": True})


def tool_schemas() -> list[dict]:
    """Anthropic-style tool list, deterministic order."""
    return [{"description": TOOLS[name]["description"],
             "input_schema": TOOLS[name]["schema"], "name": name}
            for name in sorted(TOOLS)]
