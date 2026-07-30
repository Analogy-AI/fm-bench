"""Belief-noise memoization must be bit-neutral (same values as uncached)."""

from __future__ import annotations

from engine.core.params import load_params
from engine.domain.worldgen import generate_world
from engine.sim import scouting


def _fresh_world():
    return generate_world(3, load_params())


def test_cache_matches_fresh_computation():
    warm = _fresh_world()
    cold = _fresh_world()
    viewers = sorted(warm.clubs)[:4]
    pids = sorted(warm.players)[:40]

    # warm: query twice so the second pass reads the cache
    for cid in viewers:
        for pid in pids:
            scouting.perceived_ca(warm, cid, warm.players[pid])
            scouting.perceived_potential_stars(warm, cid, warm.players[pid])
    for cid in viewers:
        for pid in pids:
            assert scouting.perceived_ca(warm, cid, warm.players[pid]) == \
                scouting.perceived_ca(cold, cid, cold.players[pid])
            assert scouting.perceived_potential_stars(warm, cid, warm.players[pid]) == \
                scouting.perceived_potential_stars(cold, cid, cold.players[pid])


def test_cache_respects_club_change_sigma_flip():
    """Own/other sigma differs; a cached unit-z must rescale, not stale."""
    warm = _fresh_world()
    cold = _fresh_world()
    viewer = sorted(warm.clubs)[0]
    pid = next(p for p in sorted(warm.players)
               if warm.players[p].club_id not in (None, viewer))

    # warm the cache while the player belongs to another club
    scouting.perceived_ca(warm, viewer, warm.players[pid])

    # transfer the player to the viewer club in both worlds
    warm.players[pid].club_id = viewer
    cold.players[pid].club_id = viewer

    assert scouting.perceived_ca(warm, viewer, warm.players[pid]) == \
        scouting.perceived_ca(cold, viewer, cold.players[pid])


def test_cache_reseeds_season_noise_on_year_change():
    warm = _fresh_world()
    cold = _fresh_world()
    viewer = sorted(warm.clubs)[0]
    pid = sorted(warm.players)[0]

    scouting.perceived_ca(warm, viewer, warm.players[pid])  # cache year 1
    warm.date.year += 1
    cold.date.year += 1
    assert scouting.perceived_ca(warm, viewer, warm.players[pid]) == \
        scouting.perceived_ca(cold, viewer, cold.players[pid])
