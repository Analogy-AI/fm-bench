"""Board confidence, warnings, firing (player club only in v0 — AI managers
are not modeled). Expectation basis is FULL squad strength (X5 anti-sandbag)."""

from __future__ import annotations

from engine.domain.league import league_table


def player_club_position(world) -> int | None:
    cid = world.player_club_id
    if cid is None:
        return None
    club = world.clubs[cid]
    table = league_table(world.division_clubs(club.division))
    for i, c in enumerate(table, start=1):
        if c.cid == cid:
            return i
    return None


def after_round(world, reports: list[dict]) -> None:
    cid = world.player_club_id
    if cid is None or world.settled:
        return
    b = world.params.board
    club = world.clubs[cid]
    for r in reports:
        # X5 anti-sandbag: deltas keyed to the FULL-SQUAD bookmaker probability,
        # not the fielded lineup — weakening your XI does not lower expectations.
        if r["home"] == cid:
            p_win, gf, ga = r["p_home_win_board"], r["home_goals"], r["away_goals"]
        elif r["away"] == cid:
            p_win = max(0.05, r["p_away_win_board"])
            gf, ga = r["away_goals"], r["home_goals"]
        else:
            continue
        if gf > ga:
            delta = b.upset_win if p_win < b.upset_margin else b.expected_win
        elif gf < ga:
            delta = b.upset_loss if p_win > 1.0 - b.upset_margin else b.expected_loss
        else:
            delta = b.draw_as_favourite if p_win > 1.0 - b.upset_margin else \
                (b.draw_as_underdog if p_win < b.upset_margin else 0)
        club.board_confidence = max(0.0, min(100.0, club.board_confidence + delta))
    _fire_check(world)


def monthly(world) -> None:
    """Day-of-month 30: position vs target, wage warning, fire vote.

    Recovery-channel design (autopsy round 2): penalties must not lock a
    death spiral within one season. Three reliefs, all qualitative-fair:
    a year-1 ramp (rookie grace), a trend relief while the wage ratio is
    actually improving, and an upper-half position support floor — a board
    does not sack a manager it just watched climb the table.
    """
    cid = world.player_club_id
    if cid is None or world.settled:
        return
    b = world.params.board
    club = world.clubs[cid]
    # confidence mean-reversion (opt-in): monthly drift toward the neutral
    # start_confidence, so a death spiral stays RECOVERABLE (design pillar) —
    # surviving a bad season is mathematically possible again. Mirrors the
    # existing morale drift pattern. 0 = off (byte-identical default).
    drift = b.get("confidence_drift_rate", 0)
    if drift:
        club.board_confidence = max(0.0, min(100.0, club.board_confidence
            + drift * (b.start_confidence - club.board_confidence) / 50.0))
    pos = player_club_position(world)
    in_season = (world.params.world.first_match_day <= world.date.day
                 <= world.params.world.season_settlement_day)
    y1 = b.y1_monthly_factor if world.date.year == 1 else 1.0
    if pos is not None and in_season:  # post-settlement gap is a table artifact
        gap = pos - club.season_target
        delta = max(-8.0, min(4.0, float(b.monthly_per_place * gap))) * y1
        # season cumulative cap on the expectation-gap channel
        used = world.board_monthly_used
        room = b.season_gap_cap - abs(used)
        if room > 0:
            applied = max(-room, min(room, delta))
            world.board_monthly_used += abs(applied)
            club.board_confidence = max(0.0, min(100.0,
                                                 club.board_confidence + applied))
    wage_bill = world.annual_wage_bill(cid)
    revenue = max(club.revenue_last_season, club.rev_ytd, 1)
    ratio = wage_bill / revenue
    if ratio > world.params.economy.wage_ratio_warning:
        pen = b.finance_penalty * y1
        improving = (club.prev_wage_ratio >= 0.0
                     and ratio <= club.prev_wage_ratio - b.finance_improving_eps)
        if improving:  # breach still costs, but a fixing manager isn't buried
            pen *= b.finance_improving_factor
        club.board_confidence = max(0.0, club.board_confidence + pen)
        detail = {"detail": "wage_ratio_above_warning", "kind": "finance",
                  "trend": "improving" if improving else "not_improving"}
        world.push_inbox("board_warning", detail, action_required=False)
        world.log_event("board_warning", detail)
        world.queue_event_stop("BOARD_WARNING")
    club.prev_wage_ratio = ratio
    # upper-half support: monthly review will not let confidence rot below
    # the floor while the team sits in the top half (X5-safe: you cannot
    # sandbag INTO the upper half).
    if in_season and pos is not None:
        half = len(world.division_clubs(club.division)) // 2
        if pos <= half and club.board_confidence < b.upper_half_conf_floor:
            club.board_confidence = float(b.upper_half_conf_floor)
    _fire_check(world, allow_vote=True)


