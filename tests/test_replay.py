"""seed + action log => bit-identical rerun (DESIGN §2.2 CI assertion)."""

from engine.game import Game
from engine.persist.replay import replay
from engine.persist.snapshot import state_hash


def test_replay_identity_with_actions():
    game = Game(seed=21, years=1, player_club="mid")
    packet = game.start()
    acted = False
    while not packet.get("game_over"):
        if not acted:
            game.call_tool("set_tactics", {"formation": "4-3-3",
                                           "style": "possession"})
            game.call_tool("set_standing_order",
                           {"mult": 1.4, "order_type": "reject_offers_below"})
            game.call_tool("append_note", {"text": "plan: youth first"})
            acted = True
        packet = game.advance()
    original = state_hash(game.world)
    replayed = replay(seed=21, years=1, player_club="mid",
                      action_log=game.action_log)
    assert replayed == original


def test_replay_headless_identity():
    g1 = Game(seed=33, years=1, player_club=None)
    h1 = g1.simulate_headless()["final_hash"]
    h2 = replay(seed=33, years=1, player_club=None, action_log=[])
    assert h1 == h2
