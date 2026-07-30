"""Economy calibration regression (G1/G2): wage/revenue band, bounded
administrations, no Y1 zero-revenue false alarms. Guards the recalibrated
params.yaml constants against drift."""

import statistics

import pytest

from engine.core.params import load_params
from engine.game import Game

SEEDS = (11, 42)


@pytest.fixture(scope="module", params=SEEDS)
def world_2y(request):
    # These are two-tier economy checks (T1 vs T2 wage bands); the engine
    # default is now 1x16, so pin a 2x16 world to keep tier-2 clubs present.
    game = Game(seed=request.param, years=2, player_club=None,
                param_overrides={"world": {"divisions": 2}})
    game.simulate_headless()
    return game.world


def _wage_ratios(world, division):
    out = []
    for c in world.division_clubs(division):
        rev = max(c.revenue_last_season, 1)
        out.append(world.annual_wage_bill(c.cid) / rev)
    return out


def test_t1_wage_ratio_band(world_2y):
    med = statistics.median(_wage_ratios(world_2y, 1))
    assert 0.45 <= med <= 0.75, f"T1 median wage/revenue {med:.3f} out of band"


def test_t2_solvent_but_pressured(world_2y):
    med = statistics.median(_wage_ratios(world_2y, 2))
    assert 0.35 <= med <= 0.80, f"T2 median wage/revenue {med:.3f} out of band"


def test_administrations_bounded(world_2y):
    admins = sum(1 for e in world_2y.event_log.entries
                 if e["type"] == "administration")
    assert admins <= 2, f"administration epidemic: {admins} in 2y all-AI world"


def test_no_year1_zero_revenue_false_alarms():
    """revenue_last_season is seeded at worldgen, so the wage-ratio warning
    basis is never near-zero in year 1."""
    from engine.domain.worldgen import generate_world
    world = generate_world(11, load_params(), None, 2)
    for c in world.clubs.values():
        assert c.revenue_last_season > 0, f"{c.cid} unseeded revenue"
        ratio = world.annual_wage_bill(c.cid) / c.revenue_last_season
        assert ratio < world.params.economy.wage_ratio_warning, (
            f"{c.cid} would trip the wage warning on day 1: {ratio:.2f}")


def test_ai_clubs_would_not_trip_investment_floor(world_2y):
    """Counterfactual: normally-managed (AI) clubs sit above the fire floor
    on the board's rev_ytd*1.2 basis; the floor only catches sandbagging."""
    floor = world_2y.params.economy.wage_ratio_fire_floor
    below = 0
    for division in (1, 2):
        for c in world_2y.division_clubs(division):
            rev = max(int(c.rev_ytd * 1.2), c.revenue_last_season, 1)
            if world_2y.annual_wage_bill(c.cid) / rev < floor:
                below += 1
    # single-snapshot check: the live rule needs TWO consecutive seasons
    # below floor and only applies to the player club, so transient dips
    # (top-heavy clubs whose revenue outruns spend) are tolerated here
    assert below <= 7, f"{below}/32 AI clubs under the investment floor"


def test_board_patience_gates_fire_vote():
    """A dip into vote territory does not trigger votes until it has lasted
    board_patience consecutive months; recovery resets the clock."""
    from engine.sim import board

    game = Game(seed=3, years=2, player_club="mid")
    game.start()
    world = game.world
    club = world.clubs[world.player_club_id]
    club.board_patience = 3
    threshold = world.params.board.fire_vote_below

    club.board_confidence = threshold - 5.0
    for _ in range(2):  # two months below: within patience, never a vote
        board._fire_check(world, allow_vote=True)
        assert not world.settled
    assert club.months_in_vote_zone == 2

    club.board_confidence = threshold + 5.0  # recovery resets the clock
    board._fire_check(world, allow_vote=True)
    assert club.months_in_vote_zone == 0
    assert not world.settled
