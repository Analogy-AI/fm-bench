"""Daily tick: fixed phase order P1-P9 (DESIGN §2.2), season transitions."""

from __future__ import annotations

from engine.core.params import M
from engine.domain import academy as academy_mod
from engine.domain.league import generate_fixtures, league_table
from engine.sim import board as board_mod
from engine.sim import development, economy, injury
from engine.sim import market_ai
from engine.sim import market_strategy
from engine.sim import match as match_mod
from engine.sim import scouting


def tick(world) -> None:
    """Advance one day and run all phases. Stop detection is done by caller."""
    new_year = world.date.advance()          # P1 clock
    if new_year:
        start_new_season(world)
    d = world.date.day
    injury.daily_recovery(world)             # P2 recovery
    _timers_and_expiry(world)                # P3 expiry events
    if world.date.is_monday():               # P4 weekly training
        development.weekly_training(world)
        development.weekly_morale_drift(world)
    if world.date.day_of_month == 15:
        development.monthly_aging(world)
    rnd = world.round_for_day(d)             # P5 matches
    if rnd is not None:
        _play_round(world, rnd)
    market_strategy.run_daily(world)         # P6 AI club decisions (pluggable)
    market_ai.resolve_sealed(world)          # P6.5 v0.3 sealed window close (no-op when off)
    market_ai.clear_market(world)            # P7 market clearing
    economy.daily(world)                     # P8 finance settle
    if world.date.day_of_month == 30:
        _for_each_seat(world, board_mod.monthly)
    _contract_milestones(world)
    if d == world.params.world.season_settlement_day:
        season_settlement(world)
    if d == world.params.world.youth_intake_day:
        youth_intake(world)
    # P9 stop determination: engine.sim.stops.due_stop(world), called by Game


def _for_each_seat(world, fn, *args) -> None:
    """Run a player-club-scoped sim function once per controlled club.

    Benchmark Mode: exactly the old direct call. League Mode: board.py (and
    friends) keep reading world.player_club_id — the acting() swap points it
    at each live seat in canonical (sorted cid) order, so board logic stays
    single-club and untouched.
    """
    if world.league is None:
        fn(world, *args)
        return
    for cid in world.league.live_cids():
        with world.league.acting(world, cid):
            fn(world, *args)


def _play_round(world, rnd: int) -> None:
    reports = []
    played: set[str] = set()
    for division in world.division_ids():
        for home_cid, away_cid in world.fixtures[division][rnd]:
            report = match_mod.play_match(world, home_cid, away_cid, rnd)
            economy.credit_gate(world, home_cid)
            reports.append(report)
            played.update(report["players"].keys())
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id is None or p.is_youth:
            continue
        p.minutes_l4 = (p.minutes_l4 + [90 if pid in played else 0])[-4:]
    _for_each_seat(world, board_mod.after_round, reports)
    # major injury to a controlled club's starter is a first-class decision event
    if world.has_controlled():
        thresh = world.params.injury.major_injury_stop_days
        for r in reports:
            for item in r["injuries"]:
                p = world.players[item["player"]]
                if world.is_controlled(p.club_id) and item["days"] >= thresh:
                    world.push_inbox_for(p.club_id, "major_injury", item,
                                         action_required=False)
                    world.queue_event_stop_for(p.club_id, "MAJOR_INJURY")


def _timers_and_expiry(world) -> None:
    d = world.date.day
    w = world.params.windows
    if d in (w.summer_open, w.winter_open):
        world.market.clear_window_bumps()
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        remaining = []
        for proj in club.facility_projects:
            if proj["complete_abs_day"] <= world.date.abs_day:
                if proj["kind"] == "academy":
                    club.academy_level = min(3, club.academy_level + 1)
                elif proj["kind"] == "training":
                    club.training_level = min(3, club.training_level + 1)
                elif proj["kind"] == "stadium":
                    club.stadium_capacity += world.params.stadium.expand_step
                world.log_event("facility_complete", {"club": cid,
                                                      "kind": proj["kind"]})
                if world.is_controlled(cid):
                    world.push_inbox_for(cid, "facility_complete",
                                         {"kind": proj["kind"]})
            else:
                remaining.append(proj)
        club.facility_projects = remaining


