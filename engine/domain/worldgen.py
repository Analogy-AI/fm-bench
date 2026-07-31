"""Procedural world generation: seed -> full fictional world (DESIGN §3)."""

from __future__ import annotations

from engine.core.params import M, Params
from engine.domain import names
from engine.domain.club import NEGOTIATION_STYLES, Club
from engine.domain.contract import market_wage_annual
from engine.domain.league import generate_fixtures
from engine.domain.player import POSITIONS, Player
from engine.domain.world import World

# senior squad position template (sums to 25)
SQUAD_TEMPLATE = [("GK", 3), ("CB", 4), ("FB", 3), ("DM", 2), ("CM", 4),
                  ("W", 3), ("AM", 2), ("ST", 4)]

STRATA_T1_RANK = {"big": 1, "mid": 7, "small": 13}


def _gen_player(world: World, stream, club: Club, pos: str, quality: int) -> Player:
    params = world.params
    pg = params.playergen
    nationality = stream.choice(names.NATIONALITIES)
    age = stream.weighted_choice(
        list(range(pg.age_min, pg.age_max + 1)),
        [1, 2, 3, 4, 5, 6, 6, 6, 6, 6, 5, 4, 3, 2, 1, 1, 1, 1][: pg.age_max - pg.age_min + 1],
    )
    ca = int(stream.truncgauss(quality, pg.squad_ca_sigma, 300, 1900))
    # young players sit below their eventual level
    if age <= 20:
        ca = int(ca * stream.uniform(0.70, 0.85))
    pa = int(min(pg.pa_max, max(ca, ca + stream.truncgauss(
        pg.pa_extra_mu * max(0.0, (27 - age) / 10.0), pg.pa_sigma, 0, 900))))
    spread_t = stream.uniform(0.9, 1.1)
    spread_p = stream.uniform(0.9, 1.1)
    p = Player(
        pid=world.ids.next("P"),
        name=names.player_name(stream, nationality),
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
        rating_bias=round(stream.gauss(0.0, params.match.rating_persistent_bias_sigma), 4),
        club_id=club.cid,
        wage_cents=0,
        contract_end_year=1 + stream.randint(0, 3),
        signed_year=1,
    )
    # Initial wage from the CLUB'S BELIEF, never true CA (C1: an exact
    # wage=f(CA) would hand the agent a day-1 truth oracle via the wage column),
    # plus a persistent per-player noise factor, quantized to coarse steps.
    from engine.sim import scouting
    wm = params.wagemodel
    believed = scouting.perceived_ca(world, club.cid, p)
    noise = 1.0 + stream.uniform(-wm.initial_wage_noise, wm.initial_wage_noise)
    wage = int(market_wage_annual(params, believed, age, 1) * noise)
    quantum = M(wm.wage_quantum_M)
    p.wage_cents = max(M(wm.min_annual_M), (wage // quantum) * quantum)
    return p


def generate_world(seed: int, params: Params, player_club: str | None = "mid",
                   years: int = 20) -> World:
    from engine.sim import draft as draft_mod
    world = World(seed, params, None, years)
    w = params.world
    drafted = draft_mod.enabled(params)
    gen = world.rng.stream("worldgen", "clubs", 0)

    # -- clubs ---------------------------------------------------------------
    for division in world.division_ids():
        pg = params.playergen
        top = pg.t1_quality_top if division == 1 else pg.t2_quality_top
        bottom = pg.t1_quality_bottom if division == 1 else pg.t2_quality_bottom
        for i in range(w.clubs_per_division):
            frac = i / (w.clubs_per_division - 1) if w.clubs_per_division > 1 else 0.0
            quality = int(top - (top - bottom) * frac)
            if division == 1:
                cash = M(60 - 48 * frac)          # 5:1 head:tail wealth
                reputation = 88 - 40 * frac
                capacity = int(55000 - 33000 * frac)
            else:
                cash = M(12 - 9 * frac)
                reputation = 45 - 25 * frac
                capacity = int(22000 - 12000 * frac)
            if drafted:
                # v0.3 equal endowment: identical club shells — the wealth,
                # brand, facility and board-patience gradients are the seat
                # confounds the draft world exists to remove (DESIGN v0.3
                # change 1). Cash is set at draft resolution.
                d = params.draft
                cash = 0
                reputation = float(d.get("reputation", 70))
                capacity = int(d.get("stadium_capacity", 38000))
            club = Club(
                cid=world.ids.next("C"),
                name=names.club_name(gen),
                division=division,
                reputation=round(reputation, 2),
                rep_ema=round(reputation, 2),
                stadium_capacity=capacity,
                cash=cash,
                academy_level=1 if drafted else min(3, 1 + (1 if gen.chance(0.35) else 0) + (1 if division == 1 and gen.chance(0.2) else 0)),
                training_level=1 if drafted else 1 + (1 if division == 1 and gen.chance(0.5) else 0),
                board_confidence=params.board.start_confidence,
                board_patience=(params.board.patience_min + params.board.patience_max) // 2 if drafted
                    else gen.randint(params.board.patience_min, params.board.patience_max),
                negotiation_style=gen.choice(NEGOTIATION_STYLES),
                aggression=round(gen.uniform(0.3, 1.0), 3),
                formation=gen.choice(params.tactics.formations),
                style=gen.choice(params.tactics.styles),
            )
            club.reset_season_record()
            world.clubs[club.cid] = club
            world.fin_snapshots[club.cid] = []
            if drafted:
                continue  # squads come from the draft; no per-club generation
            # -- squad -------------------------------------------------------
            pstream = world.rng.stream("worldgen", f"players:{club.cid}", 0)
            for pos, count in SQUAD_TEMPLATE:
                for _ in range(count):
                    p = _gen_player(world, pstream, club, pos, quality)
                    world.players[p.pid] = p

    # -- per-seed systematic mispricing (X1: no fixed exploitable alpha) -----
    mstream = world.rng.stream("worldgen", "mispricing", 0)
    mx = params.market_ai.mispricing_abs_max
    for bucket in ("young", "prime", "old"):
        world.market.age_mispricing[bucket] = round(mstream.uniform(-mx, mx), 4)

    # -- fixtures year 1 ------------------------------------------------------
    for division in world.division_ids():
        fs = world.rng.stream("fixtures", division, 1)
        world.fixtures[division] = generate_fixtures(
            [c.cid for c in world.division_clubs(division)], fs)

    # -- draft pool (v0.3; replaces per-club squads) ---------------------------
    if drafted:
        draft_mod.generate_pool(world)

    # -- expectations (draft worlds: deferred to draft resolution) ------------
    if not drafted:
        set_expectations(world)

    # -- player club selection -------------------------------------------------
    if player_club is not None:
        t1 = world.division_clubs(1)
        ranked = sorted(t1, key=lambda c: (-c.cash, c.cid))
        if player_club not in STRATA_T1_RANK:
            # Silently falling back to "mid" made a typo indistinguishable
            # from a real strata choice: the run would score a DIFFERENT club
            # than asked for and still replay bit-identically, so nothing
            # downstream could catch it. External harnesses (Open Track, MCP)
            # construct Game directly and bypass argparse's `choices`.
            raise ValueError(
                f"unknown player_club {player_club!r}; expected one of "
                f"{sorted(STRATA_T1_RANK)} or None for an all-AI world")
        rank = STRATA_T1_RANK[player_club]
        world.player_club_id = ranked[rank].cid

    # -- hard_offline mode: advantage the AI opponents (non-player clubs) -----
    # applied AFTER player selection so only the 15 rivals are boosted; default
    # mult 1.0 skips the loop entirely -> byte-identical to easy mode.
    opp_mult = getattr(w, "opponent_cash_mult", 1.0)
    if opp_mult != 1.0:
        for club in sorted(world.clubs.values(), key=lambda c: c.cid):
            if club.cid != world.player_club_id:
                club.cash = int(club.cash * opp_mult)

    # -- 5/5/5 opponent competence tiers (opt-in, default OFF) ---------------
    _assign_opponent_tiers(world, params)

    world.baseline_avg_ca = world.avg_league_ca()

    # -- seed prior-season revenue (kills Y1 zero-revenue false alarms) -------
    # (draft worlds: deferred to draft resolution, when squads/wages exist)
    if not drafted:
        from engine.sim import economy
        for division in world.division_ids():
            for club in world.division_clubs(division):
                pos = club.expectation_rank
                annual = (economy.tv_annual(world, club, pos)
                          + economy.commercial_annual(world, club)
                          + world.params.economy.home_games
                          * economy.matchday_income(world, club))
                # prior consistency: the squad this club starts with was
                # affordable last season (no day-1 wage-warning false alarms)
                annual = max(annual, int(world.annual_wage_bill(club.cid) / 0.75))
                club.revenue_last_season = annual
    return world


def _assign_opponent_tiers(world: World, params: Params) -> None:
    """5/5/5 opponent competence tiers (params.opponents; opt-in, default OFF).

    tier_mix "none" (default): pure no-op — no state touched, no RNG draw
    consumed, so default worlds stay byte-identical (golden-hash guarded).

    tier_mix "555": the NON-player clubs, ranked by initial endowment (cash
    desc, cid tie-break), receive params.opponents.tier_order cyclically.
    The cyclic hard/medium/easy pattern crosses competence with wealth —
    every wealth quintile contains all three tiers — so money and brains are
    decorrelated. The assignment is a pure deterministic function of params
    plus worldgen output (NO RNG): same seed + same params = same exam for
    every tested model (benchmark-consistency principle).

    The player's own club is NEVER tiered. With no player club (headless
    calibration, league mode) the flag is inert: the feature belongs to the
    capability track (1 LLM vs 15 scripted opponents).
    """
    opp = params.get("opponents") or {}
    mix = str(opp.get("tier_mix", "none"))
    if mix == "none" or world.player_club_id is None:
        return
    if mix != "555":
        raise ValueError(f"unknown opponents.tier_mix {mix!r}; use none|555")
    order = list(opp["tier_order"])
    tiers_cfg = opp["tiers"]
    rivals = sorted((c for c in world.clubs.values()
                     if c.cid != world.player_club_id),
                    key=lambda c: (-c.cash, c.cid))
    for i, club in enumerate(rivals):
        tier = str(order[i % len(order)])
        world.opponent_tiers[club.cid] = tier
        world._opponent_tier_cfg[club.cid] = dict(tiers_cfg[tier])


def set_expectations(world: World) -> None:
    """Preseason board expectation = full-squad strength rank within division.

    v0.3 (anti-roster-shrink sandbag, X5 completion): the ranking basis is
    floored at the decayed prior floor, then the floor is re-anchored — selling
    the squad down lowers next season's target only partially, and fully only
    after the decay has run its course (1-2 seasons). Legit growth raises the
    floor immediately.
    """
    b = world.params.board
    eff: dict[str, float] = {}
    for cid in sorted(world.clubs):
        club = world.clubs[cid]
        cur = world.squad_strength(cid)
        prev_floor = club.strength_floor or 0.0
        decayed = cur + (prev_floor - cur) * b.strength_floor_decay \
            if prev_floor > cur else cur
        eff[cid] = decayed
        club.strength_floor = decayed
    for division in world.division_ids():
        clubs = world.division_clubs(division)
        ranked = sorted(clubs, key=lambda c: (-eff[c.cid], c.cid))
        cushion = b.get("target_cushion", 0)
        # adaptive expectations (opt-in, weight<1): blend last season's ACTUAL
        # finish into the target — an unmeetable target self-corrects instead of
        # collapsing confidence (1x16 calibration). X5 guard: the adapted target
        # can never sit more than adaptive_target_max_relax places easier than
        # the squad-strength rank, so tanking cannot buy an easy season.
        w = b.get("adaptive_target_weight", 1.0)
        last_pos = getattr(world, "_last_final_pos", {}) if w < 1.0 else {}
        max_relax = b.get("adaptive_target_max_relax", 3)
        n = len(ranked)
        for rank, club in enumerate(ranked, start=1):
            club.expectation_rank = rank
            target = rank
            lp = last_pos.get(club.cid)
            if lp is not None:
                blend = int(round(w * rank + (1.0 - w) * lp))
                target = min(blend, rank + max_relax)
            # cushion: the board expects "finish within `cushion` of your squad
            # strength rank", so single-division match variance doesn't collapse
            # confidence and fire even well-played clubs (1x16 calibration).
            club.season_target = min(n, target + cushion)
