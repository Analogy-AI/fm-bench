"""Equal-endowment pre-season draft (protocol v0.3, change 1).

Opt-in via params.draft.enabled; the default-off path touches no state and
consumes no RNG, so legacy worlds stay byte-identical (tier_mix pattern).

Every club — controlled seats and scripted bots alike — picks an initial
squad from ONE shared list of 50 templates under one budget (decision #2:
independent copies, same exam). Unpicked templates vanish. Leftover budget
plus a fixed stipend becomes opening cash (decision #4). Submissions must
have >= min_picks picks incl. a goalkeeper and fit the budget (decision #15).

I3 note: bot draft policies below judge templates through
scouting.perceived_ca only — never true attributes. The AST isolation test
covers this module.
"""

from __future__ import annotations

from engine.actions.validate import ValidationError
from engine.core.params import M
from engine.domain import names
from engine.domain.contract import market_wage_annual
from engine.domain.player import Player
from engine.sim import scouting

# position cycles per tier: every tier offers GK cover and a spread of roles,
# so any budget mix can field an XI (floor tier alone covers a full squad)
_TIER_POS = {
    "top":      ["ST", "CM", "CB", "W", "AM", "GK", "FB", "DM"],
    "starter":  ["GK", "CB", "FB", "CM", "ST", "W", "DM", "AM", "CB", "CM",
                 "ST", "W"],
    "rotation": ["GK", "CB", "CB", "FB", "FB", "DM", "CM", "CM", "W", "W",
                 "AM", "ST", "ST", "CB", "CM"],
    "floor":    ["GK", "GK", "CB", "CB", "FB", "FB", "DM", "CM", "CM", "W",
                 "W", "AM", "ST", "ST", "CB"],
}


def enabled(params) -> bool:
    d = params.get("draft") or {}
    return bool(d.get("enabled", False))


# ---------------------------------------------------------------------------
# pool generation (called from worldgen when enabled)
# ---------------------------------------------------------------------------

