"""oracle_v1: INTENTIONALLY PRIVILEGED information-ceiling baseline.

This policy reads TRUE hidden state (player.ca(), pa, true market value)
straight from game.world — the one thing no legitimate agent can ever do.
Its privilege is information, NOT power: every action still goes through
the same tool interface as other baselines, with the same per-stop query
and offer budgets enforced by Game.call_tool.

Purpose (score ladder):
- It measures the benchmark's INFORMATION CEILING: how much score perfect
  knowledge is worth on top of disciplined play. Structurally it mirrors
  heuristic_v1 (same survivable spending discipline), with truth replacing
  belief bands and true-value trading replacing wage-anchored proxies, so
  the oracle-vs-heuristic gap isolates the value of information.
- It is a LEAK ALARM: any non-privileged agent whose score approaches
  oracle_v1's is presumptively exploiting a hidden-information leak and
  must be audited before its score is trusted.

Engine code must never import this module — enforced by
tests/test_isolation.py::test_engine_never_imports_baselines. Oracle scores
are reported alongside, but never mixed into, the legitimate ladder.
"""

from __future__ import annotations

from baselines.base import call, data, handle_draft
from engine.domain.contract import market_wage_annual, wage_ask
from engine.domain.transfer import value_from_ability
from engine.sim import scouting

WAGE_CEIL = 0.62
BUY_WAGE_HEADROOM = 0.55
MAX_BUYS_PER_STOP = 3
OLD_AGE = 33          # keep depth: shed only genuine veterans
RENEW_MAX_AGE = 30
PROMOTE_PA = 1350  # fixed-point: promote clear future starters
YOUNG_AGE = 24     # a young high-PA target appreciates: value the oracle mints
FATIGUE_REST = 70  # rest a starter once tired: forces rotation, keeps XI fresh


def oracle_v1(game, packet: dict) -> None:
    if handle_draft(game, packet, "balanced"):
        return
    world = game.world
    digest = packet["digest"]
    types = packet["stop_types"]

    if world.stop_counter <= 1:
        call(game, "set_standing_order",
             {"mult": 0.9, "order_type": "reject_offers_below"})

    _handle_offers(game, world)

    window_stop = digest.get("window") and any(
        t in types for t in ("WINDOW_SUMMER_PLAN", "WINDOW_WINTER_OPEN",
                             "DEADLINE_SUMMER", "DEADLINE_WINTER",
                             "PRESEASON_BOARD", "OFFER_BATCH"))
    if window_stop:
        _squad_planning(game, world, digest)

    if "YOUTH_INTAKE" in types:
        _youth(game, world)

    # every stop: the chosen XI persists across all matches between stops, so
    # refresh it each time — truth x current-fitness picks must not go stale
    _matchday_prep(game, world, digest)

    if "SEASON_SETTLEMENT" in types:
        _invest_if_rich(game)


# -- truth helpers (the privilege lives here) ---------------------------------

def _true_value_m(world, p) -> float:
    sd = world.params.world.season_settlement_day
    months = p.contract_months_left(world.date.year, world.date.day, sd) \
        if p.club_id else 0
    cents = value_from_ability(world.params, p.ca(), p.age(world.date.year),
                               world.date.year, months, p.form)
    return cents / 1e8


def _fair_wage_m(world, p) -> float:
    cents = market_wage_annual(world.params, p.ca(), p.age(world.date.year),
                               world.date.year)
    return cents / 1e8


def _fa_closing_wage_m(world, p) -> float:
    """Exact free-agent reservation ceiling (privilege: the oracle can compute
    the deterministic wage_ask the negotiation uses, so its first offer always
    closes instead of collapsing into the 180-day cooldown)."""
    cid = world.player_club_id
    ca_est = scouting.perceived_ca(world, cid, p)
    cents = wage_ask(world.params, ca_est, p.age(world.date.year),
                     world.date.year, p.form, 0)
    return cents * 1.06 / 1e8


def _fa_fee_m(world, p) -> float:
    cid = world.player_club_id
    return (world.params.market_ai.free_agent_fee_ratio
            * scouting.perceived_value(world, cid, p)) / 1e8


def _squad_true(world):
    cid = world.player_club_id
    return sorted(world.seniors_of(cid), key=lambda p: (-p.ca(), p.pid))


