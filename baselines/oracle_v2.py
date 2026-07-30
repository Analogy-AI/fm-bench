"""oracle_v2: INTENTIONALLY PRIVILEGED information-ceiling baseline (aggressive).

This policy reads TRUE hidden state straight from game.world — the one thing
no legitimate agent can ever do — and, unlike the conservative oracle_v0, it
EXPLOITS that access to the hilt so its score is a genuine soft ceiling and
leak alarm for the capability ladder. Its privilege is information, NOT
power: every action still goes through the same tool interface, the same
per-stop offer budget, and the same engine legality checks as every agent.

The five exploits (each a pure deterministic read of world state — engine
RNG streams are pure functions of (seed, domain, entity, day), so "the
future die roll" of a same-tick negotiation is itself readable truth):

1. Transfer-market arbitrage — scan every player in the league, compute the
   seller's EXACT accept threshold (seller_ask x style/distress-adjusted
   accept_ratio) and bid exactly that: every purchase closes first-offer at
   the league-minimum price. Buys are ranked by true-worth vs effective cost
   including the score's unamortized-fee liability drag.
2. Exact-threshold contracts — renewals and free-agent signings offer the
   player's exact deterministic reservation wage (wage_ask x the "renewal"
   stream draw): zero negotiation waste, league-lowest wage bill, never a
   collapsed negotiation or cooldown. Renewals also exploit that an accepted
   offer RESETS the wage to the offer, so overpaid contracts get cut; and
   because offer_contract sits OUTSIDE the per-stop offer budget while every
   accepted renewal is +5 morale, the true best-16 are re-renewed at their
   known reservation whenever that is free (res <= current wage) — a
   standing morale farm that keeps the XI's morale multiplier near its cap.
3. Academy sniping — promote only true-PA stars, at the last stop before
   their academy terms lapse (academy development beats bench development),
   and invest in academy/training early so intake quality and growth compound.
4. Peak-timing sales — every incoming bid is countered at the buyer's EXACT
   ceiling min(transfer_budget, 1.15 x buyer's believed value), quantized to
   the price ladder: surplus and post-peak players sell at the maximum any
   buyer in the world will pay, before the deterministic decline curve bites.
   Flips (resale inside 360 owned days) are refused so the adaptive
   counterpressure never taxes the flywheel.
5. Zero-waste scheduling — the XI is re-solved every stop from true CA x
   form x morale x projected fatigue across all three formations, and the
   playing style counter-picks the (static, truthfully known) styles of the
   upcoming opponents net of the style-switch penalty.

Score-structure awareness (score/composite.py): trading profit and squad
growth land in the net-worth VA channel; sales never hollow the last-3-season
window (depth floors rise in the final season); excess cash (discounted 0.3
by the score) is recycled into facilities book value and squad assets;
contracts are renewed so nobody expires off the books before the final
snapshots and months-left value multipliers stay at 1.0.

Survival controller: board confidence is itself readable truth. Below
EMERGENCY_CONF (well above the engine's vote/fire thresholds) every margin
discipline yields to on-pitch strength NOW — unrestricted morale farm at
RENEW_WAGE_CAP, XI upgrades at EMERGENCY_MARGIN, no fringe spending — so the
oracle is never fired on any seed (the 555 opponent-tier world makes year-1
title races genuinely losable; an anchored ceiling must survive them all).

Engine code must never import this module — enforced by
tests/test_isolation.py::test_engine_never_imports_baselines. Oracle scores
are reported alongside, but never mixed into, the legitimate ladder.
"""

from __future__ import annotations

import math

from baselines.base import call, handle_draft
from engine.core.params import CENTS_PER_M, M
from engine.domain.contract import wage_ask
from engine.domain.player import POS_ATK_WEIGHT, POS_DEF_WEIGHT
from engine.domain.transfer import LADDER_STEP, quantize_ask, value_from_ability
from engine.sim import scouting
from engine.sim.lineup import FORMATION_SLOTS, pos_fit
from engine.sim.market_ai import _transfer_budget, seller_ask

# -- tuning ------------------------------------------------------------------
WAGE_CAP = 0.82          # bill/revenue ceiling after any signing (warning 0.85)
WAGE_CAP_RACE = 0.84     # ... relaxed while chasing squad dominance
RENEW_WAGE_CAP = 0.84    # a renewal may go slightly closer to the line
RESERVE_REV_FRAC = 0.05  # cash reserve as fraction of annual revenue
MIN_RESERVE_M = 5.0
BUY_MARGIN = 1.18        # true worth must beat effective cost by this factor
BUY_MARGIN_RACE = 0.92   # ... for XI upgrades while not yet dominant (honors
                         # are worth far more than the ln-compressed VA loss)
