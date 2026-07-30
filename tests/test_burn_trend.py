"""Round-3 rec 4 (finance digest): qualitative monthly net-burn trend."""

from __future__ import annotations

import re

from engine.game import Game
from engine.obs import observation as obs


class _FakeClub:
    def __init__(self, history):
        self.net_history = history


REV = 120_0000_0000  # arbitrary annual revenue; monthly ~ REV/12


def test_tiers_cover_the_spectrum():
    m = REV // 12
    assert obs.burn_trend(_FakeClub([]), REV).startswith("too early")
    assert obs.burn_trend(_FakeClub([0, m]), REV) == \
        "income currently covers spending"
    assert "slightly above" in obs.burn_trend(_FakeClub([0, -m // 8]), REV)
    assert "well above" in obs.burn_trend(_FakeClub([0, -m // 2]), REV)
    assert "unsustainable" in obs.burn_trend(_FakeClub([0, -2 * m]), REV)


def test_direction_suffixes():
    m = REV // 12
    accel = obs.burn_trend(_FakeClub([0, -m // 2, -2 * m]), REV)
    assert accel.endswith("and accelerating")
    easing = obs.burn_trend(_FakeClub([0, -2 * m, -2 * m - m // 2]), REV)
    assert easing.endswith("but easing")


def test_all_tier_texts_digit_free():
    m = REV // 12
    histories = [[], [0, m], [0, -m // 8], [0, -m // 2], [0, -2 * m],
                 [0, -m // 2, -2 * m], [0, -2 * m, -2 * m - m // 2]]
    for h in histories:
        assert not re.search(r"\d", obs.burn_trend(_FakeClub(h), REV))


def test_trend_flows_through_packet_and_finances_view():
    game = Game(seed=4, years=2, player_club="mid")
    packet = game.start()
    assert "burn_trend" in packet["digest"]
    # advance past two month-ends so a real slope exists
    while True:
        packet = game.advance()
        if packet.get("game_over"):
            raise AssertionError("run ended before a trend reading")
        if game.world.clubs[game.world.player_club_id].net_history and \
                len(game.world.clubs[game.world.player_club_id].net_history) >= 2:
            break
    env = game.call_tool("get_finances", {})
    trend = env["data"]["burn_trend"]
    assert isinstance(trend, str) and not re.search(r"\d", trend)


def test_history_resets_at_season_rollover():
    game = Game(seed=4, years=2, player_club="mid")
    game.start()
    world = game.world
    club = world.clubs[world.player_club_id]
    y1 = world.date.year
    while world.date.year == y1:
        packet = game.advance()
        if packet.get("game_over"):
            raise AssertionError("ended within year 1")
    assert len(club.net_history) <= 1  # cleared at rollover, at most one reading since
