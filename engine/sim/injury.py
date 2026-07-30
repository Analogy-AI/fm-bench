"""Injury rolls (match-time) and daily recovery."""

from __future__ import annotations


def roll_match_injury(world, player, stream) -> int:
    """Returns injury duration in days (0 = no injury). Truth-domain."""
    inj = world.params.injury
    fat = world.params.fatigue
    club = world.clubs[player.club_id]
    tf = world.params.training_facility.injury_mult_per_level[club.training_level - 1]
    rate = inj.base_match_rate * player.injury_proneness * tf
    if player.age(world.date.year) >= 30:
        rate *= inj.age30_mult
    if player.fatigue > fat.thresh1:
        rate *= inj.fatigue70_mult
    if not stream.chance(rate):
        return 0
    x = stream.uniform()
    acc = 0.0
    for i, (share, lo, hi) in enumerate(inj.tiers):
        acc += share
        if x < acc or i == len(inj.tiers) - 1:
            days = stream.randint(lo, hi)
            player.injury_tier = i
            if i == len(inj.tiers) - 1:  # season-ender: permanent physical loss
                lo_l, hi_l = inj.season_ender_perm_phys_loss
                loss = stream.uniform(lo_l, hi_l)
                player.true_phys = max(10, int(player.true_phys * (1.0 - loss)))
                player.decay_headstart += world.params.aging.major_injury_headstart_years
            elif i >= 2:
                player.decay_headstart += world.params.aging.major_injury_headstart_years
            return days
    return 0


def daily_recovery(world) -> None:
    """P2: injury countdown + fatigue recovery for every player."""
    fat = world.params.fatigue
    year = world.date.year
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.injury_days > 0:
            p.injury_days -= 1
            if p.injury_days == 0:
                p.injury_tier = -1
                p.fatigue = min(p.fatigue, 40.0)
        rec = fat.recovery_base - fat.recovery_age_penalty * max(0, p.age(year) - 28)
        p.fatigue = max(0.0, p.fatigue - rec)
