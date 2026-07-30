"""Match engine: strength -> segmented Poisson goals -> sampled events -> ratings.

X2 ruling: per-player ratings are derived ONLY from sampled discrete events
(goals, assists, clean sheets, result) plus a persistent per-player observer
bias — never from underlying strength plus noise.
"""

from __future__ import annotations

import math

from engine.domain.player import (POS_ASSIST_WEIGHT, POS_ATK_WEIGHT,
                                  POS_DEF_WEIGHT, POS_GOAL_WEIGHT)
from engine.sim import injury as injury_mod
from engine.sim.lineup import FORMATION_SLOTS, auto_lineup, fitness_mult, pos_fit

__all__ = ["FORMATION_SLOTS", "auto_lineup", "play_match",
           "prematch_win_probs"]


def _fitness_mult(world, p) -> float:
    return fitness_mult(world.params, p)


def _form_mult(world, p) -> float:
    m = world.params.match
    t = max(0.0, min(1.0, (p.form - 5.8) / 1.4))
    return m.form_mult_min + (m.form_mult_max - m.form_mult_min) * t


def _morale_mult(world, p) -> float:
    mo = world.params.morale
    return mo.effect_base + p.morale / mo.effect_span


def _effective(world, club, slot: str, p) -> float:
    mult = (pos_fit(world.params, p.pos, slot) * _fitness_mult(world, p)
            * _form_mult(world, p) * _morale_mult(world, p))
    if p.style_pref != club.style:
        mult *= (1.0 - world.params.match.style_misfit_penalty)
    return p.ca() * mult


def team_strengths(world, club, lineup) -> tuple[float, float]:
    atk_num = atk_den = def_num = def_den = 0.0
    for slot, p in lineup:
        eff = _effective(world, club, slot, p)
        wa, wd = POS_ATK_WEIGHT[slot], POS_DEF_WEIGHT[slot]
        atk_num += eff * wa
        atk_den += wa
        def_num += eff * wd
        def_den += wd
    atk = (atk_num / atk_den) / 1000.0 if atk_den else 0.5
    dfn = (def_num / def_den) / 1000.0 if def_den else 0.5
    return max(atk, 0.05), max(dfn, 0.05)


def _rps_bonus(world, style_a: str, style_b: str, late: bool, opp_fatigued: bool) -> float:
    t = world.params.tactics
    bonus = t.rps[style_a][style_b]
    if (style_a == "possession" and style_b == "lowblock" and late and opp_fatigued):
        bonus = t.possession_vs_lowblock_late
    return bonus


def _style_effective(world, club, rnd: int) -> float:
    """Halved tactical impact for 2 matches after a style switch."""
    if rnd - club.style_switch_round < world.params.match.style_switch_penalty_matches:
        return 0.5
    return 1.0


def match_lambdas(world, home, away, lu_h, lu_a, segment: int, rnd: int,
                  scale: tuple[float, float] = (1.0, 1.0)) -> tuple[float, float]:
    m = world.params.match
    atk_h, def_h = team_strengths(world, home, lu_h)
    atk_a, def_a = team_strengths(world, away, lu_a)
    # v0.3: board-basis strength floor (anti-roster-shrink); (1,1) in real play
    s_h, s_a = scale
    atk_h, def_h = atk_h * s_h, def_h * s_h
    atk_a, def_a = atk_a * s_a, def_a * s_a
    late = segment >= 3
    fat_a = sum(p.fatigue for _, p in lu_a) / max(1, len(lu_a)) > 60
    fat_h = sum(p.fatigue for _, p in lu_h) / max(1, len(lu_h)) > 60
    net_h = (_rps_bonus(world, home.style, away.style, late, fat_a) * _style_effective(world, home, rnd)
             - _rps_bonus(world, away.style, home.style, late, fat_h) * _style_effective(world, away, rnd))
    net_h = max(-m.matchup_cap, min(m.matchup_cap, net_h))
    lam_h = m.base_lambda_home * (atk_h / def_a) ** m.alpha * (1.0 + net_h) * m.home_adv
    lam_a = m.base_lambda_away * (atk_a / def_h) ** m.alpha * (1.0 - net_h)
    return lam_h, lam_a


