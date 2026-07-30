"""CI gate: sim(seed) == sim(seed), different seeds differ (DESIGN §2.2)."""

from engine.game import Game
from engine.persist.snapshot import state_hash


def _run_headless(seed: int, years: int) -> str:
    game = Game(seed=seed, years=years, player_club=None)
    return game.simulate_headless()["final_hash"]


def test_same_seed_same_hash():
    assert _run_headless(7, 2) == _run_headless(7, 2)


def test_different_seed_different_hash():
    assert _run_headless(7, 1) != _run_headless(8, 1)


def test_player_game_deterministic():
    def run():
        game = Game(seed=11, years=1, player_club="mid")
        packet = game.start()
        game.call_tool("set_tactics", {"style": "gegenpress"})
        game.call_tool("append_note", {"text": "determinism probe"})
        while not packet.get("game_over"):
            packet = game.advance()
        return state_hash(game.world), game.final_result()["s_final"]
    h1, s1 = run()
    h2, s2 = run()
    assert h1 == h2
    assert s1 == s2
