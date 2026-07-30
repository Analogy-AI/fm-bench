"""AI club decisions: transfers, renewals, squad maintenance, standing-order
handling for pending offers.

I3 HARD INVARIANT (enforced by tests/test_isolation.py): this module must never
read true attributes (true_phys/true_tech/true_ment/pa/ca()) or hidden traits.
All ability/value judgments go through engine.sim.scouting belief functions.
"""

from __future__ import annotations

import math

from engine.domain.contract import market_wage_annual, wage_ask
from engine.domain.transfer import Offer, quantize_ask
from engine.sim import scouting


def sealed(world) -> bool:
    """v0.3 sealed-bid market flag (params.market_ai.sealed_bids)."""
    return bool(world.params.market_ai.get("sealed_bids", False))


def outstanding_exposure(world, buyer_cid: str) -> int:
    """Sum of the club's live outgoing offer amounts (exposure cap basis)."""
    return sum(o.amount for o in world.market.offers.values()
               if o.buyer_cid == buyer_cid
               and o.status in ("pending", "countered"))


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------

def seller_ask(world, seller_cid: str, pid: str, buyer_cid: str | None) -> int:
    """Belief-based, coarse-quantized asking price (X1 ruling)."""
    ma = world.params.market_ai
    w = world.params.windows
    p = world.players[pid]
    seller = world.clubs[seller_cid]
    ask = scouting.perceived_value(world, seller_cid, p) * ma.ask_over_value
    # persistent per-(player, window) list-price noise (X1/G3): the quoted
    # price embeds less information than the seller's internal belief, and
    # re-probing within a window cannot average it away
    kind_idx = {"summer": 0, "winter": 1}.get(world.window_kind(), 2)
    noise_stream = world.rng.stream("ask_noise", pid,
                                    world.date.year * 3 + kind_idx)
    ask *= math.exp(ma.list_price_sigma * noise_stream.gauss(0.0, 1.0))
    if p.listed:
        ask *= w.listed_discount
    kind = world.window_kind()
    if kind == "winter":
        ask *= w.winter_ask_mult
    close = w.summer_close if kind == "summer" else w.winter_close
    if kind and p.listed and close - world.date.day <= w.window_end_last_days:
        ask *= w.window_end_desperate_mult
    if seller.cash < 0:
        ask *= w.distress_mult
    ask *= world.market.window_ask_bump.get(pid, 1.0)
    if buyer_cid is not None:
        ask *= 1.0 + world.market.markup(buyer_cid, seller_cid)
        if world.clubs[buyer_cid].flip_rate() > ma.flip_rate_threshold:
            ask *= 1.0 + ma.flip_buy_penalty
    # key-player premium: top-3 believed ability in own squad
    seniors = world.seniors_of(seller_cid)
    ranked = sorted(seniors, key=lambda q: (-scouting.perceived_ca(world, seller_cid, q), q.pid))
    if any(q.pid == pid for q in ranked[:3]) and not p.listed:
        ask *= 1.3
    return quantize_ask(int(ask))


def _wage_headroom(world, club) -> int:
    ma = world.params.market_ai
    revenue = max(club.revenue_last_season, club.rev_ytd)
    target_ratio = ma.wage_target_ratio
    # 5/5/5 opponent tiers: hard boards budget wages ambitiously, easy ones
    # complacently (mult 1.0 = today's default; AI-club-only call sites)
    tier = world._opponent_tier_cfg.get(club.cid)
    if tier is not None:
        target_ratio *= tier.get("wage_target_mult", 1.0)
    target = int(revenue * target_ratio * (1.0 + club.wage_boost_pct))
    return target - world.annual_wage_bill(club.cid)


def _transfer_budget(world, club) -> int:
    return club.cash - int(0.3 * world.annual_wage_bill(club.cid))


# --------------------------------------------------------------------------
# negotiation (shared by AI-AI and player-club-as-buyer flows)
# --------------------------------------------------------------------------

def _accept_ratio(ma, seller) -> float:
    """The seller's accept threshold multiplier (shared by the bilateral
    respond path and the sealed-auction eligibility check — one rule)."""
    accept_ratio = ma.accept_ratio
    if seller.cash < 0:
        accept_ratio *= 0.8  # distress sells cheaper
    if seller.negotiation_style == "hard":
        accept_ratio *= 1.05
    elif seller.negotiation_style == "soft":
        accept_ratio *= 0.95
    return accept_ratio


