"""Belief layer: the ONLY way any viewer (AI club or observation layer) sees ability.

I1 (persistent scout bias): perceived = true + persistent_bias(viewer, player)
+ season_noise(viewer, player, season). Repeated queries within a season return
the same value; across seasons only the season noise resamples, so estimates
converge to truth+bias, never to truth.

This module is truth-domain (it generates the noise); AI decision code in
market_ai.py may only call the functions here, never read true attributes.
"""

from __future__ import annotations

from engine.domain.transfer import value_from_ability


def _belief_cache(world) -> dict:
    """Per-world memo for belief-noise unit normals.

    Streams here are derived fresh per call and never shared, so the unit-z
    each (domain, viewer, pid[, year]) key produces is a pure function of the
    world seed. Caching z (NOT sigma*z: sigma flips when a player changes
    club) skips the blake2s key derivation + Box-Muller on the hot path while
    staying bit-identical. Not part of to_dict(), so snapshots/replay are
    unaffected.
    """
    c = getattr(world, "_belief_cache", None)
    if c is None:
        c = {"bias": {}, "season": {}, "stars": {}}
        world._belief_cache = c
    return c


def _persistent_bias(world, viewer_cid: str, pid: str, sigma: float) -> float:
    cache = _belief_cache(world)["bias"]
    key = (viewer_cid, pid)
    z = cache.get(key)
    if z is None:
        s = world.rng.stream("belief_bias", f"{viewer_cid}:{pid}", 0)
        z = s.gauss(0.0, 1.0)
        cache[key] = z
    return 0.0 + sigma * z


def _season_noise(world, viewer_cid: str, pid: str, sigma: float) -> float:
    cache = _belief_cache(world)["season"]
    key = (viewer_cid, pid)
    hit = cache.get(key)
    if hit is None or hit[0] != world.date.year:
        s = world.rng.stream("belief_season", f"{viewer_cid}:{pid}", world.date.year)
        hit = (world.date.year, s.gauss(0.0, 1.0))
        cache[key] = hit
    return 0.0 + sigma * hit[1]


def perceived_ca(world, viewer_cid: str, player) -> int:
    """Believed current ability (fixed point). Own club sees tighter."""
    sc = world.params.scouting
    own = player.club_id == viewer_cid
    bias_sigma = sc.own_bias_sigma if own else sc.bias_sigma
    noise_sigma = sc.own_session_sigma if own else sc.session_sigma
    # 5/5/5 opponent competence tiers: a tiered VIEWER scouts sharper (hard)
    # or noisier (easy). Scales the cached unit-z — no extra RNG draw, so the
    # flag-off path is untouched (dict is empty by default; player club and
    # controlled clubs are never in it).
    tier = world._opponent_tier_cfg.get(viewer_cid)
    if tier is not None:
        m = tier["scout_sigma_mult"]
        bias_sigma *= m
        noise_sigma *= m
    v = (player.ca()
         + _persistent_bias(world, viewer_cid, player.pid, bias_sigma)
         + _season_noise(world, viewer_cid, player.pid, noise_sigma))
    return max(10, min(2000, int(round(v / 25.0) * 25)))  # coarse 2.5-CA grid


def perceived_potential_stars(world, viewer_cid: str, player) -> int:
    """Youth potential as 1-5 stars only (DESIGN §8)."""
    sc = world.params.scouting
    cache = _belief_cache(world)["stars"]
    key = (viewer_cid, player.pid)
    z = cache.get(key)
    if z is None:
        s = world.rng.stream("belief_stars", f"{viewer_cid}:{player.pid}", 0)
        z = s.gauss(0.0, 1.0)
        cache[key] = z
    raw = (player.pa / 10.0 - 40.0) / 32.0 + (0.0 + sc.youth_star_sigma * z)
    return max(1, min(5, int(round(raw))))


def perceived_value(world, viewer_cid: str, player) -> int:
    """Believed market value in cents, including per-seed age mispricing (X1)."""
    sd = world.params.world.season_settlement_day
    months = player.contract_months_left(world.date.year, world.date.day, sd)
    age = player.age(world.date.year)
    v = value_from_ability(world.params, perceived_ca(world, viewer_cid, player),
                           age, world.date.year, months, player.form)
    v = int(v * (1.0 + world.market.mispricing_for_age(age)))
    return v


def ability_band(world, viewer_cid: str, player) -> dict:
    """Coarse range + confidence letter for observation output (I2).

    Band is centered on the BELIEF, not the truth; width by relationship.
    """
    sc = world.params.scouting
    center = perceived_ca(world, viewer_cid, player)
    if player.club_id == viewer_cid:
        width, conf = sc.band_width_own, "A"
    elif player.club_id is not None and world.clubs[player.club_id].division == \
            world.clubs[viewer_cid].division:
        width, conf = sc.band_width_scouted, "B"
    else:
        width, conf = sc.band_width_public, "C"
    half = width // 2
    lo = max(1, center - half)
    hi = min(2000, center + half)
    return {"ca_high": round(hi / 10), "ca_low": round(lo / 10), "confidence": conf}
