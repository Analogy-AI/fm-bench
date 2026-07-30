"""World: the single source of truth. All state lives here; sim mutates it."""

from __future__ import annotations

from engine.core.clock import GameDate
from engine.core.ids import IdAllocator
from engine.core.params import Params
from engine.core.rng import RNG
from engine.domain.club import Club
from engine.domain.player import CLUSTER_W, Player
from engine.domain.transfer import MarketState


class World:
    def __init__(self, seed: int, params: Params, player_club_id: str | None,
                 years: int):
        self.seed = seed
        self.params = params
        self.rng = RNG(seed)
        self.date = GameDate(1, 1)
        self.ids = IdAllocator()
        self.clubs: dict[str, Club] = {}
        self.players: dict[str, Player] = {}
        self.fixtures: dict[int, list[list[tuple[str, str]]]] = {}
        self.market = MarketState()
        self.honors: list[dict] = []
        self.fin_snapshots: dict[str, list[dict]] = {}
        self.match_reports: dict[str, dict] = {}
        self.notebook = ""
        self.standing_orders: list[dict] = []
        # stop machinery
        self.stop_counter = 0
        self.current_stop_id: str | None = None
        self.inbox: list[dict] = []           # accumulating, delivered at stops
        self.queued_stop_types: list[str] = []  # event stops waiting to fire
        self.last_offer_batch_day = 0
        self.recent_actions: list[dict] = []
        # run config / settlement
        self.player_club_id = player_club_id
        self.years = years
        self.settled = False
        self.settle_reason: str | None = None
        self.settle_t: float | None = None    # years elapsed at settlement
        self.baseline_avg_ca = 0.0
        self.season_stops = 0
        self.board_monthly_used = 0.0  # season cap on expectation-gap channel
        self.event_log = None  # attached by persist.eventlog
        # League Mode: None in Benchmark Mode (attached by league.state).
        # Engine code never imports league/ — it only calls the helpers below.
        self.league = None
        # ablation switches (run config, set by Game; never serialized)
        self.ablations: tuple = ()
        # 5/5/5 opponent competence tiers (run config derived at worldgen from
        # params.opponents; never serialized — replay/resume regenerate it from
        # seed + param_overrides). Empty = feature off = every hook no-ops.
        self.opponent_tiers: dict[str, str] = {}       # cid -> easy|medium|hard
        self._opponent_tier_cfg: dict[str, dict] = {}  # cid -> lever dict
        # v0.3 draft (params.draft.enabled; all empty/False when off, and the
        # serialization below skips empty containers, so legacy hashes hold)
        self.draft_open = False
        self.draft_templates: dict[str, Player] = {}   # tid -> template card
        self.draft_meta: dict[str, dict] = {}          # tid -> price/tier/term
        self.draft_submissions: dict[str, list[str]] = {}
        # v0.3 stored VA baselines (draft worlds; revival injections add here)
        self.va_baseline_state: dict[str, int] = {}
        # v0.3 revival bookkeeping (params.revival.enabled)
        self.death_count: dict[str, int] = {}          # scored seats
        self.bailout_count: dict[str, int] = {}        # every club incl. bots
        self.va_injections: dict[str, int] = {}        # cents added to VA baseline

    # -- lookups (always deterministic order) --------------------------------

    def sorted_club_ids(self) -> list[str]:
        return sorted(self.clubs)

    def players_of(self, cid: str) -> list[Player]:
        return sorted((p for p in self.players.values() if p.club_id == cid),
                      key=lambda p: p.pid)

    def seniors_of(self, cid: str) -> list[Player]:
        return [p for p in self.players_of(cid) if not p.is_youth]

    def youths_of(self, cid: str) -> list[Player]:
        return [p for p in self.players_of(cid) if p.is_youth]

    def free_agents(self) -> list[Player]:
        return sorted((p for p in self.players.values() if p.club_id is None),
                      key=lambda p: p.pid)

    def division_clubs(self, division: int) -> list[Club]:
        return sorted((c for c in self.clubs.values() if c.division == division),
                      key=lambda c: c.cid)

    def division_ids(self) -> tuple[int, ...]:
        """(1, 2) by default; honors params.world.divisions for scaled worlds."""
        return tuple(range(1, self.params.world.divisions + 1))

    # -- derived quantities ---------------------------------------------------

    def annual_wage_bill(self, cid: str) -> int:
        return sum(p.wage_cents for p in self.players_of(cid))

    def squad_strength(self, cid: str) -> float:
        """Full-squad basis (X5): mean CA of the best 16 senior players."""
        cas = sorted((p.ca() for p in self.seniors_of(cid)), reverse=True)[:16]
        return sum(cas) / len(cas) if cas else 0.0

    def avg_league_ca(self) -> float:
        seniors = [p.ca() for p in self.players.values()
                   if p.club_id is not None and not p.is_youth]
        return sum(seniors) / len(seniors) if seniors else 0.0

    def is_window_open(self, day: int | None = None) -> bool:
        w = self.params.windows
        d = self.date.day if day is None else day
        return w.summer_open <= d <= w.summer_close or w.winter_open <= d <= w.winter_close

    def window_kind(self) -> str | None:
        w = self.params.windows
        d = self.date.day
        if w.summer_open <= d <= w.summer_close:
            return "summer"
        if w.winter_open <= d <= w.winter_close:
            return "winter"
        return None

    def round_for_day(self, day: int) -> int | None:
        w = self.params.world
        if day < w.first_match_day:
            return None
        off = day - w.first_match_day
        if off % w.match_interval_days != 0:
            return None
        r = off // w.match_interval_days
        return r if r < w.rounds else None

    def inflation_idx(self, kind: str) -> float:
        rate = {"revenue": self.params.economy.inflation_revenue,
                "wage": self.params.economy.inflation_wage,
                "transfer": self.params.economy.inflation_transfer}[kind]
        return (1.0 + rate) ** (self.date.year - 1)

    def cluster_weights(self) -> tuple:
        return CLUSTER_W

    # -- controlled-club routing (League Mode aware) ---------------------------
    #
    # Benchmark Mode (self.league is None): "controlled" means exactly the
    # single player club, and every *_for helper degrades to the old
    # player-club behavior — bit-identical, guarded by the golden-hash tests.

    def is_controlled(self, cid: str | None) -> bool:
        if cid is None:
            return False
        if self.league is not None:
            return self.league.is_live(cid)
        return cid == self.player_club_id

    def has_controlled(self) -> bool:
        if self.league is not None:
            return bool(self.league.live_cids())
        return self.player_club_id is not None

    def push_inbox_for(self, cid: str, item_type: str, payload: dict,
                       action_required: bool = False) -> None:
        item = {
            "action_required": action_required,
            "day": self.date.day,
            "payload": payload,
            "type": item_type,
            "year": self.date.year,
        }
        if self.league is not None:
            if self.league.acting_cid == cid:
                self.inbox.append(item)      # live view while this seat acts
            else:
                seat = self.league.seats.get(cid)
                if seat is not None and not seat.settled:
                    seat.inbox.append(item)
            return
        if cid == self.player_club_id:
            self.inbox.append(item)

    def queue_event_stop_for(self, cid: str, stop_type: str) -> None:
        if self.league is not None:
            if self.league.acting_cid == cid:
                self.queue_event_stop(stop_type)
            else:
                seat = self.league.seats.get(cid)
                if seat is not None and not seat.settled and \
                        stop_type not in seat.queued_stop_types:
                    seat.queued_stop_types.append(stop_type)
            return
        if cid == self.player_club_id:
            self.queue_event_stop(stop_type)

    def orders_of(self, cid: str) -> list[dict]:
        if self.league is not None:
            if self.league.acting_cid == cid:
                return self.standing_orders
            seat = self.league.seats.get(cid)
            return seat.standing_orders if seat is not None else []
        return self.standing_orders if cid == self.player_club_id else []

    def settle_for(self, cid: str, reason: str) -> None:
        """Settle the run (benchmark) or one seat (league) for club `cid`."""
        if self.league is not None:
            self.league.settle_seat(self, cid, reason)
            return
        if cid == self.player_club_id:
            self.settle(reason)

    # -- events / inbox -------------------------------------------------------

    def log_event(self, etype: str, data: dict) -> None:
        if self.event_log is not None:
            self.event_log.append(etype, self.date, data)

    def push_inbox(self, item_type: str, payload: dict,
                   action_required: bool = False) -> None:
        self.inbox.append({
            "action_required": action_required,
            "day": self.date.day,
            "payload": payload,
            "type": item_type,
            "year": self.date.year,
        })

    def queue_event_stop(self, stop_type: str) -> None:
        if stop_type not in self.queued_stop_types:
            self.queued_stop_types.append(stop_type)

    def revival_enabled(self) -> bool:
        rv = self.params.get("revival") or {}
        return bool(rv.get("enabled", False))

    def grant_bailout(self, cid: str, tag: str) -> int:
        """Uniform league bailout (DESIGN v0.3 change 3): decreasing
        injection schedule per club, identical for seats and bots. For
        scored clubs the amount also accrues to va_injections so bailout
        cash can never read as value added."""
        from engine.core.params import M
        rv = self.params.revival
        sched = list(rv.get("injections_m", [30, 15, 5]))
        b = self.bailout_count.get(cid, 0) + 1
        self.bailout_count[cid] = b
        inj = M(sched[min(b, len(sched)) - 1])
        self.clubs[cid].cash += inj
        self.va_injections[cid] = self.va_injections.get(cid, 0) + inj
        self.log_event("bailout", {"club": cid, "k": b, "tag": tag})
        return inj

    def revive_seat(self, cid: str, reason: str,
                    with_injection: bool) -> None:
        """Second chance for a scored seat: death counted (0.8^k on the
        positive score part), confidence reset to the floor, board memory
        cleared; injection only when the death path didn't already pay a
        bailout (administration pays club-side, firing pays here)."""
        rv = self.params.revival
        club = self.clubs[cid]
        k = self.death_count.get(cid, 0) + 1
        self.death_count[cid] = k
        inj = self.grant_bailout(cid, reason) if with_injection else 0
        club.board_confidence = float(rv.get("confidence_floor", 35))
        club.months_in_vote_zone = 0
        club.low_wage_seasons = 0
        self.log_event("revival", {"club": cid, "k": k, "reason": reason})
        self.push_inbox_for(cid, "revival", {
            "death_count": k, "reason": reason,
            "text": "The board stops short of the exit door: you get one "
                    "more chance. Confidence is reset low, a rescue fund "
                    "has arrived, and every death discounts your final "
                    "score — recover, or the discounts compound."},
            action_required=False)
        self.queue_event_stop_for(cid, "REVIVAL")

    def settle(self, reason: str) -> None:
        # v0.3 revival: fired/administration are deaths with a second
        # chance, not settlements; only completion/abandonment still settle.
        # Capped (owner decision, 2026-07-17): max_deaths revivals — enough
        # that one unlucky early death never zeroes a seat's measurement,
        # while a hopeless seat stops burning full-horizon evaluation cost.
        # The death past the cap settles for real (rho x 0.8^k, still
        # strictly worse than dying less — no arbitrage).
        if self.revival_enabled() and reason in ("fired", "administration") \
                and self.league is None and self.player_club_id is not None \
                and not self.settled:
            cid = self.player_club_id
            cap = int(self.params.revival.get("max_deaths", 3))
            if self.death_count.get(cid, 0) < cap:
                self.revive_seat(cid, reason,
                                 with_injection=(reason == "fired"))
                return
            self.death_count[cid] = self.death_count.get(cid, 0) + 1
            self.log_event("revival_exhausted",
                           {"club": cid, "deaths": self.death_count[cid]})
        if self.league is not None:
            # Inside an acting-seat context (e.g. board fires the manager),
            # settlement applies to that seat only; the world plays on.
            acting = self.league.acting_cid
            if acting is not None:
                self.league.settle_seat(self, acting, reason)
                return
            # World-level settlement (end of run / league abort): settle every
            # remaining live seat with the same reason, then the world itself.
            for cid in list(self.league.live_cids()):
                self.league.settle_seat(self, cid, reason)
        if self.settled:
            return
        self.settled = True
        self.settle_reason = reason
        self.settle_t = (self.date.year - 1) + (self.date.day / 360.0)
        self.log_event("settlement", {"reason": reason, "t": round(self.settle_t, 4)})

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict:
        d = self._base_dict()
        # v0.3 keys appear only when their feature has state (skip-at-default
        # keeps every legacy hash byte-identical)
        if self.draft_open or self.draft_templates or self.draft_submissions:
            d["draft"] = {
                "meta": {k: self.draft_meta[k] for k in sorted(self.draft_meta)},
                "open": self.draft_open,
                "submissions": {k: v for k, v in
                                sorted(self.draft_submissions.items())},
                "templates": {k: v.to_dict() for k, v in
                              sorted(self.draft_templates.items())},
            }
        if self.va_baseline_state:
            d["va_baseline_state"] = {k: self.va_baseline_state[k]
                                      for k in sorted(self.va_baseline_state)}
        if self.death_count:
            d["death_count"] = {k: self.death_count[k]
                                for k in sorted(self.death_count)}
        if self.bailout_count:
            d["bailout_count"] = {k: self.bailout_count[k]
                                  for k in sorted(self.bailout_count)}
        if self.va_injections:
            d["va_injections"] = {k: self.va_injections[k]
                                  for k in sorted(self.va_injections)}
        if self.league is not None:
            # league key present only in League Mode: Benchmark Mode
            # serialization (and therefore state hashes) stays byte-identical
            d["league"] = self.league.to_dict()
        return d

    def _base_dict(self) -> dict:
        return {
            "baseline_avg_ca": round(self.baseline_avg_ca, 4),
            "board_monthly_used": round(self.board_monthly_used, 4),
            "clubs": {k: v.to_dict() for k, v in sorted(self.clubs.items())},
            "current_stop_id": self.current_stop_id,
            "date": self.date.to_dict(),
            "fin_snapshots": {k: v for k, v in sorted(self.fin_snapshots.items())},
            "fixtures": {str(k): v for k, v in sorted(self.fixtures.items())},
            "honors": self.honors,
            "ids": self.ids.to_dict(),
            "inbox": self.inbox,
            "last_offer_batch_day": self.last_offer_batch_day,
            "market": self.market.to_dict(),
            "notebook": self.notebook,
            "player_club_id": self.player_club_id,
            "players": {k: v.to_dict() for k, v in sorted(self.players.items())},
            "queued_stop_types": self.queued_stop_types,
            "seed": self.seed,
            "settle_reason": self.settle_reason,
            "settle_t": self.settle_t,
            "settled": self.settled,
            "standing_orders": self.standing_orders,
            "stop_counter": self.stop_counter,
            "years": self.years,
        }
