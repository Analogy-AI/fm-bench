"""Round-3: insolvency countdown warnings (autopsy rec 1).

The administration fuse (admin_days below the cash threshold) used to burn
silently; controlled clubs now get qualitative warnings at the configured
countdown marks, via the shared inbox path, logged in the event log.
"""

from __future__ import annotations

import re

from engine.core.params import M
from engine.game import Game


def _drive_to_settlement(game, max_stops=200):
    """Advance collecting inbox items; return (items, warn_days, result)."""
    items, warn_days = [], []
    packet = None
    for _ in range(max_stops):
        packet = game.advance()
        for it in packet.get("inbox", []):
            items.append(it)
            if it["type"] == "insolvency_warning":
                warn_days.append((it["year"], it["day"], it["payload"]["detail"]))
        if packet.get("game_over"):
            return items, warn_days, packet["result"]
    return items, warn_days, None


def _force_insolvent(seed=5):
    game = Game(seed=seed, years=2, player_club="mid")
    game.start()
    club = game.world.clubs[game.world.player_club_id]
    club.cash = -M(500)  # far below any revenue-scaled threshold
    return game


def test_warnings_fire_at_countdown_marks_then_administration():
    game = _force_insolvent()
    world = game.world
    warn_marks = tuple(world.params.economy.admin_warn_days)
    items, warn_days, result = _drive_to_settlement(game)
    stages = [d[2] for d in warn_days]
    assert stages[:2] == ["first", "final"], f"stages: {stages}"
    # warnings land in the event log too
    etypes = [e["type"] for e in world.event_log.entries]
    assert etypes.count("insolvency_warning") >= 2
    # the fuse still ends in administration (warnings inform, don't save you)
    admin_items = [it for it in items if it["type"] == "administration"]
    assert admin_items, "administration never fired after sustained insolvency"
    # countdown spacing matches the configured marks
    assert len(warn_days) >= 2
    first, final = warn_days[0], warn_days[1]
    gap = (final[0] - first[0]) * 360 + (final[1] - first[1])
    assert gap == warn_marks[1] - warn_marks[0], f"gap {gap}"


def test_warning_text_is_digit_free():
    game = _force_insolvent()
    items, warn_days, _ = _drive_to_settlement(game)
    warn_items = [it for it in items if it["type"] == "insolvency_warning"]
    assert warn_items
    for it in warn_items:
        assert not re.search(r"\d", it["payload"]["text"])


def test_forced_scenario_is_deterministic():
    runs = []
    for _ in range(2):
        game = _force_insolvent()
        items, warn_days, result = _drive_to_settlement(game)
        runs.append((warn_days,
                     result["final_state_hash"] if result else None))
    assert runs[0] == runs[1]


def test_headless_worlds_never_warn():
    """No controlled club => no inbox, no insolvency_warning events."""
    game = Game(seed=11, years=2, player_club=None)
    res = game.simulate_headless()
    assert res["final_hash"]
    etypes = [e["type"] for e in game.world.event_log.entries]
    assert "insolvency_warning" not in etypes