def _contract_milestones(world) -> None:
    """Flag controlled-club contracts entering their final 12 months (once)."""
    if world.league is None:
        cids = [world.player_club_id] if world.player_club_id else []
    else:
        cids = world.league.live_cids()
    sd = world.params.world.season_settlement_day
    for cid in cids:
        for p in world.seniors_of(cid):
            months = p.contract_months_left(world.date.year, world.date.day, sd)
            if months < 12 and not p.renewal_flagged:
                p.renewal_flagged = True
                world.push_inbox_for(cid, "contract_expiring",
                                     {"months_left": months, "player": p.pid},
                                     action_required=False)
                world.queue_event_stop_for(cid, "CONTRACT_EXPIRY")


def season_settlement(world) -> None:
    """Day 300: honors, prizes, board eval, promotion/relegation, contracts,
    retirements, snapshots."""
    from engine.domain.finance import financial_snapshot

    params = world.params
    sc = params.score.honors
    year = world.date.year
    tables = {division: league_table(world.division_clubs(division))
              for division in world.division_ids()}

    economy.season_prizes(world)
    _for_each_seat(world, board_mod.season_eval)

    cpd = params.world.clubs_per_division
    multi_div = len(world.division_ids()) >= 2
    honors_now = []
    for pos, club in enumerate(tables[1], start=1):
        if pos == 1:
            honors_now.append((club.cid, "T1_CHAMPION"))
        elif pos <= 4:
            honors_now.append((club.cid, "T1_TOP4"))
        if multi_div and pos > cpd - params.world.relegated:
            honors_now.append((club.cid, "RELEGATED_T1"))
    if multi_div:
        for pos, club in enumerate(tables[2], start=1):
            if pos == 1:
                honors_now.append((club.cid, "T2_CHAMPION"))
            elif pos <= params.world.promoted:
                honors_now.append((club.cid, "T2_PROMOTED"))
    for cid, code in honors_now:
        world.honors.append({"cid": cid, "code": code,
                             "points": sc.get(code, 0), "year": year})
        world.log_event("honor", {"cid": cid, "code": code, "year": year})

    # promotion / relegation (division swap effective now; fixtures at new year)
    for cid in world.sorted_club_ids():
        if world.clubs[cid].parachute_years_left > 0:
            world.clubs[cid].parachute_years_left -= 1
    if multi_div:
        for pos, club in enumerate(tables[1], start=1):
            if pos > cpd - params.world.relegated:
                # parachute base = PREVIOUS tier's TV money (DESIGN §7.4), so it
                # must be computed before the division is reassigned
                club.parachute_base_cents = economy.tv_annual(world, club, pos)
                club.division = 2
                club.parachute_years_left = 2
        for pos, club in enumerate(tables[2], start=1):
            if pos <= params.world.promoted:
                club.division = 1

    # contracts expiring this year -> free agency; academy terms lapse
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id is None:
            continue
        if p.contract_end_year <= year:
            if p.is_youth:
                del world.players[pid]
                continue
            if world.is_controlled(p.club_id):
                world.push_inbox_for(p.club_id, "player_left_free", {"player": pid})
            world.log_event("contract_expired", {"club": p.club_id, "player": pid})
            p.club_id = None
            p.free_agent_since = year
            p.listed = False

    # retirements and free-agent purge
    ag = params.aging
    for pid in sorted(world.players):
        p = world.players[pid]
        age = p.age(year)
        retire = (age >= ag.hard_retire_age
                  or (age >= ag.retire_age and p.ca() < ag.retire_ca_below
                      and not p.is_youth))
        purge = (p.club_id is None and p.free_agent_since is not None
                 and year - p.free_agent_since >= params.world.free_agent_purge_seasons)
        if retire or purge:
            if world.is_controlled(p.club_id):
                world.push_inbox_for(p.club_id, "player_retired", {"player": pid})
            world.log_event("retired" if retire else "purged", {"player": pid})
            del world.players[pid]

    # financial snapshots (all clubs; scoring reads the player club's)
    for cid in world.sorted_club_ids():
        world.fin_snapshots[cid].append(financial_snapshot(world, world.clubs[cid]))

    economy.update_reputation(world)

    _ai_facility_investment(world, year)

    world.log_event("season_settled", {
        "t1_champion": tables[1][0].cid,
        "t2_champion": tables[2][0].cid if multi_div else None,
        "year": year})

    if year >= world.years and (world.player_club_id is not None
                                or world.league is not None):
        world.settle("completed")


