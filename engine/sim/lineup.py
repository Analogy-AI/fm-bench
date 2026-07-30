"""Team selection — BELIEF-ONLY module (I3: covered by the AST isolation test).

auto_lineup is used both for AI clubs' team selection and to auto-complete the
player club's chosen XI. It must never read true attributes; all ability
judgments go through engine.sim.scouting.
"""

from __future__ import annotations

from engine.sim import scouting

FORMATION_SLOTS = {
    "4-4-2": ["GK", "FB", "FB", "CB", "CB", "W", "W", "CM", "CM", "ST", "ST"],
    "4-3-3": ["GK", "FB", "FB", "CB", "CB", "DM", "CM", "CM", "W", "W", "ST"],
    "3-5-2": ["GK", "CB", "CB", "CB", "FB", "FB", "CM", "CM", "AM", "ST", "ST"],
}


def pos_fit(params, pos: str, slot: str) -> float:
    if pos == slot:
        return 1.0
    if slot == "GK" or pos == "GK":
        return 0.30
    return params.match.out_of_position_penalty


def fitness_mult(params, p) -> float:
    """Performance multiplier from fatigue (same table the match engine uses).

    Belief-safe: fatigue is the club's own observable condition data, not a
    hidden attribute. Also used in auto-selection (calibration v0.3) so clubs
    naturally rest a tired starter once a fresh backup's effective quality
    overtakes him — rotation emerges instead of iron-man.
    """
    fat = params.fatigue
    if p.fatigue > fat.thresh2:
        return fat.thresh2_perf
    if p.fatigue > fat.thresh1:
        return fat.thresh1_perf
    return 1.0


def auto_lineup(world, club, honor_player_choice: bool = True) -> list[tuple[str, object]]:
    """Fill formation slots by believed ability.

    If the club is the player club and a lineup is set (and honor_player_choice),
    the chosen available players are seated FIRST — each into its best-fit free
    slot — and only the remaining slots are filled greedily from the rest.
    A legal 11-man available XI is therefore honored 11/11.
    """
    params = world.params
    slots = FORMATION_SLOTS[club.formation]
    available = [p for p in world.seniors_of(club.cid) if p.available()]
    if len(available) < 11:  # emergency youth call-ups
        available += [p for p in world.youths_of(club.cid) if p.available()]
    if honor_player_choice and world.is_controlled(club.cid) and club.lineup:
        chosen = [world.players[pid] for pid in club.lineup
                  if pid in world.players and world.players[pid].club_id == club.cid
                  and world.players[pid].available()]
        rest = [p for p in available if p.pid not in {c.pid for c in chosen}]
    else:
        chosen, rest = [], list(available)
    perceived = {p.pid: scouting.perceived_ca(world, club.cid, p)
                 for p in available}

    free = list(enumerate(slots))          # (slot_index, slot)
    seated: list[tuple[int, str, object]] = []
    # phase 1: seat chosen players, each into its best-fit remaining slot
    for p in chosen:
        if not free:
            break
        best_i, best_fit = 0, -1.0
        for i, (_, slot) in enumerate(free):
            f = pos_fit(params, p.pos, slot)
            if f > best_fit:
                best_i, best_fit = i, f
        idx, slot = free.pop(best_i)
        seated.append((idx, slot, p))
    # phase 2: fill remaining slots greedily from the rest by believed quality.
    # v0.3: AI (script-run) clubs weight by current fitness — their managers
    # rotate. CONTROLLED clubs get a belief-only caretaker: rotation is the
    # manager's job, and an unmanaged club iron-mans its best XI into the
    # ground (otherwise idling would inherit optimal rotation for free).
    if world.is_controlled(club.cid):
        quality = dict(perceived)
    else:
        # 5/5/5 opponent tiers: an easy-tier manager is lazy — he iron-mans
        # his (noisily) believed-best XI instead of resting tired starters.
        # Untiered/medium clubs keep the fitness-aware default unchanged.
        tier = world._opponent_tier_cfg.get(club.cid)
        if tier is not None and not tier["fitness_aware_lineup"]:
            quality = dict(perceived)
        else:
            quality = {p.pid: perceived[p.pid] * fitness_mult(params, p)
                       for p in available}
    pool = sorted(rest, key=lambda p: (-quality[p.pid], p.pid))
    used = {p.pid for _, _, p in seated}
    for idx, slot in free:
        best, best_q = None, -1.0
        for p in pool:
            if p.pid in used:
                continue
            q = quality[p.pid] * pos_fit(params, p.pos, slot)
            if q > best_q:
                best, best_q = p, q
        if best is not None:
            used.add(best.pid)
            seated.append((idx, slot, best))
    seated.sort(key=lambda t: t[0])
    return [(slot, p) for _, slot, p in seated]