RAID_MARGIN = 0.75       # ... for a title rival's best-XI player (double swing)
DOMINANCE_GAP = 45       # fixed-point best-16 lead over the best rival we want
KEEP_DEPTH = 19          # never sell below this many seniors
FINAL_KEEP_DEPTH = 21    # ... and keep more in the final (snapshot) season
PROSPECT_AGE = 23
PROSPECT_GAP = 100       # fixed-point PA-CA headroom to count as a prospect
PROSPECT_MAX_SENIORS = 26  # prospects only fill spare slots, never crowd stars
INVENTORY_MAX_SENIORS = 28  # pure trading stock only fills the last free slots
PROMOTE_PA_FLOOR = 1250  # fixed-point PA to earn a senior squad slot
JUNK_VALUE_M = 1.2       # sub-scale assets get released to free trading slots
PROJ_FATIGUE = 15.0      # mid-stretch fatigue projection for lineup choice
MORALE_FARM_BELOW = 92.0  # re-renew (+5 morale each) until players sit here
FARM_WAGE_CAP = 0.79     # farm renewals stop well short of the warning line
FARM_BUMP_MAX = 1.00     # never farm a raise beyond this multiple of wage
EMERGENCY_CONF = 32.0    # board confidence below this = survival mode (the
                         # vote zone starts at 20, hard fire at 10; reacting
                         # at 32 leaves a full month of margin)
EMERGENCY_MARGIN = 0.45  # any XI upgrade at any price while in survival mode
FRINGE_WAGE_CAP = 0.70   # prospects/inventory never push the bill above this
FORMATIONS = ("4-3-3", "4-4-2", "3-5-2")
STYLES = ("possession", "gegenpress", "lowblock")


def oracle_v2(game, packet: dict) -> None:
    if handle_draft(game, packet, "balanced"):
        return
    world = game.world
    me = world.player_club_id
    # survival controller: board confidence is readable truth. Below
    # EMERGENCY_CONF every margin discipline yields to on-pitch strength NOW
    # (unrestricted morale farm, near-free XI upgrades, no fringe spending):
    # an information oracle must never be fired.
    _handle_offers(game, world, me)        # 4: peak-timed sales at buyer ceiling
    _renewals(game, world, me)             # 2: exact-threshold renewals
    _youth(game, world, me)                # 3: academy sniping
    _release_junk(game, world, me)         # slots are trading capital
    _sign_free_agents(game, world, me)     # 2: exact-threshold FA sniping
    if world.is_window_open():
        _buy(game, world, me)              # 1: exact-price market arbitrage
    _manage_listings(game, world, me)      # 4: put surplus in the shop window
    _invest(game, world, me)               # recycle discounted excess cash
    _lineup_and_tactics(game, world, me)   # 5: zero-waste scheduling


# -- truth helpers (the privilege lives here) ---------------------------------

def _months_left(world, p) -> int:
    if p.club_id is None:
        return 0
    sd = world.params.world.season_settlement_day
    return p.contract_months_left(world.date.year, world.date.day, sd)


def _tv(world, p, months: int | None = None) -> int:
    """True market value in cents at (optionally overridden) months left."""
    m = _months_left(world, p) if months is None else months
    return value_from_ability(world.params, p.ca(), p.age(world.date.year),
                              world.date.year, m, p.form)


def _v_of_ca(world, ca: int, age: int = 26) -> int:
    return value_from_ability(world.params, max(ca, 10), age,
                              world.date.year, 30, 6.5)


def _nth_ca(players, n: int) -> int:
    cas = sorted((p.ca() for p in players), reverse=True)
    return cas[n] if len(cas) > n else 0