def youth_intake(world) -> None:
    year = world.date.year
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        stream = world.rng.stream("youth_intake", cid, year)
        # anti-hollowing floor (opt-in): worse league rank -> larger reverse
        # factor -> better youth (see academy.reverse_pa_mu). Pure read; with
        # reverse_pa_mu=0 (default) the intake stays byte-identical.
        table = league_table(world.division_clubs(club.division))
        pos = next((i for i, c in enumerate(table, 1) if c.cid == cid), 1)
        reverse_factor = (pos - 1) / (len(table) - 1) if len(table) > 1 else 0.0
        intake = academy_mod.generate_intake(world.params, stream, club, year,
                                             world.ids,
                                             reverse_factor=reverse_factor)
        for p in intake:
            world.players[p.pid] = p
        if world.is_controlled(cid):
            world.push_inbox_for(cid, "youth_intake", {
                "prospects": [p.pid for p in intake]}, action_required=False)
        else:
            _ai_youth_management(world, club)


def _ai_facility_investment(world, year: int) -> None:
    """AI clubs build facilities slowly over 20 years (belief-free: only
    cash/level/chance — scanned by the I3 truth-isolation test)."""
    params = world.params
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        if world.is_controlled(cid) or club.facility_projects:
            continue
        s = world.rng.stream("ai_invest", cid, year)
        if club.cash > M(40) and club.academy_level < 3 and s.chance(0.15):
            cost = M(params.academy.cost_upgrade_M[club.academy_level + 1])
            club.cash -= cost
            club.facility_projects.append({
                "complete_abs_day": world.date.abs_day + params.academy.upgrade_days,
                "cost": cost, "kind": "academy"})


def _ai_youth_management(world, club) -> None:
    """AI promotes 3+ star 18-19yo youths, releases stalled 20yo ones."""
    year = world.date.year
    for p in world.youths_of(club.cid):
        age = p.age(year)
        stars = scouting.perceived_potential_stars(world, club.cid, p)
        if age >= 18 and stars >= 3 and \
                len(world.seniors_of(club.cid)) < world.params.world.max_squad_size:
            from engine.domain.contract import market_wage_annual
            p.is_youth = False
            p.wage_cents = market_wage_annual(world.params,
                                              scouting.perceived_ca(world, club.cid, p),
                                              age, year) // 2
            p.contract_end_year = year + world.params.academy.youth_contract_years
        elif age >= 20:
            del world.players[p.pid]


def start_new_season(world) -> None:
    """Day 1 of a new year: rollover, fixtures, expectations, resets."""
    from engine.domain.worldgen import set_expectations

    # adaptive expectations (opt-in): capture last season's final positions
    # BEFORE reset_season_record wipes the tables. Transient like
    # world._va_baselines — never serialized, so snapshots/replay unaffected;
    # not even created when the feature is off (byte-identical default).
    if world.params.board.get("adaptive_target_weight", 1.0) < 1.0:
        world._last_final_pos = {
            c.cid: i
            for division in world.division_ids()
            for i, c in enumerate(
                league_table(world.division_clubs(division)), start=1)
        }
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        club.revenue_last_season = club.rev_ytd
        club.rev_ytd = 0
        club.cost_ytd = 0
        club.net_history = []  # ytd counters reset; stale slopes would lie
        club.reset_season_record()
        club.in_administration = False
        if club.division == 1:
            club.parachute_years_left = 0
    for division in world.division_ids():
        fs = world.rng.stream("fixtures", division, world.date.year)
        world.fixtures[division] = generate_fixtures(
            [c.cid for c in world.division_clubs(division)], fs)
    set_expectations(world)
    world.market.season_decay(world.params.market_ai.markup_season_decay)
    world.board_monthly_used = 0.0
    world.season_stops = 0
    if world.league is not None:
        world.league.reset_season_counters()
    for pid in sorted(world.players):
        p = world.players[pid]
        p.season_minutes = 0
        p.season_apps = p.season_goals = p.season_assists = 0
        p.minutes_l4 = []
        p.renewal_flagged = False
    # prune old match reports to bound memory (event log keeps everything)
    cutoff_year = world.date.year - 1
    for mid in sorted(world.match_reports):
        if world.match_reports[mid]["year"] < cutoff_year:
            del world.match_reports[mid]
    world.log_event("season_start", {"year": world.date.year})
