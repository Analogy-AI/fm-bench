"""Club finances: daily settle, monthly income, matchday gate, prizes,
parachutes, administration. All money integer cents; nominal (inflated)."""

from __future__ import annotations

from engine.core.params import M
from engine.domain.finance import facility_book_value
from engine.domain.league import league_table


def tv_annual(world, club, position: int) -> int:
    e = world.params.economy
    base = M(e.tv_base_M[club.division])
    gradient = M(e.tv_gradient_top_M[club.division])
    amount = base + int(gradient * (16 - position) / 15)
    return int(amount * world.inflation_idx("revenue"))


def commercial_annual(world, club) -> int:
    """Superlinear in reputation: big brands out-earn small ones by more than
    the rep ratio, matching the wage curve's spread (G1 recalibration)."""
    e = world.params.economy
    scale = 1.0 if club.division == 1 else e.commercial_div2_scale
    rep_sq = club.rep_ema * club.rep_ema / 100.0
    return int(M(e.commercial_coef_M) * rep_sq * scale
               * world.inflation_idx("revenue"))


def _position_of(world, club) -> int:
    table = league_table(world.division_clubs(club.division))
    for i, c in enumerate(table, start=1):
        if c.cid == club.cid:
            return i
    return 16


def matchday_income(world, club) -> int:
    e = world.params.economy
    st = world.params.stadium
    games = club.tw + club.td + club.tl
    win_ratio = club.tw / games if games else 0.4
    ratio = (st.base_attendance + 0.20 * win_ratio + 0.15 * club.rep_ema / 100.0
             + (0.05 if club.division == 1 else 0.0))
    ratio = max(0.35, min(0.99, ratio))
    # effective ticket price scales with reputation (big brands charge more)
    price = e.ticket_price * (0.5 + club.rep_ema / 100.0)
    gate = int(club.stadium_capacity * ratio * price * 100)  # price in credits
    return int(gate * world.inflation_idx("revenue"))


def daily(world) -> None:
    e = world.params.economy
    dom = world.date.day_of_month
    for cid in world.sorted_club_ids():
        club = world.clubs[cid]
        # costs: wages + upkeep + revenue-proportional operations, accrued daily
        wage_bill = world.annual_wage_bill(cid)
        book, _ = facility_book_value(world.params, club)
        upkeep = int(book * e.facility_upkeep_pct)
        upkeep += M(world.params.academy.running_cost_M[club.academy_level])
        upkeep += M(e.misc_fixed_cost_M[club.division])
        upkeep += int(club.revenue_last_season * e.operating_cost_pct)
        daily_cost = (wage_bill + upkeep) // 360
        club.cash -= daily_cost
        club.cost_ytd += daily_cost
        # monthly income
        if dom == 30:
            pos = _position_of(world, club)
            income = tv_annual(world, club, pos) // 12
            income += commercial_annual(world, club) // 12
            if club.parachute_years_left > 0:
                pct = e.parachute[0] if club.parachute_years_left == 2 else e.parachute[1]
                income += int(club.parachute_base_cents * pct) // 12
            # anti-hollowing floor (opt-in): solidarity top-up scaled by how low
            # the club sits (bottom gets the most). solidarity_M=0 (default)=off.
            if e.get("solidarity_M", 0):
                _n = len(world.division_clubs(club.division))
                _rf = (pos - 1) / (_n - 1) if _n > 1 else 0.0
                income += int(M(e.solidarity_M) * _rf) // 12
            club.cash += income
            club.rev_ytd += income
            # month-end net reading for the qualitative burn trend
            # (observability only; excluded from state hash, see Club.to_dict)
            club.net_history = (club.net_history
                                + [club.rev_ytd - club.cost_ytd])[-3:]
        _administration_check(world, club)


def credit_gate(world, home_cid: str) -> None:
    club = world.clubs[home_cid]
    income = matchday_income(world, club)
    club.cash += income
    club.rev_ytd += income


_INSOLVENCY_WARN_TEXT = {
    "first": ("The club is trading insolvent. If finances do not recover, "
              "administration proceedings will follow."),
    "final": ("The club has been trading insolvent for a sustained period; "
              "administration looms. Raise cash now — sell players, cut "
              "wages — or the accountants take over."),
}


def _administration_check(world, club) -> None:
    e = world.params.economy
    if club.in_administration:
        return
    revenue = max(club.revenue_last_season, club.rev_ytd, M(5))
    if club.cash < int(e.admin_cash_threshold * revenue):
        club.admin_negative_days += 1
    else:
        club.admin_negative_days = 0
        return
    # Round-3: the fuse used to burn silently — a real manager knows the club
    # is trading insolvent. Qualitative countdown warnings, controlled clubs
    # only (scripted AI does not read its inbox; headless hashes unchanged).
    if world.is_controlled(club.cid) and \
            "no-insolvency-warnings" not in getattr(world, "ablations", ()) and \
            club.admin_negative_days in tuple(e.admin_warn_days):
        stage = "first" if club.admin_negative_days == e.admin_warn_days[0] \
            else "final"
        detail = {"detail": stage, "kind": "insolvency",
                  "text": _INSOLVENCY_WARN_TEXT[stage]}
        world.push_inbox_for(club.cid, "insolvency_warning", detail,
                             action_required=False)
        world.log_event("insolvency_warning", {"club": club.cid, "stage": stage})
        world.queue_event_stop_for(club.cid, "INSOLVENCY_WARNING")
    if club.admin_negative_days >= e.admin_days:
        club.in_administration = True
        club.points_penalty += e.admin_points_penalty
        club.admin_negative_days = 0
        # force-list the three highest earners
        seniors = sorted(world.seniors_of(club.cid),
                         key=lambda p: (-p.wage_cents, p.pid))
        for p in seniors[:3]:
            p.listed = True
        world.log_event("administration", {"club": club.cid})
        if world.revival_enabled():
            # v0.3 uniform league bailout: every club entering administration
            # (bot or seat) draws from the same decreasing injection schedule
            world.grant_bailout(club.cid, "administration")
        if world.is_controlled(club.cid):
            world.push_inbox_for(club.cid, "administration",
                                 {"points_penalty": e.admin_points_penalty},
                                 action_required=False)
            world.queue_event_stop_for(club.cid, "ADMINISTRATION")
            if world.revival_enabled():
                # deterministic death (unified with firing, decision #16);
                # settle() converts it into a revival, no settle dice
                world.settle_for(club.cid, "administration")
                return
            s = world.rng.stream("admin_settle", club.cid, world.date.abs_day)
            if s.chance(e.admin_settle_prob):
                world.settle_for(club.cid, "administration")


def season_prizes(world) -> None:
    e = world.params.economy
    for division in world.division_ids():
        table = league_table(world.division_clubs(division))
        for pos, club in enumerate(table, start=1):
            prize = int(M(e.prize_champion_M[division]) * (17 - pos) / 16
                        * world.inflation_idx("revenue"))
            club.cash += prize
            club.rev_ytd += prize


def update_reputation(world) -> None:
    for division in world.division_ids():
        table = league_table(world.division_clubs(division))
        for pos, club in enumerate(table, start=1):
            target = 90 - 4 * (pos - 1) if division == 1 else 45 - 2 * (pos - 1)
            club.reputation = float(target)
            club.rep_ema = round(0.8 * club.rep_ema + 0.2 * target, 3)