def generate_pool(world) -> None:
    params = world.params
    cfg = params.draft
    stream = world.rng.stream("draft", "pool", 0)
    idx = 0
    for tier in cfg.tiers:  # list of {name,count,price_lo_m,price_hi_m,ca_mu,ca_sigma}
        name = str(tier["name"])
        cycle = _TIER_POS[name]
        for i in range(int(tier["count"])):
            idx += 1
            tid = f"T{idx:02d}"
            pos = cycle[i % len(cycle)]
            pg = params.playergen
            nationality = stream.choice(names.NATIONALITIES)
            age = stream.weighted_choice(
                list(range(pg.age_min, pg.age_max + 1)),
                [1, 2, 3, 4, 5, 6, 6, 6, 6, 6, 5, 4, 3, 2, 1, 1, 1, 1][
                    : pg.age_max - pg.age_min + 1])
            ca = int(stream.truncgauss(tier["ca_mu"], tier["ca_sigma"],
                                       300, 1900))
            if age <= 20:
                ca = int(ca * stream.uniform(0.70, 0.85))
            pa = int(min(pg.pa_max, max(ca, ca + stream.truncgauss(
                pg.pa_extra_mu * max(0.0, (27 - age) / 10.0),
                pg.pa_sigma, 0, 900))))
            spread_t = stream.uniform(0.9, 1.1)
            spread_p = stream.uniform(0.9, 1.1)
            tpl = Player(
                pid=tid,
                name=tid,  # neutral card id; copies are named per club
                nationality=nationality,
                pos=pos,
                birth_year=1 - age,
                true_phys=max(10, min(2000, int(ca * spread_p))),
                true_tech=max(10, min(2000, int(ca * spread_t))),
                true_ment=max(10, min(2000, int(ca * (3.0 - spread_p - spread_t)))),
                pa=pa,
                injury_proneness=round(0.5 + 2.5 * stream.uniform(0.0, 1.0) ** 2, 3),
                professionalism=round(stream.uniform(0.5, 1.5), 3),
                consistency=round(stream.uniform(0.7, 1.3), 3),
                aging_offset=round(stream.uniform(-1.0, 2.0), 2),
                style_pref=stream.choice(params.tactics.styles),
                rating_bias=round(stream.gauss(
                    0.0, params.match.rating_persistent_bias_sigma), 4),
                club_id=None,
                wage_cents=0,
                contract_end_year=1,  # replaced per copy at resolution
                signed_year=1,
            )
            # preset contract card: wage quoted from a template-side noisy CA
            # (never exact truth — C1 pattern), quantized; term 2-5 years
            wm = params.wagemodel
            ca_quote = ca + int(stream.gauss(0.0, 40.0))
            # per-tier wage premium: stars demand superstar wages, so a
            # stars-plus-scrubs draft pays for its XI through the wage bill
            # (the anti-greedy pricing lever behind gate decision #3)
            wage = int(market_wage_annual(params, ca_quote, age, 1)
                       * float(tier.get("wage_mult", 1.0))
                       * (1.0 + stream.uniform(-wm.initial_wage_noise,
                                               wm.initial_wage_noise)))
            quantum = M(wm.wage_quantum_M)
            tpl.wage_cents = max(M(wm.min_annual_M), (wage // quantum) * quantum)
            years = 2 + stream.randint(0, 3)
            price = stream.uniform(float(tier["price_lo_m"]),
                                   float(tier["price_hi_m"]))
            world.draft_templates[tid] = tpl
            world.draft_meta[tid] = {
                "contract_years": years,
                "price_cents": (M(price) // M(0.5)) * M(0.5),  # 0.5M steps
                "tier": name,
            }
    world.draft_open = True


# ---------------------------------------------------------------------------
# validation + submission
# ---------------------------------------------------------------------------

def cost_of(world, tids: list[str]) -> int:
    return sum(world.draft_meta[t]["price_cents"] for t in tids)


def validate_picks(world, tids: list[str]) -> None:
    cfg = world.params.draft
    if len(set(tids)) != len(tids):
        raise ValidationError("duplicate_picks", "pick list contains duplicates")
    unknown = [t for t in tids if t not in world.draft_templates]
    if unknown:
        raise ValidationError("unknown_template",
                              f"no such templates: {', '.join(unknown[:5])}")
    if len(tids) < int(cfg.min_picks):
        raise ValidationError(
            "too_few_picks",
            f"a valid squad needs at least {cfg.min_picks} players "
            f"(you picked {len(tids)}); the cheapest tier is priced so a "
            f"legal squad always fits the budget")
    if not any(world.draft_templates[t].pos == "GK" for t in tids):
        raise ValidationError("no_goalkeeper",
                              "at least one GK is required to field a team")
    cost = cost_of(world, tids)
    budget = M(cfg.budget_m)
    if cost > budget:
        raise ValidationError(
            "over_budget",
            f"picks cost {cost / M(1):.1f}M against a budget of "
            f"{cfg.budget_m}M; drop players or choose cheaper tiers")


def submit(world, cid: str, tids: list[str]) -> dict:
    if not world.draft_open:
        raise ValidationError("draft_closed", "the draft phase is over")
    validate_picks(world, tids)
    world.draft_submissions[cid] = list(tids)
    spent = cost_of(world, tids)
    return {"picks": len(tids), "spent_m": round(spent / M(1), 1),
            "leftover_m": round((M(world.params.draft.budget_m) - spent) / M(1), 1)}


def auto_fill(world, tids: list[str]) -> list[str]:
    """Anti-deadlock repair: turn any list into a valid one — dedupe, drop
    the most expensive picks while the list is over budget OR while a legal
    squad (min_picks incl. GK) cannot be completed within budget, and extend
    from the cheapest remaining templates. The floor tier guarantees a
    fixpoint (cheapest min_picks cost far under budget)."""
    cfg = world.params.draft
    budget = M(cfg.budget_m)
    min_picks = int(cfg.min_picks)
    picks = [t for i, t in enumerate(tids)
             if t in world.draft_templates and t not in tids[:i]]

    def valid(ps) -> bool:
        return (len(ps) >= min_picks
                and any(world.draft_templates[t].pos == "GK" for t in ps)
                and cost_of(world, ps) <= budget)

    for _ in range(len(world.draft_templates) + 1):
        while picks and cost_of(world, picks) > budget:
            picks.remove(max(picks,
                             key=lambda t: (world.draft_meta[t]["price_cents"], t)))
        remaining = sorted((t for t in world.draft_templates if t not in picks),
                           key=lambda t: (world.draft_meta[t]["price_cents"], t))
        for t in remaining:
            need_gk = not any(world.draft_templates[x].pos == "GK"
                              for x in picks)
            if len(picks) >= min_picks and not need_gk:
                break
            if need_gk and world.draft_templates[t].pos != "GK" and \
                    len(picks) >= min_picks - 1:
                continue
            if cost_of(world, picks) + world.draft_meta[t]["price_cents"] <= budget:
                picks.append(t)
        if valid(picks):
            return picks
        # full-but-short: drop the priciest pick to make room and retry
        if picks:
            picks.remove(max(picks,
                             key=lambda t: (world.draft_meta[t]["price_cents"], t)))
        else:  # nothing left: pure floor build
            picks = []
    return picks


# ---------------------------------------------------------------------------
# scripted bot policy (belief-based only — I3)
# ---------------------------------------------------------------------------

# target counts for a balanced 18-man draft
_BOT_NEEDS = [("GK", 2), ("CB", 3), ("FB", 2), ("DM", 1), ("CM", 3),
              ("W", 2), ("AM", 1), ("ST", 3)]


def bot_picks(world, cid: str) -> list[str]:
    """Deterministic balanced drafter: fill positional needs by believed
    value-per-cost, then spend leftover on best believed upgrades. The
    5/5/5 viewer-tier scout noise differentiates easy/hard boards for free
    (perceived_ca already applies the multiplier)."""
    cfg = world.params.draft
    budget = M(cfg.budget_m)
    reserve = M(cfg.get("bot_reserve_m", 10))
    picks: list[str] = []

    def spend() -> int:
        return cost_of(world, picks)

    def affordable(t) -> bool:
        return spend() + world.draft_meta[t]["price_cents"] <= budget - reserve

    def score(t) -> float:
        tpl = world.draft_templates[t]
        ca = scouting.perceived_ca(world, cid, tpl)
        price_m = world.draft_meta[t]["price_cents"] / M(1)
        return ca / (price_m + 15.0)

    for pos, count in _BOT_NEEDS:
        cands = sorted((t for t in sorted(world.draft_templates)
                        if world.draft_templates[t].pos == pos
                        and t not in picks),
                       key=lambda t: (-score(t), t))
        for t in cands:
            if sum(1 for x in picks
                   if world.draft_templates[x].pos == pos) >= count:
                break
            if affordable(t):
                picks.append(t)
    # leftover: best believed ability that still fits (depth for fatigue)
    extras = sorted((t for t in sorted(world.draft_templates) if t not in picks),
                    key=lambda t: (-scouting.perceived_ca(
                        world, cid, world.draft_templates[t]), t))
    for t in extras:
        if len(picks) >= int(cfg.get("bot_max_picks", 22)):
            break
        if affordable(t):
            picks.append(t)
    if len(picks) < int(cfg.min_picks) or \
            not any(world.draft_templates[t].pos == "GK" for t in picks):
        picks = auto_fill(world, picks)
    return picks


# ---------------------------------------------------------------------------
# resolution: copies, cash, expectations, VA baseline
# ---------------------------------------------------------------------------

def resolve(world) -> None:
    """Instantiate every club's picks as fresh players and close the draft.

    Deterministic: submissions for controlled clubs, bot_picks for the rest,
    in sorted club order. Copies get new pids and per-club names; the club's
    scout re-assesses its own copies on arrival (fresh, tighter own-band —
    the draft-time card band was a 'C' public read)."""
    params = world.params
    cfg = params.draft
    budget = M(cfg.budget_m)
    stipend = M(cfg.stipend_m)
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        tids = world.draft_submissions.get(cid)
        if tids is None:
            tids = bot_picks(world, cid)
            world.draft_submissions[cid] = tids
        nstream = world.rng.stream("draft", f"names:{cid}", 0)
        for tid in tids:
            tpl = world.draft_templates[tid]
            meta = world.draft_meta[tid]
            p = Player(**{**tpl.to_dict(),
                          "pid": world.ids.next("P"),
                          "name": names.player_name(nstream, tpl.nationality),
                          "club_id": cid,
                          "contract_end_year": 1 + meta["contract_years"],
                          "signed_year": 1})
            world.players[p.pid] = p
        club.cash = budget - cost_of(world, tids) + stipend
    world.draft_open = False
    world.draft_templates = {}
    world.log_event("draft_resolved", {
        "picks": {c: len(world.draft_submissions[c])
                  for c in sorted(world.draft_submissions)}})

    # post-draft world finalization (mirrors the tail of generate_world)
    from engine.domain.worldgen import set_expectations
    set_expectations(world)
    world.baseline_avg_ca = world.avg_league_ca()
    from engine.domain.finance import financial_snapshot
    from engine.sim import economy
    for division in world.division_ids():
        for club in world.division_clubs(division):
            pos = club.expectation_rank
            annual = (economy.tv_annual(world, club, pos)
                      + economy.commercial_annual(world, club)
                      + world.params.economy.home_games
                      * economy.matchday_income(world, club))
            annual = max(annual, int(world.annual_wage_bill(club.cid) / 0.75))
            club.revenue_last_season = annual
    # VA baseline = the club's OWN post-draft day-0 net worth (stored state;
    # injections add to it later — score-neutral bailouts, DESIGN change 3)
    for cid in world.sorted_club_ids():
        snap = financial_snapshot(world, world.clubs[cid])
        world.va_baseline_state[cid] = snap["net_worth"]