def respond_as_seller(world, offer: Offer) -> str:
    """Seller decision on a pending offer. Mutates offer.status; returns it."""
    ma = world.params.market_ai
    p = world.players.get(offer.player_id)
    seller = world.clubs[offer.seller_cid]
    if p is None or p.club_id != offer.seller_cid:
        offer.status = "expired"
        return offer.status
    if len(world.seniors_of(offer.seller_cid)) <= world.params.world.min_squad_size:
        offer.status = "rejected"
        return offer.status
    ask = seller_ask(world, offer.seller_cid, offer.player_id, offer.buyer_cid)
    offer.ask_at_creation = ask
    accept_ratio = _accept_ratio(ma, seller)
    ratio = offer.amount / max(ask, 1)
    if ratio >= accept_ratio:
        offer.status = "accepted"
    elif ratio >= ma.counter_lo and offer.rounds < ma.max_rounds:
        offer.rounds += 1
        # counter at a stream-random fraction of the gap (G3: a midpoint
        # counter would make the hidden ask exactly invertible)
        frac_stream = world.rng.stream("negotiation", offer.oid,
                                       world.date.abs_day)
        # v0.3: counters land nearer the buyer (0.35-0.55 of the gap) so a
        # fair-price opening reaches closable range within the round budget
        frac = 0.35 + 0.20 * frac_stream.uniform(0.0, 1.0)
        offer.counter_amount = quantize_ask(
            offer.amount + int(frac * (ask - offer.amount)))
        offer.status = "countered"
    else:
        offer.status = "rejected"
        world.market.bump_window_ask(offer.player_id, ma.reject_ask_bump)
    return offer.status


def execute_transfer(world, pid: str, buyer_cid: str, seller_cid: str,
                     fee: int) -> None:
    ma = world.params.market_ai
    p = world.players[pid]
    buyer, seller = world.clubs[buyer_cid], world.clubs[seller_cid]
    commission = int(fee * world.params.economy.agent_commission)
    buyer.cash -= fee
    seller.cash += fee - commission
    # adaptive counterpressure bookkeeping
    seller.sales_total += 1
    if p.bought_fee_cents > 0 and \
            (world.date.abs_day - p.bought_abs_day) < ma.flip_owned_days:
        seller.flips += 1
    bump = ma.single_source_markup
    if fee < int(0.8 * scouting.perceived_value(world, seller_cid, p)):
        bump += ma.bargain_extra_markup
    world.market.bump_markup(buyer_cid, seller_cid, bump)
    # player joins buyer on an auto-negotiated contract (v0 simplification)
    age = p.age(world.date.year)
    new_ca = scouting.perceived_ca(world, buyer_cid, p)
    p.wage_cents = int(wage_ask(world.params, new_ca, age, world.date.year,
                                p.form, 24) * 1.05)
    p.contract_end_year = world.date.year + (4 if age <= 25 else 3)
    p.signed_year = world.date.year
    p.club_id = buyer_cid
    p.listed = False
    p.morale = max(p.morale, 70.0)
    p.bought_abs_day = world.date.abs_day
    p.bought_fee_cents = fee
    entry = {"buyer": buyer_cid, "day": world.date.day, "fee": fee,
             "player": pid, "seller": seller_cid, "year": world.date.year}
    world.market.transfer_log.append(entry)
    world.log_event("transfer", entry)
    for side in (buyer_cid, seller_cid):
        if world.is_controlled(side):
            world.push_inbox_for(side, "transfer_completed", entry)