def _proj_ca(world, p, yrs: float) -> int:
    """Deterministic CA projection: training growth to PA, post-peak decline."""
    dev = world.params.development
    ag = world.params.aging
    peak, annual_pct, cliff, cliff_pct = ag.groups[p.pos]
    ca = float(p.ca())
    age0 = p.age(world.date.year)
    steps = max(1, int(round(yrs * 2)))
    for i in range(steps):
        a = age0 + i * 0.5
        gap = p.pa - ca
        if gap > 0:
            mult = 0.5
            for cap in sorted(int(k) for k in dev.age_mult):
                if a <= cap:
                    mult = dev.age_mult[cap]
                    break
            # ~31 training Mondays/season, ~0.75 playing-time factor, our tq
            ca += min(gap, 0.5 * 31 * dev.k_dev_weekly * mult * (gap / 1000.0)
                      * 0.75 * 1.1 * p.professionalism)
        a_eff = a + p.decay_headstart - p.aging_offset
        if a_eff > peak:
            rate = annual_pct + (cliff_pct if a_eff >= cliff else 0.0)
            ca *= 1.0 - 0.5 * (rate * 0.34 + ag.tech_decline_pct * 0.36) / 100.0
    return int(ca)


def _keep_score(world, p) -> int:
    """Rank players for depth by where they will be in ~1.5 years."""
    return _proj_ca(world, p, 1.5)


def _hold_worth(world, p, bar11: int) -> int:
    """Score-channel worth (cents) of having this player on our books."""
    year = world.date.year
    age = p.age(year)
    yrs_left = max(0.5, world.years - year + (300 - world.date.day) / 360.0)
    horizon = min(2.5, yrs_left)
    v_now = _tv(world, p, 30)
    ca2 = _proj_ca(world, p, horizon)
    v_fut = value_from_ability(world.params, ca2, age + int(round(horizon)),
                               year + int(round(horizon)), 30, p.form)
    worth = 0.85 * (0.55 * v_now + 0.45 * v_fut)
    if p.ca() > bar11:  # immediate XI upgrade: honors premium
        worth += 0.35 * max(v_now - _v_of_ca(world, bar11), 0)
    return int(worth)


def _fee_drag(world, contract_years: int) -> float:
    """Mean unamortized-fee fraction across the last-3-season snapshots."""
    year = world.date.year
    end = year + contract_years
    tot = 0.0
    for y in range(world.years - 2, world.years + 1):
        if y >= year:
            tot += min(max(0, end - y), contract_years) / contract_years
    return tot / 3.0


def _accept_price(world, me: str, p) -> int:
    """EXACT first-offer accept threshold of p's club, in cents (exploit 1)."""
    seller = world.clubs[p.club_id]
    ask = seller_ask(world, p.club_id, p.pid, me)
    ratio = world.params.market_ai.accept_ratio
    if seller.cash < 0:
        ratio *= 0.8
    if seller.negotiation_style == "hard":
        ratio *= 1.05
    elif seller.negotiation_style == "soft":
        ratio *= 0.95
    return int(math.ceil(max(ask, 1) * ratio)) + 20_000  # float-safety margin


def _reservation(world, cid: str, p, months: int) -> int:
    """EXACT deterministic reservation wage of a renewal / FA offer (exploit 2)."""
    ca_est = scouting.perceived_ca(world, cid, p)
    ask = wage_ask(world.params, ca_est, p.age(world.date.year),
                   world.date.year, p.form, months)
    s = world.rng.stream("renewal", f"{cid}:{p.pid}", world.date.year)
    return int(ask * s.uniform(0.9, 1.05))


def _buyer_ceiling(world, buyer_cid: str, p) -> int:
    """EXACT maximum a buyer will pay for p right now (exploit 4)."""
    buyer = world.clubs[buyer_cid]
    return min(_transfer_budget(world, buyer),
               int(scouting.perceived_value(world, buyer_cid, p) * 1.15))


def _best_rung(cap: int) -> int:
    """Largest quantize_ask ladder value <= cap (0 if none)."""
    if cap < 1_000_000:
        return 0
    k = int(math.floor(math.log(cap / 1_000_000) / math.log(LADDER_STEP)))
    while k >= 0:
        r = int(1_000_000 * LADDER_STEP ** k)
        if r <= cap and quantize_ask(r) == r:
            return r
        k -= 1
    return 0


def _emergency(world, me: str) -> bool:
    """Survival mode: the board is drifting toward the fire/vote thresholds."""
    return world.clubs[me].board_confidence < EMERGENCY_CONF


def _arms_race(world, me: str) -> bool:
    """True until our true best-16 clearly dominates every rival's."""
    mine = world.squad_strength(me)
    rival = max(world.squad_strength(cid) for cid in world.sorted_club_ids()
                if cid != me)
    return mine < rival + DOMINANCE_GAP