def prematch_win_probs(world, home, away, lu_h, lu_a, rnd: int,
                       scale: tuple[float, float] = (1.0, 1.0)) -> tuple[float, float]:
    """Bookmaker (p_home_win, p_away_win); closed-form Poisson, no RNG."""
    lam_h, lam_a = match_lambdas(world, home, away, lu_h, lu_a, 0, rnd, scale)
    max_g = 8
    ph = [math.exp(-lam_h) * lam_h ** k / math.factorial(k) for k in range(max_g + 1)]
    pa = [math.exp(-lam_a) * lam_a ** k / math.factorial(k) for k in range(max_g + 1)]
    home_win = sum(ph[i] * pa[j] for i in range(max_g + 1)
                   for j in range(max_g + 1) if i > j)
    away_win = sum(ph[i] * pa[j] for i in range(max_g + 1)
                   for j in range(max_g + 1) if i < j)
    return home_win, away_win


def _board_strength_scale(world, club) -> float:
    """v0.3: lambda scale lifting a shrunk squad back to its season-start
    strength floor on the BOARD's basis only (anti-roster-shrink sandbag)."""
    floor = getattr(club, "strength_floor", 0.0) or 0.0
    if floor <= 0.0:
        return 1.0
    cur = world.squad_strength(club.cid)
    if cur <= 0.0:
        return world.params.board.strength_floor_scale_cap
    return min(world.params.board.strength_floor_scale_cap, max(1.0, floor / cur))


def _board_win_probs(world, home, away, rnd: int) -> tuple[float, float]:
    """Board-expectation basis (X5 anti-sandbag): probabilities computed from
    each side's FULL-SQUAD best XI, ignoring the player's submitted lineup,
    with strength floored at the season-start snapshot (v0.3) so selling the
    squad down does not turn losses 'expected' or wins into cheap 'upsets'."""
    bl_h = auto_lineup(world, home, honor_player_choice=False)
    bl_a = auto_lineup(world, away, honor_player_choice=False)
    scale = (_board_strength_scale(world, home), _board_strength_scale(world, away))
    return prematch_win_probs(world, home, away, bl_h, bl_a, rnd, scale)


def _forfeit(world, home, away, lu_h, lu_a, rnd: int) -> dict:
    """A side unable to field 7 players forfeits 0-3 (both short: 0-0 void)."""
    gh, ga = 0, 0
    if len(lu_h) < 7 <= len(lu_a):
        ga = 3
    elif len(lu_a) < 7 <= len(lu_h):
        gh = 3
    home.record_result(gh, ga)
    away.record_result(ga, gh)
    mid = f"M{world.date.year:02d}R{rnd:02d}{home.cid}"
    report = {"away": away.cid, "away_goals": ga, "forfeit": True, "goals": [],
              "home": home.cid, "home_goals": gh, "injuries": [], "mid": mid,
              "p_away_win_board": 0.5, "p_home_win": 0.5,
              "p_home_win_board": 0.5, "players": {}, "round": rnd,
              "year": world.date.year}
    world.match_reports[mid] = report
    world.log_event("match_forfeit", {"away": away.cid, "home": home.cid,
                                      "mid": mid})
    return report


