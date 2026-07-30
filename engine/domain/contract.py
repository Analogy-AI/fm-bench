"""Wage model and renewal negotiation math (pure functions)."""

from __future__ import annotations

import math

from engine.core.params import M, Params


def market_wage_annual(params: Params, ca_fixed: int, age: int, year: int) -> int:
    """Fair annual wage in cents for a player of given ability/age at given year."""
    wm = params.wagemodel
    ca_display = ca_fixed / 10.0
    base = wm.w0_cents * math.exp(wm.k_per_ca_pt * ca_display)
    if age <= 20:
        base *= 0.7
    elif age >= 31:
        base *= 0.85
    infl = (1.0 + params.economy.inflation_wage) ** (year - 1)
    return max(int(base * infl), M(wm.min_annual_M))


def wage_ask(params: Params, ca_fixed: int, age: int, year: int,
             form: float, months_left: int) -> int:
    """What the player demands in a renewal (leverage rises near expiry)."""
    wm = params.wagemodel
    base = market_wage_annual(params, ca_fixed, age, year)
    form_mult = 1.0 + wm.form_coef * max(-1.0, min(1.0, (form - 6.5) / 0.7))
    leverage = wm.leverage_last_year if months_left < 12 else 1.0
    return int(base * form_mult * leverage)
