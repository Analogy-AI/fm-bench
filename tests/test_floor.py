"""Anti-hollowing floor (opt-in): default-off is byte-identical; on has effect."""

from engine.domain.academy import generate_intake
from engine.game import Game


def _idle_hash(overrides):
    g = Game(seed=3, years=2, player_club="mid", param_overrides=overrides)
    p = g.start()
    while not p.get("game_over"):
        p = g.advance()
    return p["result"]["final_state_hash"]


def test_default_off_is_byte_identical():
    # explicit zeros must reproduce the no-override (default) world exactly
    base = _idle_hash(None)
    assert base == _idle_hash({"academy": {"reverse_pa_mu": 0},
                               "economy": {"solidarity_M": 0}})


def test_reverse_weighting_raises_youth_potential():
    g = Game(seed=5, years=1, player_club="mid",
             param_overrides={"academy": {"reverse_pa_mu": 60}})
    w = g.world
    club = w.clubs[w.player_club_id]

    def mean_pa(rf):  # counter-based RNG: same key => identical draws
        s = w.rng.stream("floortest", club.cid, 0)
        intake = generate_intake(w.params, s, club, 2, w.ids, reverse_factor=rf)
        return sum(pl.pa for pl in intake) / len(intake)

    # a bottom club (reverse_factor=1) gets higher-potential intake than a top one
    assert mean_pa(1.0) > mean_pa(0.0)


def test_solidarity_changes_the_world():
    # turning solidarity on must change the simulation (knob has a real effect)
    assert _idle_hash({"economy": {"solidarity_M": 150}}) != _idle_hash(None)