def _finances(world, me: str) -> tuple[int, int, int]:
    club = world.clubs[me]
    revenue = max(club.revenue_last_season, club.rev_ytd, 1)
    reserve = max(M(MIN_RESERVE_M), int(RESERVE_REV_FRAC * revenue))
    return club.cash, revenue, reserve


def _offers_left(game) -> int:
    return game.world.params.stops.offer_budget - game.offers_this_stop


# -- 4: peak-timing sales at the buyer's exact ceiling -------------------------

def _sell_floor(world, p, rank: int, ca_rank: int,
                n_seniors: int) -> int | None:
    """Minimum acceptable fee for p (cents), or None = not for sale.

    ca_rank is the player's rank by CURRENT true CA: anyone inside the best 16
    feeds both the match engine and the board's anti-sandbag strength floor
    (X5), so selling him turns losses into 'upsets' all season — only a
    blow-away bid for an aging star is worth that.
    """
    year = world.date.year
    if p.bought_fee_cents > 0 and (world.date.abs_day - p.bought_abs_day
                                   < world.params.market_ai.flip_owned_days):
        return None  # never flip: sell-side counterpressure taxes the flywheel
    min_depth = FINAL_KEEP_DEPTH if year >= world.years else KEEP_DEPTH
    if n_seniors <= min_depth:
        return None
    age = p.age(year)
    v = _tv(world, p)
    if age <= 22 and p.pa - p.ca() >= 150 and p.pa >= 1200:
        return int(1.8 * v)               # growth asset: the flywheel core
    if ca_rank < 16:                      # board floor + match strength
        if age >= 31 and rank >= 8 and year < world.years:
            return int(1.6 * v)           # blow-away bid for a fading star
        return None
    if rank >= min_depth:                 # true surplus beyond the depth floor
        base = 0.9 if age < 30 else (0.75 if age < 33 else 0.6)
    else:                                 # keep-worthy depth: overpays only
        base = 1.15 if age < 31 else 0.95
    if year >= world.years:               # never hollow the final snapshots
        base = max(base, 1.3)
    return int(base * v)


def _handle_offers(game, world, me: str) -> None:
    ma = world.params.market_ai
    seniors = world.seniors_of(me)
    ranked = sorted(seniors, key=lambda p: (-_keep_score(world, p), p.pid))
    rank = {p.pid: i for i, p in enumerate(ranked)}
    by_ca = sorted(seniors, key=lambda p: (-p.ca(), p.pid))
    ca_rank = {p.pid: i for i, p in enumerate(by_ca)}
    # best sale first: the offer budget must go to the highest-value closings
    live = []
    for oid in sorted(world.market.offers):
        o = world.market.offers[oid]
        if o.status != "pending" or o.seller_cid != me:
            continue
        p = world.players.get(o.player_id)
        if p is None or p.club_id != me:
            continue
        ceiling = _buyer_ceiling(world, o.buyer_cid, p)
        proceeds = max(o.amount, _best_rung(ceiling))
        live.append((-proceeds, oid, o, p))
    live.sort()
    for _, oid, o, p in live:
        if _offers_left(game) <= 0:
            return
        if world.players.get(p.pid) is None or p.club_id != me:
            continue  # already sold to a better bidder this stop
        floor = _sell_floor(world, p, rank.get(p.pid, 99),
                            ca_rank.get(p.pid, 99),
                            len(world.seniors_of(me)))
        if floor is None:
            continue  # let it expire; rejecting would waste offer budget
        rung = _best_rung(_buyer_ceiling(world, o.buyer_cid, p))
        if max(o.amount, rung) < floor:
            continue
        if rung > o.amount and o.rounds < ma.max_rounds:
            call(game, "respond_to_offer",
                 {"action": "counter", "offer_id": oid,
                  "counter_amount_m": (rung + 5_000) / CENTS_PER_M})
        else:
            call(game, "respond_to_offer",
                 {"action": "accept", "offer_id": oid})


def _manage_listings(game, world, me: str) -> None:
    """The shop window: exactly the players _sell_floor is willing to move."""
    year = world.date.year
    seniors = world.seniors_of(me)
    ranked = sorted(seniors, key=lambda p: (-_keep_score(world, p), p.pid))
    rank = {p.pid: i for i, p in enumerate(ranked)}
    by_ca = sorted(seniors, key=lambda p: (-p.ca(), p.pid))
    ca_rank = {p.pid: i for i, p in enumerate(by_ca)}
    for p in seniors:
        want = _sell_floor(world, p, rank[p.pid], ca_rank[p.pid],
                           len(seniors)) is not None
        if p.listed != want:
            call(game, "list_player", {"listed": want, "player_id": p.pid})


