"""Replay: seed + action log -> bit-identical rerun (DESIGN §2.2 CI assertion).

Log records can contain two control markers besides ordinary tool calls:
- "advance": the player ended the stop via call_tool("advance"). It is the
  stop TERMINATOR — replay advances exactly once for it and must not advance
  again for that stop (C3: double-advance skipped stops and dropped actions).
- "abandon": final_result() was called mid-run; replay settles right there
  instead of simulating to natural completion (M3).
"""

from __future__ import annotations

from engine.persist.snapshot import state_hash


def replay(seed: int, years: int, player_club: str | None,
           action_log: list[dict], params_path: str | None = None,
           param_overrides: dict | None = None) -> str:
    """Re-run a recorded game; returns the final state hash.

    param_overrides must match the original run's (solo-run manifests persist
    them); replaying an overridden world against defaults diverges.
    """
    from engine.game import Game

    game = Game(seed=seed, years=years, player_club=player_club,
                params_path=params_path, record=False,
                param_overrides=param_overrides)
    if player_club is None:
        game.simulate_headless()
        return state_hash(game.world)
    packet = game.start()
    i = 0
    while not game.world.settled:
        stop_id = packet["stop_id"]
        advanced_via_log = False
        while i < len(action_log) and action_log[i]["stop_id"] == stop_id:
            a = action_log[i]
            i += 1
            if a["tool"] == "advance":
                packet = game.advance()
                advanced_via_log = True
                break
            if a["tool"] == "abandon":
                game.final_result()
                return state_hash(game.world)
            game.call_tool(a["tool"], a["args"])
        if not advanced_via_log and not game.world.settled:
            packet = game.advance()
    return state_hash(game.world)