def renewal_response(world, cid: str, pid: str, offered_wage: int,
                     years: int) -> dict:
    """Player's answer to a contract offer (renewal or free-agent signing)."""
    p = world.players[pid]
    year = world.date.year
    if world.date.abs_day < p.renewal_cooldown_until:
        return {"accepted": False, "reason": "negotiation_cooldown",
                "hint": "recent talks with this player stalled; pressing again "
                        "immediately will not move him and may raise his demands"}
    sd = world.params.world.season_settlement_day
    months = p.contract_months_left(year, world.date.day, sd) if p.club_id else 0
    ca_est = scouting.perceived_ca(world, cid, p)
    ask = wage_ask(world.params, ca_est, p.age(year), year, p.form, months)
    s = world.rng.stream("renewal", f"{cid}:{pid}", year)
    reservation = int(ask * s.uniform(0.9, 1.05))
    if offered_wage >= reservation:
        club = world.clubs[cid]
        signing_fee = 0
        if p.club_id is None:
            # v0.3: free agents demand a signing fee — the zero-fee channel
            # must not strictly dominate paid transfers
            signing_fee = int(world.params.market_ai.free_agent_fee_ratio
                              * scouting.perceived_value(world, cid, p))
            if club.cash < signing_fee:
                return {"accepted": False, "reason": "insufficient_cash",
                        "hint": "the player's signing fee exceeds your "
                                "available cash"}
        p.wage_cents = offered_wage
        p.contract_end_year = year + max(1, min(5, years))
        p.signed_year = year
        p.is_youth = False
        p.renewal_flagged = False
        if p.club_id is None:
            club.cash -= signing_fee
            club.cost_ytd += signing_fee
            p.club_id = cid
            p.morale = 65.0
            p.free_agent_since = None
            world.log_event("free_signing", {"club": cid, "fee": signing_fee,
                                             "player": pid,
                                             "wage": offered_wage})
            return {"accepted": True, "signing_fee": signing_fee,
                    "wage": offered_wage}
        else:
            p.morale = min(100.0, p.morale + 5)
            world.log_event("renewal", {"club": cid, "player": pid,
                                        "wage": offered_wage})
        return {"accepted": True, "wage": offered_wage}
    if offered_wage >= int(0.85 * reservation):
        p.morale = max(0.0, p.morale - 2)
        return {"accepted": False, "counter_wage": int(reservation * 1.05),
                "reason": "wants_more"}
    p.morale = max(0.0, p.morale + world.params.morale.negotiation_collapse)
    p.renewal_cooldown_until = world.date.abs_day + 180
    return {"accepted": False, "reason": "negotiation_collapsed"}


# --------------------------------------------------------------------------
# daily AI loop
# --------------------------------------------------------------------------

def daily(world) -> None:
    ma = world.params.market_ai
    market_day = (world.date.day % ma.market_day_interval == 0
                  and world.is_window_open())
    for cid in world.sorted_club_ids():
        if world.is_controlled(cid):
            continue
        club = world.clubs[cid]
        _squad_maintenance(world, club)
        if market_day and not _tier_skips_market_day(world, cid):
            _renewals(world, club)
            _try_buy(world, club)


def _tier_skips_market_day(world, cid: str) -> bool:
    """5/5/5 opponent tiers: an easy-tier manager only works every
    `market_day_stride`-th market day (deterministic day-index parity — no
    RNG). Untiered/medium clubs (stride 1) never skip: default unchanged."""
    tier = world._opponent_tier_cfg.get(cid)
    if tier is None:
        return False
    stride = int(tier.get("market_day_stride", 1))
    if stride <= 1:
        return False
    idx = world.date.day // world.params.market_ai.market_day_interval
    return idx % stride != 0


def _squad_maintenance(world, club) -> None:
    w = world.params.world
    seniors = world.seniors_of(club.cid)
    if len(seniors) < w.min_squad_size:
        _sign_best_free_agent(world, club)
    seniors = world.seniors_of(club.cid)
    if len(seniors) < 14:  # emergency: promote youths regardless of potential
        for p in world.youths_of(club.cid):
            if len(world.seniors_of(club.cid)) >= 14:
                break
            p.is_youth = False
            p.wage_cents = 8_000_000  # emergency minimum contract
            p.contract_end_year = world.date.year + 2
            p.signed_year = world.date.year
    if len(seniors) > world.params.market_ai.sell_surplus_squad:
        ranked = sorted(seniors, key=lambda p: (scouting.perceived_ca(world, club.cid, p), p.pid))
        for p in ranked[:2]:
            p.listed = True


def _sign_best_free_agent(world, club) -> None:
    frees = world.free_agents()
    if not frees:
        return
    headroom = _wage_headroom(world, club)
    wage_floor = market_wage_annual(world.params, 600, 25, world.date.year)
    ranked = sorted(frees, key=lambda p: (-scouting.perceived_ca(world, club.cid, p),
                                          p.pid))
    # walk down the list: sign the best AFFORDABLE free agent, not the best
    # one full stop (v0.3: the old single-candidate check almost never fired)
    for p in ranked[:5]:
        ca_est = scouting.perceived_ca(world, club.cid, p)
        ask = wage_ask(world.params, ca_est, p.age(world.date.year),
                       world.date.year, p.form, 0)
        fee = int(world.params.market_ai.free_agent_fee_ratio
                  * scouting.perceived_value(world, club.cid, p))
        if club.cash < fee:
            continue
        if ask <= max(headroom, wage_floor):
            renewal_response(world, club.cid, p.pid, ask, 2)
            return