# -- 2: exact-threshold contracts ----------------------------------------------

def _renewals(game, world, me: str) -> None:
    year = world.date.year
    seniors = world.seniors_of(me)
    ranked = sorted(seniors, key=lambda p: (-_keep_score(world, p), p.pid))
    rank = {p.pid: i for i, p in enumerate(ranked)}
    by_ca = sorted(seniors, key=lambda p: (-p.ca(), p.pid))
    ca_rank = {p.pid: i for i, p in enumerate(by_ca)}
    _, revenue, _ = _finances(world, me)
    emergency = _emergency(world, me)
    for p in sorted(seniors, key=lambda q: q.pid):
        months = _months_left(world, p)
        age = p.age(year)
        if age >= 33:
            continue  # decline/retirement zone: sell, never extend
        if world.date.abs_day < p.renewal_cooldown_until:
            continue  # unreachable (we always meet reservation), belt & braces
        res = _reservation(world, me, p, months)
        wage_cut = res < int(p.wage_cents * 0.9)
        due = months <= 21
        # morale farm: offer_contract is outside the per-stop offer budget,
        # and an accepted renewal is +5 morale (engine: renewal_response).
        # The reservation is a deterministic read, so every farm offer is
        # accepted first try. A ~95-morale XI plays ~+8% stronger
        # (morale_mult 0.9 + m/500) — team-wide, that beats any transfer at
        # the price of bumping the best-16's wages to today's reservation a
        # little early (the due renewal would pay it anyway; the wage-cap
        # check below still gates every offer). Farming a BOUGHT player also
        # re-books his unamortized fee at the next snapshots (~1 VA pt), a
        # fair trade against title odds (~+100 H per flipped race), so the
        # farm covers exactly the true best-16 who feed the match engine.
        farm = (ca_rank.get(p.pid, 99) < 16 and p.morale <= MORALE_FARM_BELOW
                and ((res <= int(p.wage_cents * FARM_BUMP_MAX)
                      and p.bought_fee_cents == 0)
                     # survival mode: +5 morale on the whole XI, whatever the
                     # wage bump or fee re-booking costs — VA points are
                     # worthless to a fired manager
                     or emergency))
        if not (due or wage_cut or farm):
            continue
        if p.bought_fee_cents > 0 and p.contract_end_year > world.years \
                and not farm:
            continue  # renewal would resurrect the fee liability for nothing
        keeper = rank.get(p.pid, 99) < KEEP_DEPTH or (
            age <= PROSPECT_AGE and p.pa - p.ca() >= PROSPECT_GAP)
        v = _tv(world, p, 30)
        if not keeper and not farm:
            # fringe: extend only for a wage cut, or to keep a sellable asset
            # on the books through its expiry (both are pure wins)
            if due and v < M(1.5):
                continue
            if not due and not wage_cut:
                continue
            if v < M(1.0):
                continue
        bill = world.annual_wage_bill(me)
        cap = (RENEW_WAGE_CAP if (due or wage_cut or emergency)
               else FARM_WAGE_CAP)
        if bill - p.wage_cents + res > cap * revenue:
            continue
        years = 5 if age <= 27 else (3 if age <= 30 else 2)
        call(game, "offer_contract",
             {"player_id": p.pid, "wage_m_per_year": (res + 20_000) / CENTS_PER_M,
              "years": years})