def _keeper_bar(squad) -> int:
    """True CA of our 11th player: anyone above it is core."""
    cas = sorted((p.ca() for p in squad), reverse=True)
    return cas[10] if len(cas) > 10 else 0


# -- offers -------------------------------------------------------------------

def _handle_offers(game, world) -> None:
    cid = world.player_club_id
    squad = _squad_true(world)
    bar = _keeper_bar(squad)
    # rank in the squad by true CA: keep two-deep (top 18) so a season of
    # injuries and fatigue never forces a genuinely weak XI onto the pitch.
    rank = {p.pid: i for i, p in enumerate(squad)}
    for oid in sorted(world.market.offers):
        o = world.market.offers[oid]
        if o.status not in ("pending", "countered"):
            continue
        if o.seller_cid == cid and o.status == "pending":
            p = world.players.get(o.player_id)
            if p is None:
                continue
            tv = _true_value_m(world, p)
            amount_m = o.amount / 1e8
            age = p.age(world.date.year)
            # genuine surplus only: an aging vet, or clear depth (below 18th)
            # that a strong bid overpays for.
            surplus = age >= OLD_AGE or (rank.get(p.pid, 0) >= 18
                                         and p.ca() < bar and age >= 28)
            if surplus and (amount_m >= tv or age >= OLD_AGE):
                call(game, "respond_to_offer",
                     {"action": "accept", "offer_id": oid})
            elif surplus:
                call(game, "respond_to_offer",
                     {"action": "counter", "offer_id": oid,
                      "counter_amount_m": round(tv * 1.15, 1)})
            else:
                call(game, "respond_to_offer",
                     {"action": "reject", "offer_id": oid})
        elif o.buyer_cid == cid and o.status == "countered":
            p = world.players.get(o.player_id)
            fin = data(game, "get_finances") or {}
            counter_m = (o.counter_amount or 0) / 1e8
            tv = _true_value_m(world, p) if p is not None else 0.0
            if p is not None and counter_m <= min(tv * 1.05,
                                                  fin.get("cash_m", 0) * 0.5):
                call(game, "respond_to_offer",
                     {"action": "accept_counter", "offer_id": oid})
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": oid})


# -- window planning ----------------------------------------------------------

def _squad_planning(game, world, digest: dict) -> None:
    squad = _squad_true(world)
    fin = data(game, "get_finances") or {}
    wage_ratio = fin.get("wage_ratio", 1.0)
    bar = _keeper_bar(squad)
    year = world.date.year

    # shed only genuine veterans (value only falls from here). Keeping squad
    # depth matters more than trimming wages: a thin squad tires and loses,
    # and losing craters board confidence faster than wages do.
    for p in squad:
        if p.age(year) >= OLD_AGE and not p.listed:
            call(game, "list_player", {"listed": True, "player_id": p.pid})

    # renew the true core young enough to keep appreciating
    sd = world.params.world.season_settlement_day
    for p in squad:
        months = p.contract_months_left(year, world.date.day, sd)
        if (months <= 12 and p.age(year) <= RENEW_MAX_AGE
                and (p.ca() >= bar or p.pa >= PROMOTE_PA)
                and wage_ratio < WAGE_CEIL):
            wage = round(_fair_wage_m(world, p) * 1.05, 2)
            env = call(game, "offer_contract",
                       {"player_id": p.pid, "wage_m_per_year": wage,
                        "years": 4 if p.pa > p.ca() else 3})
            d = env.get("data") or {}
            counter = d.get("counter_wage_m")
            if not d.get("accepted") and counter:
                if counter <= _fair_wage_m(world, p) * 1.3:
                    call(game, "offer_contract",
                         {"player_id": p.pid, "wage_m_per_year": counter})

    if wage_ratio < BUY_WAGE_HEADROOM and digest.get("window"):
        _buy(game, world, squad, bar)