def opening_briefing(world) -> None:
    """Day-1 qualitative covenant from the board (no numeric constants).

    The starting wage bill can sit near the board's comfort line — an ambush
    unless it is disclosed. Same inbox path feeds agent packets and the UI.
    """
    cid = world.player_club_id
    if cid is None:
        return
    club = world.clubs[cid]
    wage_bill = world.annual_wage_bill(cid)
    revenue = max(club.revenue_last_season, club.rev_ytd, 1)
    ratio = wage_bill / revenue
    warn = world.params.economy.wage_ratio_warning
    if ratio > warn:
        stance = "over_line"
        text = ("Your wage bill already exceeds the level the board is "
                "comfortable with. Reduce it or grow revenue, quickly.")
    elif ratio > warn - 0.15:
        stance = "near_line"
        text = ("Your wage bill is close to the board's comfort line. There is "
                "little headroom for new wages until revenue grows.")
    else:
        stance = "comfortable"
        text = "Club finances are currently within the board's comfort zone."
    world.push_inbox("board_briefing", {
        "detail": stance, "kind": "finance",
        "text": text + " The board values financial discipline as much as "
                       "results; sustained overspending erodes its confidence. "
                       "Note that expectations are anchored to the squad you "
                       "inherit each season: selling the squad down will not "
                       "lower the bar you are judged against this year."},
        action_required=False)


def _fire_check(world, allow_vote: bool = False) -> None:
    cid = world.player_club_id
    if cid is None or world.settled:
        return
    b = world.params.board
    club = world.clubs[cid]
    if club.board_confidence < b.fire_below:
        world.log_event("fired", {"confidence": round(club.board_confidence, 1)})
        world.settle("fired")
        return
    if allow_vote:
        if club.board_confidence < b.fire_vote_below:
            club.months_in_vote_zone += 1
        else:
            club.months_in_vote_zone = 0
        # board_patience months of grace before votes start (a slightly
        # under-target season must be recoverable; sustained failure is not)
        if club.months_in_vote_zone >= club.board_patience:
            s = world.rng.stream("board_vote", cid,
                                 world.date.year * 12 + world.date.month)
            if s.chance(b.fire_vote_prob):
                world.log_event("fired", {"confidence": round(club.board_confidence, 1),
                                          "via": "vote"})
                world.settle("fired")


def season_eval(world) -> None:
    """At settlement: target vs final position; budget boost; wage-floor check."""
    cid = world.player_club_id
    if cid is None or world.settled:
        return
    b = world.params.board
    club = world.clubs[cid]
    pos = player_club_position(world)
    if pos is not None:
        delta = max(-15.0, min(15.0, (club.season_target - pos) * 3.0))
        club.board_confidence = max(0.0, min(100.0, club.board_confidence + delta))
    club.wage_boost_pct = b.budget_boost_pct if club.board_confidence > b.budget_boost_above else 0.0
    # Firing is an early-termination mechanic; at the natural end of the run
    # the settlement following this eval ends the game anyway, and a
    # final-day "fired" only injects rho noise into an otherwise completed
    # run (observed: heuristic fired at t=4.83 on a 5y run). Skip firing then.
    if world.date.year >= world.years:
        return
    # competitive-investment floor (E1): two consecutive seasons with the wage
    # bill under economy.wage_ratio_fire_floor of annualized revenue -> fired.
    revenue = max(int(world.clubs[cid].rev_ytd * 1.2), 1)
    ratio = world.annual_wage_bill(cid) / revenue
    # Investment floor fires for under-investment ONLY when the board is also
    # unhappy (confidence below the threshold). Root cause of the 1x16 20y
    # breakage (docs/CALIBRATION_1x16.md): revenue outgrows a disciplined wage
    # bill, so a WINNING, high-confidence club's wage ratio drifts below the
    # floor and it was fired despite success — flattening 20y discrimination.
    # The confidence gate keeps the anti-hoarding intent (hoard AND lose ->
    # low confidence -> still fired) without punishing efficient winners.
    unhappy = club.board_confidence < b.get("investment_floor_confidence", 50)
    if ratio < world.params.economy.wage_ratio_fire_floor and unhappy:
        club.low_wage_seasons += 1
    else:
        club.low_wage_seasons = 0
    if club.low_wage_seasons >= 2:
        world.log_event("fired", {"via": "investment_floor"})
        world.settle("fired")
        return
    _fire_check(world)