def _sign_free_agents(game, world, me: str) -> None:
    params = world.params
    year = world.date.year
    seniors = world.seniors_of(me)
    bar11 = _nth_ca(seniors, 10)
    bar16 = _nth_ca(seniors, 15)
    contested = _arms_race(world, me) or _emergency(world, me)
    cands = []
    for p in world.free_agents():
        age = p.age(year)
        if age >= 31:
            continue
        fee = int(params.market_ai.free_agent_fee_ratio
                  * scouting.perceived_value(world, me, p))
        res = _reservation(world, me, p, 0)
        worth = _hold_worth(world, p, bar11)
        cost = fee + int(1.3 * res)  # signings carry NO fee liability
        upgrade = p.ca() > bar16
        prospect = (age <= PROSPECT_AGE and p.pa - p.ca() >= PROSPECT_GAP
                    and p.pa >= bar16)
        # pure trading stock: the fee is keyed to OUR belief, so a player the
        # scouts underrate is bought at a fraction of his true resale value.
        # While the title race (or the board) is undecided, fringe stock must
        # not gorge on the wage/cash headroom that buys real XI strength.
        inventory = (worth >= int(2.2 * cost) and worth >= M(2.0)
                     and not contested)
        if not (upgrade or prospect or inventory):
            continue
        if worth < int(cost * 1.1) + M(1.0):
            continue
        max_seniors = (params.world.max_squad_size - 1
                       if upgrade or prospect else INVENTORY_MAX_SENIORS)
        cands.append((-(worth - cost), p.pid, fee, res, max_seniors,
                      upgrade, p))
    cands.sort()
    for _, _, fee, res, max_seniors, upgrade, p in cands:
        if len(world.seniors_of(me)) >= max_seniors:
            continue
        cash, revenue, reserve = _finances(world, me)
        if fee > cash - reserve:
            continue
        wage_cap = WAGE_CAP if upgrade else FRINGE_WAGE_CAP
        if world.annual_wage_bill(me) + res > wage_cap * revenue:
            continue
        yrs = 4 if p.age(year) <= 26 else 2
        call(game, "offer_contract",
             {"player_id": p.pid, "wage_m_per_year": (res + 20_000) / CENTS_PER_M,
              "years": yrs})


# -- 1: transfer-market arbitrage at the exact accept price --------------------

def _buy_margin(world, me: str, race: bool, emergency: bool, upgrade: bool,
                star: bool, prospect: bool, raid: bool,
                seniors_n: int) -> float | None:
    """Required worth/eff_cost margin for a candidate, None = ineligible."""
    if emergency:
        # survival mode: XI strength at almost any price, nothing else
        return EMERGENCY_MARGIN if (upgrade or raid) else None
    if raid:
        return RAID_MARGIN
    if upgrade:
        return BUY_MARGIN_RACE if race else BUY_MARGIN
    if prospect:
        # fills spare slots only, steeper bar while racing for the title
        if seniors_n >= PROSPECT_MAX_SENIORS:
            return None
        return 1.5 if race else BUY_MARGIN
    # depth bargain / trading stock: only screaming mispricings
    if seniors_n >= INVENTORY_MAX_SENIORS:
        return None
    return 1.65


def _is_raid(world, me: str, p, bar11: int) -> bool:
    """A title rival's best-XI player: buying him swings the race twice over."""
    if p.ca() <= bar11 + 20:
        return False
    rival = world.squad_strength(p.club_id)
    if rival < world.squad_strength(me) - 25:
        return False
    top11 = _nth_ca(world.seniors_of(p.club_id), 10)
    return p.ca() >= top11