def _buy(game, world, squad, bar: int) -> None:
    """Buy the market's genuinely best value: true value vs what it costs."""
    cid = world.player_club_id
    year = world.date.year
    candidates = []
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id == cid or p.is_youth:
            continue
        sd = world.params.world.season_settlement_day
        months = p.contract_months_left(year, world.date.day, sd) \
            if p.club_id else 0
        purchasable = p.listed or p.club_id is None or months < 12
        if not purchasable:
            continue
        age = p.age(year)
        # two ways a target adds value: an immediate XI upgrade (CA > bar), OR
        # a young player whose true potential will appreciate above the bar.
        upgrade = p.ca() > bar and not (age > 27 and p.ca() < bar + 100)
        prospect = age <= YOUNG_AGE and p.pa >= bar and p.pa - p.ca() >= 80
        if not (upgrade or prospect):
            continue
        candidates.append(p)
    # rank by value minted per true cost: appreciation headroom (pa-ca) for the
    # young, current edge over the bar for the rest.
    def _mint(p):
        gain = max(p.ca() - bar, 0) + max(p.pa - p.ca(), 0) * (
            0.6 if p.age(year) <= YOUNG_AGE else 0.0)
        return -gain / max(_true_value_m(world, p), 0.5)
    candidates.sort(key=lambda p: (_mint(p), p.pid))
    buys = 0
    for p in candidates:
        if buys >= MAX_BUYS_PER_STOP:
            break
        fin = data(game, "get_finances") or {}
        cash_m = fin.get("cash_m", 0)
        if cash_m < 5 or fin.get("wage_ratio", 1.0) >= BUY_WAGE_HEADROOM:
            break
        tv = _true_value_m(world, p)
        if p.club_id is None:
            # sign only genuine bargains: true value must clear the signing
            # fee plus a wage-payback margin, priced at the exact closing wage
            wage_m = round(_fa_closing_wage_m(world, p), 2)
            fee_m = _fa_fee_m(world, p)
            if tv < fee_m + 2.0 * wage_m or fee_m > cash_m * 0.4:
                continue
            env = call(game, "offer_contract",
                       {"player_id": p.pid, "wage_m_per_year": wage_m,
                        "years": 3})
            if (env.get("data") or {}).get("accepted"):
                buys += 1
            continue
        # open BELOW true value and let the counter walk us up: paying full
        # tv on the first bid overpays whenever the seller's belief is low
        bid = min(round(tv * 0.7, 1), round(cash_m * 0.35, 1))
        if bid < 0.5:
            continue
        env = call(game, "make_transfer_offer",
                   {"amount_m": bid, "player_id": p.pid})
        d = env.get("data") or {}
        if d.get("status") == "accepted":
            buys += 1
        elif d.get("status") == "countered":
            counter = d.get("counter_amount_m") or 1e18
            if counter <= min(cash_m * 0.4, tv * 1.08):
                env2 = call(game, "respond_to_offer",
                            {"action": "accept_counter", "offer_id": d["oid"]})
                if (env2.get("data") or {}).get("status") == "bought":
                    buys += 1
            else:
                call(game, "respond_to_offer",
                     {"action": "withdraw", "offer_id": d["oid"]})


# -- youth / matchday / investment ---------------------------------------------

def _youth(game, world) -> None:
    cid = world.player_club_id
    bar = _keeper_bar(_squad_true(world))
    for p in sorted(world.youths_of(cid), key=lambda p: p.pid):
        if p.pa >= PROMOTE_PA or p.pa > bar:
            call(game, "promote_youth", {"player_id": p.pid})


def _matchday_prep(game, world, digest: dict) -> None:
    year = world.date.year
    # rest a star only once genuinely tired (keeps rotation happening so no one
    # hits exhaustion), but not so early that fresh reserves displace good tired
    # players — the 70 cutoff was throwing away second-half seasons.
    fit = [p for p in _squad_true(world)
           if p.available() and p.fatigue < FATIGUE_REST]
    if len(fit) < 11:
        fit = [p for p in _squad_true(world) if p.available()]
    ids = [p.pid for p in fit[:11]]
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
            if opp_pos <= pos - 4:
                style = "lowblock"
            elif opp_pos >= pos + 4:
                style = "gegenpress"
    call(game, "set_tactics", {"formation": "4-3-3", "style": style})


def _invest_if_rich(game) -> None:
    # excess cash is discounted (0.3) in net-worth VA and earns nothing in
    # squad value — a rich, safe oracle turns it into appreciating facilities.
    fin = data(game, "get_finances") or {}
    if fin.get("cash_m", 0) > 3 * max(fin.get("wage_bill_m", 1), 1):
        overview = data(game, "get_club_overview") or {}
        for kind in ("academy", "training", "stadium"):
            if overview.get(f"{kind}_level", 3) < 3:
                call(game, "invest", {"kind": kind})
                return
