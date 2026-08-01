"""Regressions for the verified audit/integrity findings (C1-C3, M1, M3).

Each test is the minimal repro of a confirmed reviewer finding, inverted into
an invariant (internal design rulings X1/X4/X5).
"""

import math

from engine.core.params import load_params
from engine.domain.worldgen import generate_world
from engine.game import Game
from engine.persist.replay import replay
from engine.persist.snapshot import state_hash
from engine.sim import scouting
from engine.sim.lineup import auto_lineup


# -- C1: initial wages must not be a true-CA oracle --------------------------

def test_initial_wage_no_truth_oracle():
    """Inverting the wage formula must NOT beat the scouting belief channel."""
    params = load_params()
    wm = params.wagemodel
    inv_errs, belief_errs = [], []
    for seed in (1, 2, 3, 4, 5):
        world = generate_world(seed, params, "mid")
        cid = world.player_club_id
        for p in world.seniors_of(cid):
            age = p.age(1)
            age_mult = 0.7 if age <= 20 else (0.85 if age >= 31 else 1.0)
            base = p.wage_cents / (wm.w0_cents * age_mult)
            if base <= 0:
                continue
            # display CA = ln(base)/k; fixed points = display * 10
            ca_inverted_fixed = 10.0 * math.log(base) / wm.k_per_ca_pt
            inv_errs.append(abs(ca_inverted_fixed - p.ca()))
            believed = scouting.perceived_ca(world, cid, p)
            belief_errs.append(abs(believed - p.ca()))
    mean_inv = sum(inv_errs) / len(inv_errs)
    mean_belief = sum(belief_errs) / len(belief_errs)
    # wage inversion must be no sharper than the designed scouting channel
    assert mean_inv >= mean_belief, (mean_inv, mean_belief)


# -- C2: a legal chosen XI must actually be fielded ---------------------------

def test_set_lineup_honored():
    params = load_params()
    world = generate_world(7, params, "mid")
    cid = world.player_club_id
    club = world.clubs[cid]
    available = [p for p in world.seniors_of(cid) if p.available()]
    weakest = sorted(available, key=lambda p: (p.ca(), p.pid))[:11]
    club.lineup = [p.pid for p in weakest]
    fielded = {p.pid for _, p in auto_lineup(world, club)}
    assert fielded == set(club.lineup), \
        f"only {len(fielded & set(club.lineup))}/11 of the chosen XI fielded"


def test_auto_lineup_ai_unchanged_shape():
    params = load_params()
    world = generate_world(7, params, "mid")
    for cid in world.sorted_club_ids()[:4]:
        lu = auto_lineup(world, world.clubs[cid], honor_player_choice=False)
        assert len(lu) == 11


# -- X5: board probability ignores the submitted lineup ----------------------

def test_board_prob_immune_to_sandbagging():
    """Two identical worlds, one sandbagged lineup: board probs must match."""
    def run_first_match(set_weak_lineup: bool):
        game = Game(seed=11, years=1, player_club="mid")
        packet = game.start()
        if set_weak_lineup:
            squad = game.call_tool("get_squad", {})["data"]
            fit = [p for p in squad if p.get("injury_days", 0) == 0]
            weak = sorted(fit, key=lambda v: (v["ability"]["ca_low"]
                                              + v["ability"]["ca_high"], v["pid"]))[:11]
            r = game.call_tool("set_lineup", {"player_ids": [p["pid"] for p in weak]})
            assert r["ok"], r
        cid = game.world.player_club_id
        while not packet.get("game_over"):
            for mid in sorted(game.world.match_reports):
                rep = game.world.match_reports[mid]
                if cid in (rep["home"], rep["away"]):
                    return rep
            packet = game.advance()
        raise AssertionError("no match played")

    honest = run_first_match(False)
    sandbag = run_first_match(True)
    assert honest["mid"] == sandbag["mid"]
    assert honest["p_home_win_board"] == sandbag["p_home_win_board"]
    assert honest["p_away_win_board"] == sandbag["p_away_win_board"]
    # while the fielded-lineup probability is allowed to differ
    assert "p_home_win" in honest


# -- C3: replay must be identical when advance goes through call_tool --------

def test_replay_identity_via_call_tool_advance():
    game = Game(seed=11, years=1, player_club="mid")
    packet = game.start()
    stops_acted = 0
    style_flip = ["possession", "gegenpress"]
    while not packet.get("game_over"):
        if stops_acted < 4:  # state-affecting action at 4 consecutive stops
            game.call_tool("set_tactics", {"style": style_flip[stops_acted % 2]})
            game.call_tool("append_note", {"text": f"stop {stops_acted}"})
            if stops_acted == 1:
                squad = game.call_tool("get_squad", {})["data"]
                game.call_tool("list_player", {"player_id": squad[0]["pid"],
                                               "listed": True})
            stops_acted += 1
        env = game.call_tool("advance", {})
        packet = env["data"]
    original = state_hash(game.world)
    assert replay(seed=11, years=1, player_club="mid",
                  action_log=game.action_log) == original


# -- M3: abandoned runs replay to the same settlement -------------------------

def test_abandoned_run_replays_identically():
    game = Game(seed=5, years=2, player_club="mid")
    packet = game.start()
    for _ in range(6):  # a few stops with an action, then quit mid-run
        game.call_tool("set_tactics", {"style": "lowblock"})
        env = game.call_tool("advance", {})
        packet = env["data"]
        if packet.get("game_over"):
            break
    result = game.final_result()
    assert game.world.settle_reason in ("abandoned", "fired", "administration",
                                        "completed")
    original = state_hash(game.world)
    replayed = replay(seed=5, years=2, player_club="mid",
                      action_log=game.action_log)
    assert replayed == original
    if game.world.settle_reason == "abandoned":
        assert result["rho"] < 1.0


# -- M1: parachute base uses the PREVIOUS tier's TV money --------------------

def test_parachute_base_previous_tier():
    from engine.core.params import M

    # Parachute payments only exist with promotion/relegation, i.e. >= 2
    # divisions; the engine default is now 1x16, so pin a 2x16 world.
    game = Game(seed=3, years=1, player_club=None,
                param_overrides={"world": {"divisions": 2}})
    game.simulate_headless()
    world = game.world
    dropped = [c for c in world.clubs.values() if c.parachute_years_left > 0]
    assert dropped, "no relegated clubs found"
    t1_base = M(world.params.economy.tv_base_M[1])
    t2_max = M(world.params.economy.tv_base_M[2]
               + world.params.economy.tv_gradient_top_M[2])
    for c in dropped:
        assert c.parachute_base_cents >= t1_base > t2_max, \
            (c.cid, c.parachute_base_cents)
