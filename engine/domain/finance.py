"""Financial snapshot and net-worth accounting (DESIGN §10.1 NW single schema)."""

from __future__ import annotations

from engine.core.params import M, Params


def facility_book_value(params: Params, club) -> tuple[int, int]:
    """(completed_book_cents, in_construction_cents)."""
    book = 0
    ac = params.academy.cost_upgrade_M
    tc = params.training_facility.cost_upgrade_M
    for lvl in range(2, club.academy_level + 1):
        book += M(ac[lvl])
    for lvl in range(2, club.training_level + 1):
        book += M(tc[lvl])
    in_construction = sum(p["cost"] for p in club.facility_projects)
    return book, in_construction


def squad_true_value(world, club) -> int:
    """Sum of V_true over squad, engine-internal (score settlement only)."""
    from engine.domain.transfer import value_from_ability

    total = 0
    sd = world.params.world.season_settlement_day
    for p in world.players_of(club.cid):
        months = p.contract_months_left(world.date.year, world.date.day, sd)
        total += value_from_ability(world.params, p.ca(), p.age(world.date.year),
                                    world.date.year, months, p.form)
    return total


def financial_snapshot(world, club) -> dict:
    """Season-end snapshot for scoring. All cents, nominal (deflated at scoring)."""
    params = world.params
    sc = params.score
    wage_bill = world.annual_wage_bill(club.cid)
    cash = club.cash
    cash_cap = sc.cash_excess_wage_mult * wage_bill
    if cash > cash_cap:
        counted_cash = cash_cap + int((cash - cash_cap) * sc.cash_excess_discount)
    else:
        counted_cash = cash  # negative cash counts in full (E1)
    squad_v = squad_true_value(world, club)
    book, construction = facility_book_value(params, club)
    # unamortized transfer fees are a liability (DESIGN 10.1; G4): booking a
    # player's market value the day his fee is paid would let expiring-contract
    # purchases mint net worth instantly. Straight-line over contract length.
    fee_liability = 0
    for pl in world.players_of(club.cid):
        if pl.bought_fee_cents > 0:
            total_y = max(1, pl.contract_end_year - pl.signed_year)
            rem_y = min(max(0, pl.contract_end_year - world.date.year), total_y)
            fee_liability += pl.bought_fee_cents * rem_y // total_y
    net_worth = (counted_cash
                 + int(squad_v * sc.squad_liquidity_discount)
                 + book
                 + int(construction * sc.construction_discount)
                 - fee_liability)
    return {
        "cash": cash,
        "counted_cash": counted_cash,
        "facilities_book": book,
        "facilities_construction": construction,
        "fee_liability": fee_liability,
        "net_worth": net_worth,
        "squad_value_true": squad_v,
        "wage_bill": wage_bill,
        "year": world.date.year,
    }
