"""Calibration round 2 (autopsy recs): recovery channels, opening briefing,
board-warning observability, budget hints.

The autopsy found the year-1 death spiral locked in ~10 game-months (design
target: 2-3 seasons) and that board warnings never reached the event log.
"""

from __future__ import annotations

import json

from engine.game import Game
from engine.sim import board


def _played_game(seed: int = 3, years: int = 5) -> Game:
    game = Game(seed=seed, years=years, player_club="mid", record=False)
    game.start()
    return game


def _force_breach(world) -> None:
    """Put the player club over the wage warning line deterministically."""
    cid = world.player_club_id
    club = world.clubs[cid]
    wage = world.annual_wage_bill(cid)
    club.revenue_last_season = int(wage / 0.95)  # ratio ~0.95 > warning 0.85
    club.rev_ytd = 0


def test_finance_penalty_full_when_not_improving():
    game = _played_game()
    world = game.world
    club = world.clubs[world.player_club_id]
    _force_breach(world)
    world.date.year = 2  # isolate from the y1 ramp
    club.prev_wage_ratio = 0.5  # last month was BETTER -> ratio worsened
    club.board_confidence = 50.0
    club.tw = 0  # bottom half: no support floor interference
    before = club.board_confidence
    board.monthly(world)
    assert before - club.board_confidence >= abs(
        world.params.board.finance_penalty) - 1e-9


def test_finance_penalty_relieved_while_improving():
    game = _played_game()
    world = game.world
    club = world.clubs[world.player_club_id]
    _force_breach(world)
    world.date.year = 2
    wage = world.annual_wage_bill(world.player_club_id)
    ratio_now = wage / max(club.revenue_last_season, club.rev_ytd, 1)
    club.prev_wage_ratio = ratio_now + 0.05  # trending down = improving
    club.board_confidence = 50.0
    club.tw = 0
    before = club.board_confidence
    board.monthly(world)
    full = abs(world.params.board.finance_penalty)
    took = before - club.board_confidence
    assert took < full, "improving trend must relieve the finance penalty"
    assert took > 0, "a breach must still cost something"


def test_year1_monthly_ramp():
    game = _played_game()
    world = game.world
    club = world.clubs[world.player_club_id]
    _force_breach(world)
    assert world.date.year == 1
    club.prev_wage_ratio = 0.5  # not improving
    club.board_confidence = 50.0
    club.tw = 0
    before = club.board_confidence
    board.monthly(world)
    full = abs(world.params.board.finance_penalty)
    took = before - club.board_confidence
    assert took < full, "year-1 ramp must scale the finance penalty down"


def test_upper_half_confidence_floor():
    game = _played_game()
    world = game.world
    club = world.clubs[world.player_club_id]
    club.tw = 20  # top of the table
    club.board_confidence = 12.0  # above fire_below, under the floor
    club.revenue_last_season = 10 ** 12  # no finance breach
    world.date.day = world.params.world.first_match_day + 1  # in season
    board.monthly(world)
    assert not world.settled
    assert club.board_confidence >= world.params.board.upper_half_conf_floor


def test_bottom_half_gets_no_floor():
    game = _played_game()
    world = game.world
    club = world.clubs[world.player_club_id]
    club.tw = 0
    club.tl = 20  # bottom of the table
    club.board_confidence = 21.0  # above vote line; no fire randomness
    club.revenue_last_season = 10 ** 12
    world.date.day = world.params.world.first_match_day + 1
    board.monthly(world)
    assert club.board_confidence < world.params.board.upper_half_conf_floor


def test_board_warning_reaches_event_log():
    game = _played_game()
    world = game.world
    world.clubs[world.player_club_id].tw = 0
    _force_breach(world)
    board.monthly(world)
    warns = [e for e in world.event_log.entries
             if e.get("type") == "board_warning"]
    assert warns, "board warnings must be persisted in the event log"
    assert warns[-1]["data"]["trend"] in ("improving", "not_improving")


def test_opening_briefing_in_first_packet_and_replay():
    game = Game(seed=3, years=2, player_club="mid", record=True)
    packet = game.start()
    briefs = [i for i in packet["inbox"] if i["type"] == "board_briefing"]
    assert len(briefs) == 1
    assert briefs[0]["payload"]["detail"] in (
        "over_line", "near_line", "comfortable")
    text = briefs[0]["payload"]["text"]
    assert not any(ch.isdigit() for ch in text), "briefing must stay qualitative"
    # replay parity: replay() also goes through Game.start()
    while not packet.get("game_over"):
        packet = game.advance()
    from engine.persist.replay import replay
    rep_hash = replay(seed=3, years=2, player_club="mid",
                      action_log=game.action_log)
    assert rep_hash == packet["result"]["final_state_hash"]


def test_budget_hints_mention_reset():
    game = _played_game()
    world = game.world
    game.queries_this_stop = world.params.stops.query_budget
    env = game.call_tool("get_squad", {})
    assert not env["ok"] and "advance" in env["error"]["hint"]
    game.offers_this_stop = world.params.stops.offer_budget
    env = game.call_tool("make_transfer_offer",
                         {"player_id": "P00001", "amount_m": 1.0})
    assert not env["ok"] and "advance" in env["error"]["hint"]
    for env_ in (env,):
        assert not any(ch.isdigit() for ch in env_["error"]["hint"])