def _renewals(world, club) -> None:
    sd = world.params.world.season_settlement_day
    year, day = world.date.year, world.date.day
    seniors = world.seniors_of(club.cid)
    if not seniors:
        return
    ranked = sorted(seniors, key=lambda p: (-scouting.perceived_ca(world, club.cid, p), p.pid))
    median_ca = scouting.perceived_ca(world, club.cid, ranked[len(ranked) // 2])
    for p in seniors:
        months = p.contract_months_left(year, day, sd)
        if months >= 12 or world.date.abs_day < p.renewal_cooldown_until:
            continue
        ca_est = scouting.perceived_ca(world, club.cid, p)
        if ca_est >= median_ca and _wage_headroom(world, club) > 0:
            ask = wage_ask(world.params, ca_est, p.age(year), year, p.form, months)
            renewal_response(world, club.cid, p.pid, ask, 3)
        elif not p.listed:
            p.listed = True


def _try_buy(world, club) -> None:
    ma = world.params.market_ai
    budget = _transfer_budget(world, club)
    headroom = _wage_headroom(world, club)
    if budget < 3 * 10**8 or headroom <= 0:  # < 3M cents
        return
    # weakest starting position group by belief
    from engine.sim.lineup import auto_lineup
    lineup = auto_lineup(world, club)
    if len(lineup) < 11:
        _sign_best_free_agent(world, club)
        return
    slot_ca: dict[str, list[int]] = {}
    for slot, p in lineup:
        slot_ca.setdefault(slot, []).append(scouting.perceived_ca(world, club.cid, p))
    need_pos = min(sorted(slot_ca), key=lambda s: min(slot_ca[s]))
    current_best = min(slot_ca[need_pos])
    # 5/5/5 opponent tiers: hard managers upgrade proactively on smaller
    # gaps, easy ones only stir for a big hole (mult 1.0 = today's default)
    need_gap = ma.buy_need_gap
    tier = world._opponent_tier_cfg.get(club.cid)
    if tier is not None:
        need_gap = int(round(need_gap * tier.get("buy_need_gap_mult", 1.0)))
    # candidate scan (deterministic order)
    best_target, best_score = None, 0.0
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id in (None, club.cid) or p.is_youth or p.pos != need_pos:
            continue
        if world.is_controlled(p.club_id) and not p.listed:
            continue  # AI only chases listed controlled-club players (v0 simplification)
        ca_est = scouting.perceived_ca(world, club.cid, p)
        if ca_est < current_best + need_gap and not p.listed:
            continue
        value_est = scouting.perceived_value(world, club.cid, p)
        if value_est > budget:
            continue
        wage_est = wage_ask(world.params, ca_est, p.age(world.date.year),
                            world.date.year, p.form, 24)
        if wage_est > headroom:
            continue
        score = ca_est / max(value_est, 1) * (2.0 if p.listed else 1.0)
        if score > best_score:
            best_target, best_score = p, score
    if best_target is None:
        return
    seller_cid = best_target.club_id
    ask = seller_ask(world, seller_cid, best_target.pid, club.cid)
    if ask > budget:
        return
    bid_mult = 0.88 + 0.12 * club.aggression
    seller_club = world.clubs[seller_cid]
    if seller_club.flip_rate() > ma.flip_rate_threshold:
        # sell-side flip friction (G6): buyers lowball known flippers
        bid_mult *= 1.0 - ma.flip_sell_penalty
    amount = quantize_ask(int(ask * bid_mult))
    offer = Offer(oid=world.ids.next("O"), player_id=best_target.pid,
                  buyer_cid=club.cid, seller_cid=seller_cid, amount=amount,
                  created_abs_day=world.date.abs_day,
                  expires_abs_day=world.date.abs_day + ma.offer_ttl_days)
    if world.is_controlled(seller_cid):
        world.market.offers[offer.oid] = offer
        world.push_inbox_for(seller_cid, "transfer_offer", {
            "amount": offer.amount, "buyer": club.cid, "oid": offer.oid,
            "player": best_target.pid}, action_required=True)
        return
    if sealed(world):
        # v0.3: bots ride the same windowed pipeline as seats (decision #19)
        # — queue and let resolve_sealed settle it at day end. Exposure cap
        # applies to bots exactly as to seats.
        if outstanding_exposure(world, club.cid) + offer.amount > club.cash:
            return
        world.market.offers[offer.oid] = offer
        return
    # AI-AI: resolve synchronously, up to 3 rounds
    for _ in range(ma.max_rounds):
        status = respond_as_seller(world, offer)
        if status == "accepted":
            if offer.amount <= _transfer_budget(world, club):
                execute_transfer(world, best_target.pid, club.cid, seller_cid,
                                 offer.amount)
            return
        if status == "countered":
            belief_cap = int(scouting.perceived_value(world, club.cid, best_target) * 1.15)
            if offer.counter_amount <= min(_transfer_budget(world, club), belief_cap):
                offer.amount = offer.counter_amount
                offer.status = "pending"
                continue
            return
        return


# --------------------------------------------------------------------------
# v0.3 sealed-bid resolution (DESIGN change 2) — runs daily before clearing
# --------------------------------------------------------------------------

def resolve_sealed(world) -> None:
    """Window-close resolution of bot-seller offers. Engine-internal, zero
    agent involvement, zero new stop types (zero-new-rounds constraint).

    Groups live offers by (bot seller, player): one live offer -> the exact
    v0.2 bilateral flow (async: counters land in the buyer's next packet);
    two or more -> first-price sealed auction. Clearing amounts are never
    pushed to non-participants (decision #9)."""
    if not sealed(world):
        return
    groups: dict[tuple, list[Offer]] = {}
    for oid in sorted(world.market.offers):
        o = world.market.offers[oid]
        if o.status not in ("pending", "countered"):
            continue
        if world.is_controlled(o.seller_cid):
            continue  # seat sellers resolve their own batch at their stop
        p = world.players.get(o.player_id)
        if p is None or p.club_id != o.seller_cid:
            o.status = "expired"
            continue
        groups.setdefault((o.seller_cid, o.player_id), []).append(o)
    for key in sorted(groups):
        offers = sorted(groups[key], key=lambda o: o.oid)
        if len(offers) == 1:
            # a countered single is waiting on ITS buyer, not on the seller
            if offers[0].status == "pending":
                _resolve_single(world, offers[0])
        else:
            _resolve_auction(world, key[0], key[1], offers)


def _buyer_cap(world, offer: Offer) -> int:
    buyer = world.clubs[offer.buyer_cid]
    if world.is_controlled(offer.buyer_cid):
        return buyer.cash
    return _transfer_budget(world, buyer)


def _resolve_single(world, offer: Offer) -> None:
    """The v0.2 bilateral semantics, executed at day end."""
    status = respond_as_seller(world, offer)
    buyer_ctrl = world.is_controlled(offer.buyer_cid)
    if status == "accepted":
        if offer.amount <= _buyer_cap(world, offer):
            execute_transfer(world, offer.player_id, offer.buyer_cid,
                             offer.seller_cid, offer.amount)
        else:
            offer.status = "rejected"
            if buyer_ctrl:
                world.push_inbox_for(offer.buyer_cid, "offer_failed_funds",
                                     {"oid": offer.oid,
                                      "player": offer.player_id})
        return
    if status == "countered":
        if buyer_ctrl:
            world.push_inbox_for(offer.buyer_cid, "offer_countered", {
                "counter_amount": offer.counter_amount, "oid": offer.oid,
                "player": offer.player_id})
            return
        # bot buyer: same continuation rule as the old sync loop, spread
        # over days — accept the counter if it clears budget and belief cap
        p = world.players.get(offer.player_id)
        cap = int(scouting.perceived_value(world, offer.buyer_cid, p) * 1.15)
        if offer.counter_amount <= min(_buyer_cap(world, offer), cap):
            offer.amount = offer.counter_amount
            offer.status = "pending"  # resolves next day
        return
    if status == "rejected" and buyer_ctrl:
        world.push_inbox_for(offer.buyer_cid, "offer_rejected",
                             {"oid": offer.oid, "player": offer.player_id})


def _resolve_auction(world, seller_cid: str, pid: str,
                     offers: list[Offer]) -> None:
    """First-price sealed auction among >=2 live offers on one player.

    Eligibility uses the same accept threshold as bilateral negotiation
    (per-buyer ask x seller's accept ratio, so repeat-pair markups and flip
    penalties still bite). Winner pays their own bid. Ties break via the
    auction_tiebreak RNG domain. Losers learn only who won (no amounts)."""
    ma = world.params.market_ai
    seller = world.clubs[seller_cid]
    if len(world.seniors_of(seller_cid)) <= world.params.world.min_squad_size:
        for o in offers:
            o.status = "rejected"
            if world.is_controlled(o.buyer_cid):
                world.push_inbox_for(o.buyer_cid, "offer_rejected",
                                     {"oid": o.oid, "player": pid})
        return
    thresh = _accept_ratio(ma, seller)
    eligible = []
    for o in offers:
        ask = seller_ask(world, seller_cid, pid, o.buyer_cid)
        o.ask_at_creation = ask
        if o.amount >= ask * thresh and o.amount <= _buyer_cap(world, o):
            eligible.append(o)
    if not eligible:
        # nobody clears: the highest bid proceeds to bilateral negotiation,
        # the rest are outbid (one-shot, no re-bid rounds)
        best_amt = max(o.amount for o in offers)
        tied = [o for o in offers if o.amount == best_amt]
        survivor = _tiebreak(world, pid, tied)
        for o in offers:
            if o is not survivor:
                _mark_outbid(world, o, survivor.buyer_cid, pid)
        if survivor.status == "pending":
            _resolve_single(world, survivor)
        return
    best_amt = max(o.amount for o in eligible)
    tied = [o for o in eligible if o.amount == best_amt]
    winner = _tiebreak(world, pid, tied)
    for o in offers:
        if o is not winner:
            _mark_outbid(world, o, winner.buyer_cid, pid)
    execute_transfer(world, pid, winner.buyer_cid, seller_cid, winner.amount)
    winner.status = "accepted"


def _tiebreak(world, pid: str, tied: list[Offer]) -> Offer:
    if len(tied) == 1:
        return tied[0]
    s = world.rng.stream("auction_tiebreak", pid, world.date.abs_day)
    return sorted(tied, key=lambda o: o.oid)[s.randint(0, len(tied) - 1)]


def _mark_outbid(world, offer: Offer, winner_cid: str, pid: str) -> None:
    offer.status = "outbid"
    if world.is_controlled(offer.buyer_cid):
        world.push_inbox_for(offer.buyer_cid, "offer_outbid", {
            "oid": offer.oid, "player": pid,
            "won_by": world.clubs[winner_cid].name})


# --------------------------------------------------------------------------
# pending offers upkeep (P7) — expiry, standing orders, deadline day
# --------------------------------------------------------------------------

def clear_market(world) -> None:
    ma = world.params.market_ai
    w = world.params.windows
    deadline = world.date.day in (w.summer_close - 1, w.winter_close - 1)
    for oid in sorted(world.market.offers):
        offer = world.market.offers[oid]
        if offer.status not in ("pending", "countered"):
            continue
        if not world.is_controlled(offer.seller_cid):
            continue
        p = world.players.get(offer.player_id)
        if p is None or p.club_id != offer.seller_cid:
            offer.status = "expired"
            continue
        handled = _apply_standing_orders(world, offer)
        if handled:
            continue
        if world.date.abs_day >= offer.expires_abs_day or deadline or \
                not world.is_window_open():
            offer.status = "expired"
            world.push_inbox_for(offer.seller_cid, "offer_expired",
                                 {"oid": offer.oid, "player": offer.player_id})
    # prune resolved offers older than 30 days to bound state size
    for oid in sorted(world.market.offers):
        offer = world.market.offers[oid]
        if offer.status != "pending" and offer.status != "countered" and \
                world.date.abs_day - offer.created_abs_day > 30:
            del world.market.offers[oid]


def _apply_standing_orders(world, offer: Offer) -> bool:
    """Whitelisted order types only (Red Team A1): single threshold + fixed action."""
    p = world.players[offer.player_id]
    seller_cid = offer.seller_cid
    my_value = scouting.perceived_value(world, seller_cid, p)
    for order in world.orders_of(seller_cid):
        if order["type"] == "reject_offers_below" and \
                offer.amount < int(my_value * order["mult"]):
            offer.status = "rejected"
            world.push_inbox_for(seller_cid, "offer_auto_rejected",
                                 {"oid": offer.oid, "player": offer.player_id})
            return True
        if order["type"] == "accept_offers_above" and \
                offer.amount >= int(my_value * order["mult"]):
            execute_transfer(world, offer.player_id, offer.buyer_cid,
                             offer.seller_cid, offer.amount)
            offer.status = "accepted"
            return True
    return False
