"""Player entity. True attributes are engine-internal and must never cross obs/."""

from __future__ import annotations

POSITIONS = ["GK", "CB", "DM", "FB", "CM", "W", "AM", "ST"]

# attack / defense weight of each position for match strength aggregation
POS_ATK_WEIGHT = {"GK": 0.02, "CB": 0.15, "DM": 0.35, "FB": 0.45, "CM": 0.7,
                  "W": 1.0, "AM": 1.0, "ST": 1.2}
POS_DEF_WEIGHT = {"GK": 1.2, "CB": 1.2, "DM": 1.0, "FB": 0.9, "CM": 0.7,
                  "W": 0.35, "AM": 0.3, "ST": 0.15}
# scorer / assist propensity for event sampling
POS_GOAL_WEIGHT = {"GK": 0.02, "CB": 0.35, "DM": 0.5, "FB": 0.5, "CM": 1.0,
                   "W": 2.2, "AM": 2.5, "ST": 4.0}
POS_ASSIST_WEIGHT = {"GK": 0.05, "CB": 0.4, "DM": 0.8, "FB": 1.2, "CM": 2.0,
                     "W": 3.0, "AM": 3.5, "ST": 1.5}
# cluster weights (phys, tech, ment) used for the generic CA aggregate
CLUSTER_W = (0.34, 0.36, 0.30)


class Player:
    __slots__ = (
        "pid", "name", "nationality", "pos", "birth_year",
        "true_phys", "true_tech", "true_ment", "pa",
        "injury_proneness", "professionalism", "consistency", "aging_offset",
        "style_pref", "rating_bias",
        "club_id", "is_youth", "listed",
        "wage_cents", "contract_end_year", "signed_year",
        "fatigue", "morale", "form", "injury_days", "injury_tier",
        "season_minutes", "minutes_l4", "season_apps", "season_goals",
        "season_assists", "career_apps", "career_goals", "career_assists",
        "decay_headstart", "free_agent_since", "renewal_cooldown_until",
        "renewal_flagged", "bought_abs_day", "bought_fee_cents",
        "last_offer_years",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        # defaults for mutable state
        self.fatigue = kw.get("fatigue", 0.0)
        self.morale = kw.get("morale", 60.0)
        self.form = kw.get("form", 6.5)
        self.injury_days = kw.get("injury_days", 0)
        self.injury_tier = kw.get("injury_tier", -1)
        self.season_minutes = kw.get("season_minutes", 0)
        self.minutes_l4 = kw.get("minutes_l4", [])
        self.season_apps = kw.get("season_apps", 0)
        self.season_goals = kw.get("season_goals", 0)
        self.season_assists = kw.get("season_assists", 0)
        self.career_apps = kw.get("career_apps", 0)
        self.career_goals = kw.get("career_goals", 0)
        self.career_assists = kw.get("career_assists", 0)
        self.decay_headstart = kw.get("decay_headstart", 0.0)
        self.is_youth = kw.get("is_youth", False)
        self.listed = kw.get("listed", False)
        self.free_agent_since = kw.get("free_agent_since")
        self.renewal_cooldown_until = kw.get("renewal_cooldown_until", 0)
        self.renewal_flagged = kw.get("renewal_flagged", False)
        self.bought_abs_day = kw.get("bought_abs_day", 0)
        self.bought_fee_cents = kw.get("bought_fee_cents", 0)
        self.last_offer_years = kw.get("last_offer_years", 0)

    # -- truth-domain helpers (never exposed through obs/) ------------------

    def ca(self) -> int:
        """Generic current ability, fixed point 1..2000."""
        w = CLUSTER_W
        return int(round(w[0] * self.true_phys + w[1] * self.true_tech
                         + w[2] * self.true_ment))

    def age(self, year: int) -> int:
        return year - self.birth_year

    def contract_months_left(self, year: int, day: int, settlement_day: int) -> int:
        """Whole game-months until contract expiry (expires at settlement of end_year)."""
        days_left = (self.contract_end_year - year) * 360 + (settlement_day - day)
        return max(0, days_left // 30)

    def available(self) -> bool:
        return self.injury_days <= 0

    def to_dict(self) -> dict:
        d = {}
        for k in self.__slots__:
            v = getattr(self, k)
            if k == "last_offer_years" and not v:
                continue  # skip-at-default: keeps pre-round-3 state hashes
            if isinstance(v, float):
                v = round(v, 6)
            d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        return cls(**d)
