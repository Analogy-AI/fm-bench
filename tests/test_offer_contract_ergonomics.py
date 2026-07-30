"""Round-3 rec 2: `years` optional on follow-up contract offers.

The autopsy attributed ~90% of all LLM tool errors to a fail/retry loop on
`offer_contract` (omitting `years` when accepting a counter-wage). Follow-up
offers now default to the previous offer's term.
"""

from __future__ import annotations

import re

from engine.game import Game


def _renewal_target(game):
    """Pick an own senior and drive to a countered negotiation state."""
    world = game.world
    cid = world.player_club_id
    seniors = sorted(world.seniors_of(cid), key=lambda p: p.pid)
    return seniors[0]


def _offer(game, pid, wage_m, years=None):
    args = {"player_id": pid, "wage_m_per_year": wage_m}
    if years is not None:
        args["years"] = years
    return game.call_tool("offer_contract", args)


def test_followup_offer_without_years_succeeds():
    game = Game(seed=9, years=2, player_club="mid")
    game.start()
    world = game.world
    from engine.domain.contract import wage_ask
    from engine.sim import scouting
    cid = world.player_club_id
    sd = world.params.world.season_settlement_day
    p = counter = None
    for cand in sorted(world.seniors_of(cid), key=lambda x: x.pid):
        months = cand.contract_months_left(world.date.year, world.date.day, sd)
        ask = wage_ask(world.params, scouting.perceived_ca(world, cid, cand),
                       cand.age(world.date.year), world.date.year,
                       cand.form, months)
        # 0.90x ask is always >= 0.85x reservation (counter zone), and only
        # accepted if the reservation rolled its very bottom — retry then.
        env = _offer(game, cand.pid, round(ask * 0.90 / 1e8, 2), years=3)
        data = env.get("data", {})
        if env.get("ok") and not data.get("accepted") and "counter_wage_m" in data:
            p, counter = cand, data["counter_wage_m"]
            break
    else:
        raise AssertionError("no negotiation reached a counter state")
    # accept the counter WITHOUT years — must not be missing_arg
    env2 = _offer(game, p.pid, counter)
    assert env2.get("ok"), env2
    assert env2["data"].get("accepted") is True
    assert env2["data"].get("years_offered") == 3  # inherited from first offer


def test_first_offer_without_years_gives_defaulting_hint():
    game = Game(seed=9, years=2, player_club="mid")
    game.start()
    p = _renewal_target(game)
    env = _offer(game, p.pid, 5.0)  # never offered before, no years
    assert env.get("ok") is False
    err = env["error"]
    assert err["code"] == "missing_arg"
    assert "years" in err["hint"]
    assert not re.search(r"\d", err["hint"])


def test_missing_required_arg_hint_names_field_digit_free():
    game = Game(seed=9, years=2, player_club="mid")
    game.start()
    env = game.call_tool("offer_contract", {"wage_m_per_year": 5.0})
    assert env.get("ok") is False
    assert env["error"]["code"] == "missing_arg"
    assert "player_id" in env["error"]["hint"]
    assert not re.search(r"\d", env["error"]["hint"])
