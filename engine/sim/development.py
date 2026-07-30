"""Weekly training growth, monthly aging, weekly morale drift."""

from __future__ import annotations

from engine.domain.player import CLUSTER_W


def weekly_training(world) -> None:
    """P4 on Mondays inside the training season. DESIGN §5.2 formula."""
    dev = world.params.development
    d = world.date.day
    if not (dev.season_train_start <= d <= dev.season_train_end):
        return
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id is None:
            continue
        club = world.clubs[p.club_id]
        gap = p.pa - p.ca()
        if gap <= 0:
            continue
        recent = sum(p.minutes_l4)
        minutes_ratio = min(1.0, recent / dev.full_minutes_per_28d)
        f = dev.low_minutes_factor + (1.0 - dev.low_minutes_factor) * minutes_ratio
        if p.is_youth:
            f = max(f, 0.5)  # academy game time abstracted
        tq = dev.training_quality_per_level[club.training_level - 1]
        age = p.age(world.date.year)
        age_mult = 0.5
        for cap in sorted(int(k) for k in dev.age_mult):
            if age <= cap:
                age_mult = dev.age_mult[cap] if cap in dev.age_mult \
                    else dev.age_mult[str(cap)]
                break
        delta = (dev.k_dev_weekly * age_mult * (gap / 1000.0)
                 * f * tq * p.professionalism)
        _apply_growth(p, delta)


def _apply_growth(p, delta: float) -> None:
    """Distribute CA growth across clusters, hard-capped by PA."""
    gap = p.pa - p.ca()
    delta = min(delta, float(gap))
    if delta <= 0:
        return
    p.true_phys = min(2000, p.true_phys + delta * 0.9)
    p.true_tech = min(2000, p.true_tech + delta * 1.1)
    p.true_ment = min(2000, p.true_ment + delta * 1.0)
    _quantize(p)


def monthly_aging(world) -> None:
    """P3 on day-of-month 15: post-peak decline, mental growth until 30."""
    ag = world.params.aging
    year = world.date.year
    for pid in sorted(world.players):
        p = world.players[pid]
        peak, annual_pct, cliff, cliff_pct = ag.groups[p.pos]
        age_eff = p.age(year) + p.decay_headstart - p.aging_offset
        if age_eff > peak:
            rate = annual_pct + (cliff_pct if age_eff >= cliff else 0.0)
            p.true_phys = max(10, p.true_phys * (1.0 - rate / 100.0 / 12.0))
            p.true_tech = max(10, p.true_tech * (1.0 - ag.tech_decline_pct / 100.0 / 12.0))
        if p.age(year) < ag.mental_growth_until:
            p.true_ment = min(2000, p.true_ment * (1.0 + ag.mental_growth_pct / 100.0 / 12.0))
        _quantize(p)


def _quantize(p) -> None:
    p.true_phys = int(round(p.true_phys))
    p.true_tech = int(round(p.true_tech))
    p.true_ment = int(round(p.true_ment))


def weekly_morale_drift(world) -> None:
    m = world.params.morale
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.morale > m.drift_target:
            p.morale = max(m.drift_target, p.morale - m.drift_per_week)
        elif p.morale < m.drift_target:
            p.morale = min(m.drift_target, p.morale + m.drift_per_week)
