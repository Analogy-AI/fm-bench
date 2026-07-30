"""Fixtures (double round-robin, circle method) and league table."""

from __future__ import annotations

from engine.core.rng import Stream


def generate_fixtures(club_ids: list[str], stream: Stream) -> list[list[tuple[str, str]]]:
    """30 rounds for 16 clubs: 15 rounds circle method + mirrored return legs."""
    ids = sorted(club_ids)
    stream.shuffle(ids)
    n = len(ids)
    half = n // 2
    rounds: list[list[tuple[str, str]]] = []
    rot = ids[1:]
    for r in range(n - 1):
        pairs = []
        line = [ids[0]] + rot
        for i in range(half):
            a, b = line[i], line[n - 1 - i]
            pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        rot = rot[-1:] + rot[:-1]
    rounds += [[(away, home) for home, away in rnd] for rnd in rounds]
    return rounds


def league_table(clubs: list) -> list:
    """Sorted standings: points desc, goal diff desc, goals desc, name asc."""
    return sorted(
        clubs,
        key=lambda c: (-c.points(), -(c.tgf - c.tga), -c.tgf, c.name, c.cid),
    )