def play_match(world, home_cid: str, away_cid: str, rnd: int) -> dict:
    m = world.params.match
    home, away = world.clubs[home_cid], world.clubs[away_cid]
    stream = world.rng.stream("match", f"{home_cid}:{away_cid}", world.date.abs_day)
    lu_h, lu_a = auto_lineup(world, home), auto_lineup(world, away)
    if len(lu_h) < 7 or len(lu_a) < 7:
        return _forfeit(world, home, away, lu_h, lu_a, rnd)
    p_home_win, _ = prematch_win_probs(world, home, away, lu_h, lu_a, rnd)
    if world.is_controlled(home_cid) or world.is_controlled(away_cid):
        p_home_board, p_away_board = _board_win_probs(world, home, away, rnd)
    else:
        p_home_board, p_away_board = p_home_win, 0.0  # unused off controlled clubs

    goals: list[dict] = []
    gh = ga = 0
    for seg in range(m.segments):
        lam_h, lam_a = match_lambdas(world, home, away, lu_h, lu_a, seg, rnd)
        for side, lam, lineup, cid in (("H", lam_h, lu_h, home_cid),
                                        ("A", lam_a, lu_a, away_cid)):
            n = stream.poisson(lam / m.segments)
            for _ in range(n):
                if gh + ga >= m.max_goals:
                    break
                scorer = stream.weighted_choice(
                    [p for _, p in lineup],
                    [POS_GOAL_WEIGHT[s] * (p.true_tech / 1000.0) for s, p in lineup])
                assister = stream.weighted_choice(
                    [p for _, p in lineup],
                    [POS_ASSIST_WEIGHT[s] * (p.true_tech / 1000.0)
                     if p.pid != scorer.pid else 0.001 for s, p in lineup])
                minute = seg * 15 + stream.randint(1, 15)
                goals.append({"assist": assister.pid, "minute": minute,
                              "scorer": scorer.pid, "side": side})
                if side == "H":
                    gh += 1
                else:
                    ga += 1

    # post-match state updates -------------------------------------------------
    report_players = {}
    injuries = []
    for club, lineup, gf, gcond in ((home, lu_h, gh, ga), (away, lu_a, ga, gh)):
        won = gf > gcond
        lost = gf < gcond
        for slot, p in lineup:
            scored = sum(1 for g in goals if g["scorer"] == p.pid)
            assisted = sum(1 for g in goals if g["assist"] == p.pid)
            rating = 6.5 + 0.9 * scored + 0.45 * assisted
            if slot in ("GK", "CB", "FB", "DM"):
                rating += 0.5 if gcond == 0 else -0.15 * gcond
            rating += 0.2 if won else (-0.2 if lost else 0.0)
            rating += p.rating_bias
            rating = max(4.0, min(10.0, rating))
            p.form = 0.7 * p.form + 0.3 * rating
            p.fatigue = min(100.0, p.fatigue + world.params.fatigue.per_match[slot])
            p.season_minutes += 90
            p.season_apps += 1
            p.career_apps += 1
            p.season_goals += scored
            p.career_goals += scored
            p.season_assists += assisted
            p.career_assists += assisted
            days = injury_mod.roll_match_injury(world, p, stream)
            if days > 0:
                p.injury_days = days
                injuries.append({"days": days, "player": p.pid, "tier": p.injury_tier})
            report_players[p.pid] = {"assists": assisted, "goals": scored,
                                     "rating": round(rating, 2)}
        mo = world.params.morale
        delta = mo.win if won else (mo.loss if lost else 0)
        if lost and club.tl >= 3:
            delta += mo.streak_loss_extra
        for _, p in lineup:
            p.morale = max(0.0, min(100.0, p.morale + delta))

    home.record_result(gh, ga)
    away.record_result(ga, gh)

    mid = f"M{world.date.year:02d}R{rnd:02d}{home_cid}"
    report = {
        "away": away_cid, "away_goals": ga, "goals": goals, "home": home_cid,
        "home_goals": gh, "injuries": injuries, "mid": mid,
        "p_away_win_board": round(p_away_board, 4),
        "p_home_win": round(p_home_win, 4),
        "p_home_win_board": round(p_home_board, 4), "players": report_players,
        "round": rnd, "year": world.date.year,
    }
    world.match_reports[mid] = report
    world.log_event("match", {"away": away_cid, "away_goals": ga,
                              "home": home_cid, "home_goals": gh,
                              "mid": mid, "round": rnd})
    return report