def _buy(game, world, me: str) -> None:
    params = world.params
    year = world.date.year
    squad_sizes: dict[str, int] = {}
    cands = []
    race = _arms_race(world, me)
    emergency = _emergency(world, me)
    bar11 = _nth_ca(world.seniors_of(me), 10)
    bar16 = _nth_ca(world.seniors_of(me), 15)
    seniors_n = len(world.seniors_of(me))
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id in (None, me) or p.is_youth:
            continue
        age = p.age(year)
        if age >= 32:
            continue
        n = squad_sizes.get(p.club_id)
        if n is None:
            n = len(world.seniors_of(p.club_id))
            squad_sizes[p.club_id] = n
        if n <= params.world.min_squad_size:
            continue  # seller would auto-reject
        upgrade = p.ca() > bar11 + 20
        star = p.ca() > bar11 + 60
        prospect = (age <= PROSPECT_AGE and p.pa - p.ca() >= PROSPECT_GAP
                    and p.pa >= bar16)
        bargain = p.ca() > bar16 + 20 and age <= 29
        if not (upgrade or prospect or bargain):
            continue
        raid = race and age <= 30 and _is_raid(world, me, p, bar11)
        margin = _buy_margin(world, me, race, emergency, upgrade, star,
                             prospect, raid, seniors_n)
        if margin is None:
            continue
        price = _accept_price(world, me, p)
        cy = 4 if age <= 25 else 3
        eff_cost = int(price * (1.0 + _fee_drag(world, cy)))
        worth = _hold_worth(world, p, bar11)
        if worth < int(eff_cost * margin):
            continue
        # while racing, the budget goes to title-swinging strength FIRST
        # (raids, then XI upgrades), value ratio second; once dominant the
        # ordering is pure arbitrage yield
        pri = (0 if raid else 1 if upgrade else 2) if race else 0
        cands.append((pri, -(worth / max(eff_cost, 1)), pid, p))
    cands.sort()
    for _, _, _, p in cands:
        if _offers_left(game) <= 0:
            return
        if len(world.seniors_of(me)) >= params.world.max_squad_size - 1:
            return
        cash, revenue, reserve = _finances(world, me)
        price = _accept_price(world, me, p)  # markup moves after each purchase
        if price > cash - reserve:
            continue
        race = _arms_race(world, me)
        my_seniors = world.seniors_of(me)
        bar11 = _nth_ca(my_seniors, 10)
        bar16 = _nth_ca(my_seniors, 15)
        age = p.age(year)
        upgrade = p.ca() > bar11 + 20
        star = p.ca() > bar11 + 60
        prospect = (age <= PROSPECT_AGE and p.pa - p.ca() >= PROSPECT_GAP
                    and p.pa >= bar16)
        raid = race and age <= 30 and _is_raid(world, me, p, bar11)
        margin = _buy_margin(world, me, race, emergency, upgrade, star,
                             prospect, raid, len(my_seniors))
        if margin is None:
            continue
        wage_cap = (WAGE_CAP_RACE if (race and (star or raid)) or emergency
                    else WAGE_CAP)
        new_wage = int(wage_ask(params, scouting.perceived_ca(world, me, p),
                                p.age(year), year, p.form, 24) * 1.05)
        if world.annual_wage_bill(me) + new_wage > wage_cap * revenue:
            continue
        cy = 4 if p.age(year) <= 25 else 3
        worth = _hold_worth(world, p, bar11)
        if worth < int(price * (1.0 + _fee_drag(world, cy)) * margin):
            continue
        env = call(game, "make_transfer_offer",
                   {"amount_m": price / CENTS_PER_M, "player_id": p.pid})
        d = env.get("data") or {}
        if d.get("status") == "countered":  # unreachable by construction
            call(game, "respond_to_offer",
                 {"action": "withdraw", "offer_id": d["oid"]})


# -- 3: academy sniping ---------------------------------------------------------

def _youth(game, world, me: str) -> None:
    params = world.params
    year = world.date.year
    day = world.date.day
    seniors = world.seniors_of(me)
    bar11 = _nth_ca(seniors, 10)
    bar16 = _nth_ca(seniors, 15)
    for p in sorted(world.youths_of(me), key=lambda q: (-q.pa, q.pid)):
        if len(world.seniors_of(me)) >= params.world.max_squad_size - 1:
            return
        last_chance = p.contract_end_year <= year and day >= 240
        monster = p.ca() >= bar11  # already an XI player: play him now
        keeper = p.pa >= max(PROMOTE_PA_FLOOR, bar16 - 50)
        if not (monster or (last_chance and keeper)):
            continue
        call(game, "promote_youth", {"player_id": p.pid})


def _release_junk(game, world, me: str) -> None:
    """Squad slots are trading capital: shed sub-scale assets blocking them."""
    seniors = world.seniors_of(me)
    if len(seniors) < INVENTORY_MAX_SENIORS:
        return
    ranked = sorted(seniors, key=lambda p: (-_keep_score(world, p), p.pid))
    by_ca = sorted(seniors, key=lambda p: (-p.ca(), p.pid))
    ca_rank = {p.pid: i for i, p in enumerate(by_ca)}
    released = 0
    for p in reversed(ranked):  # worst first
        if released >= 2 or len(world.seniors_of(me)) <= KEEP_DEPTH + 2:
            return
        age = p.age(world.date.year)
        prospect = age <= 22 and p.pa - p.ca() >= 150 and p.pa >= 1200
        if (prospect or ca_rank[p.pid] < 20 or age < 20
                or _tv(world, p) >= M(JUNK_VALUE_M)
                or p.wage_cents >= M(1.5)):
            continue
        call(game, "release_player", {"player_id": p.pid})
        released += 1


# -- facilities: recycle 0.3-discounted excess cash into book value -------------

