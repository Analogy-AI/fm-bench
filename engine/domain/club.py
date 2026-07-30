"""Club entity: finances, facilities, board, AI profile, season record."""

from __future__ import annotations

NEGOTIATION_STYLES = ["soft", "firm", "hard"]


class Club:
    __slots__ = (
        "cid", "name", "division", "reputation", "rep_ema",
        "stadium_capacity", "cash",
        "academy_level", "training_level", "facility_projects",
        "board_confidence", "board_patience", "months_in_vote_zone", "expectation_rank", "season_target",
        "negotiation_style", "aggression",
        "formation", "style", "style_switch_round", "prev_style", "lineup",
        "belief_bias", "sales_total", "flips", "low_wage_seasons", "prev_wage_ratio",
        "strength_floor",
        "parachute_years_left", "parachute_base_cents",
        "admin_negative_days", "in_administration", "points_penalty",
        "rev_ytd", "cost_ytd", "revenue_last_season", "wage_boost_pct",
        "tw", "td", "tl", "tgf", "tga",
        "net_history",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        self.facility_projects = kw.get("facility_projects", [])
        self.belief_bias = kw.get("belief_bias", {})
        self.sales_total = kw.get("sales_total", 0)
        self.flips = kw.get("flips", 0)
        self.low_wage_seasons = kw.get("low_wage_seasons", 0)
        # last monthly wage/revenue ratio; -1.0 = no reading yet (board trend relief)
        self.prev_wage_ratio = kw.get("prev_wage_ratio", -1.0)
        # season-start squad-strength floor for board expectations (v0.3 anti-
        # roster-shrink sandbag): selling the squad down mid-season does not
        # lower the bar the board judges you against; decays across seasons
        # so deliberate rebuilds stay viable
        self.strength_floor = kw.get("strength_floor", 0.0)
        self.months_in_vote_zone = kw.get("months_in_vote_zone", 0)
        self.parachute_years_left = kw.get("parachute_years_left", 0)
        self.parachute_base_cents = kw.get("parachute_base_cents", 0)
        self.admin_negative_days = kw.get("admin_negative_days", 0)
        self.in_administration = kw.get("in_administration", False)
        self.points_penalty = kw.get("points_penalty", 0)
        self.rev_ytd = kw.get("rev_ytd", 0)
        self.cost_ytd = kw.get("cost_ytd", 0)
        self.revenue_last_season = kw.get("revenue_last_season", 0)
        self.wage_boost_pct = kw.get("wage_boost_pct", 0.0)
        self.style_switch_round = kw.get("style_switch_round", -99)
        self.prev_style = kw.get("prev_style")
        self.lineup = kw.get("lineup")
        # last 3 month-end (rev_ytd - cost_ytd) readings, for the qualitative
        # burn trend in finances_view (round-3 rec 4)
        self.net_history = kw.get("net_history", [])
        for k in ("tw", "td", "tl", "tgf", "tga"):
            if getattr(self, k) is None:
                setattr(self, k, 0)

    # -- season record -------------------------------------------------------

    def reset_season_record(self) -> None:
        self.tw = self.td = self.tl = self.tgf = self.tga = 0
        self.points_penalty = 0

    def points(self) -> int:
        return self.tw * 3 + self.td - self.points_penalty

    def record_result(self, gf: int, ga: int) -> None:
        self.tgf += gf
        self.tga += ga
        if gf > ga:
            self.tw += 1
        elif gf == ga:
            self.td += 1
        else:
            self.tl += 1

    def flip_rate(self) -> float:
        return self.flips / self.sales_total if self.sales_total else 0.0

    def to_dict(self) -> dict:
        d = {}
        for k in self.__slots__:
            v = getattr(self, k)
            if k == "net_history":
                # observability-only (feeds the qualitative burn-trend string,
                # never a sim decision) — excluded from state/hash like
                # world.match_reports; replay recomputes it from the daily sim
                continue
            if isinstance(v, float):
                v = round(v, 6)
            elif isinstance(v, dict):
                v = dict(sorted(v.items()))
            d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Club":
        return cls(**d)