def _invest(game, world, me: str) -> None:
    params = world.params
    club = world.clubs[me]
    in_prog = {pr["kind"] for pr in club.facility_projects}
    cash, revenue, _ = _finances(world, me)
    reserve = max(M(MIN_RESERVE_M), int(0.15 * revenue))
    if club.academy_level < 3 and "academy" not in in_prog:
        cost = M(params.academy.cost_upgrade_M[club.academy_level + 1])
        if cash - cost >= reserve:
            call(game, "invest", {"kind": "academy"})
            cash -= cost
    if club.training_level < 3 and "training" not in in_prog:
        cost = M(params.training_facility.cost_upgrade_M[club.training_level + 1])
        if cash - cost >= reserve:
            call(game, "invest", {"kind": "training"})
            cash -= cost
    if world.years - world.date.year >= 8 and "stadium" not in in_prog:
        st = params.stadium
        new_cap = club.stadium_capacity + st.expand_step
        cost = int(M(st.cost_base_M) * (new_cap / st.cap_ref) ** st.cost_pow)
        if cash - cost >= 3 * world.annual_wage_bill(me) + M(20):
            call(game, "invest", {"kind": "stadium"})


# -- 5: zero-waste scheduling ----------------------------------------------------

def _effective(world, style: str, p, slot: str) -> float:
    fat = world.params.fatigue
    m = world.params.match
    mo = world.params.morale
    f = p.fatigue + PROJ_FATIGUE
    fit = fat.thresh2_perf if f > fat.thresh2 else \
        (fat.thresh1_perf if f > fat.thresh1 else 1.0)
    t = max(0.0, min(1.0, (p.form - 5.8) / 1.4))
    form_m = m.form_mult_min + (m.form_mult_max - m.form_mult_min) * t
    morale_m = mo.effect_base + p.morale / mo.effect_span
    mult = pos_fit(world.params, p.pos, slot) * fit * form_m * morale_m
    if p.style_pref != style:
        mult *= 1.0 - m.style_misfit_penalty
    return p.ca() * mult


def _pick_xi(world, me: str, formation: str, style: str):
    """Greedy true-strength XI for a formation; returns (score, [(slot, p)])."""
    pool = [p for p in world.seniors_of(me) if p.available()]
    xi = []
    used = set()
    for slot in FORMATION_SLOTS[formation]:
        best, best_e = None, -1.0
        for p in pool:
            if p.pid in used:
                continue
            e = _effective(world, style, p, slot)
            if e > best_e:
                best, best_e = p, e
        if best is None:
            break
        used.add(best.pid)
        xi.append((slot, best))
    if len(xi) < 11:
        return -1e18, xi
    atk_n = atk_d = def_n = def_d = 0.0
    for slot, p in xi:
        e = _effective(world, style, p, slot)
        atk_n += e * POS_ATK_WEIGHT[slot]
        atk_d += POS_ATK_WEIGHT[slot]
        def_n += e * POS_DEF_WEIGHT[slot]
        def_d += POS_DEF_WEIGHT[slot]
    score = math.log(max(atk_n / atk_d, 1e-6)) + math.log(max(def_n / def_d, 1e-6))
    return score, xi


def _upcoming_opponent_styles(world, me: str, k: int = 4) -> list[str]:
    club = world.clubs[me]
    w = world.params.world
    out: list[str] = []
    for rnd in range(w.rounds):
        day = w.first_match_day + rnd * w.match_interval_days
        if day <= world.date.day:
            continue
        for home, away in world.fixtures[club.division][rnd]:
            if me in (home, away):
                opp = away if home == me else home
                out.append(world.clubs[opp].style)
        if len(out) >= k:
            break
    return out[:k]


def _lineup_and_tactics(game, world, me: str) -> None:
    club = world.clubs[me]
    rps = world.params.tactics.rps
    opp_styles = _upcoming_opponent_styles(world, me)
    best = None
    for style in STYLES:
        for formation in FORMATIONS:
            score, xi = _pick_xi(world, me, formation, style)
            if len(xi) < 11:
                continue
            # matchup utility over the visible stretch, alpha-scaled strength
            util = 1.35 * score
            for i, os in enumerate(opp_styles):
                net = rps[style][os] - rps[os][style]
                if style != club.style and i < 2:
                    net *= 0.5  # style-switch penalty on the first two matches
                util += net / max(len(opp_styles), 1)
            key = (util, style == club.style, formation == club.formation)
            if best is None or key > best[0]:
                best = (key, formation, style, xi)
    if best is None:
        return
    _, formation, style, xi = best
    call(game, "set_tactics", {"formation": formation, "style": style})
    # natural-position players first so the engine seats everyone at full fit
    natural = [p.pid for slot, p in xi if p.pos == slot]
    off_pos = [p.pid for slot, p in xi if p.pos != slot]
    ids = natural + off_pos
    if len(ids) == 11:
        call(game, "set_lineup", {"player_ids": ids})
