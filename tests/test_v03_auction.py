"""v0.3 sealed-bid market battery (protocol v0.3, change 2).

All scripted, $0. Covers: legacy no-op, seat-order fairness under identical
sealed bids (the confound the mechanism exists to kill), exposure cap,
single-bidder bilateral preservation, tie-break determinism/symmetry, and
the price-non-disclosure audit (archive + inbox carry no amounts to
non-participants).
"""

from __future__ import annotations

import json

from engine.domain.transfer import Offer
from engine.game import Game
from engine.persist.snapshot import state_hash
from engine.sim import market_ai

SEALED = {"market_ai": {"sealed_bids": True}}


def _world(seed=1, years=2, sealed=True, draft=False):
    ov: dict = {}
    if sealed:
        ov["market_ai"] = {"sealed_bids": True}
    if draft:
        ov["draft"] = {"enabled": True}
    g = Game(seed=seed, years=years, player_club=None,
             param_overrides=ov or None)
    return g


def _mk_offer(world, buyer_cid, seller_cid, pid, amount):
    o = Offer(oid=world.ids.next("O"), player_id=pid, buyer_cid=buyer_cid,
              seller_cid=seller_cid, amount=amount,
              created_abs_day=world.date.abs_day,
              expires_abs_day=world.date.abs_day + 10)
    world.market.offers[o.oid] = o
    return o


def _pick_target(world, exclude):
    """A bot-owned player some other club could plausibly buy."""
    for pid in sorted(world.players):
        p = world.players[pid]
        if p.club_id and p.club_id not in exclude and not p.is_youth:
            return p
    raise AssertionError("no target")


# ---------------------------------------------------------------------------
# legacy no-op
# ---------------------------------------------------------------------------

def test_default_off_identical():
    a = Game(seed=7, years=2, player_club=None)
    b = Game(seed=7, years=2, player_club=None)
    ha = a.simulate_headless()["final_hash"]
    hb = b.simulate_headless()["final_hash"]
    assert ha == hb


# ---------------------------------------------------------------------------
# seat-order fairness: identical bids from different club slots must win
# ~50/50 across many windows (v0.2's ordering confound is dead)
# ---------------------------------------------------------------------------

def test_identical_bids_win_evenly_across_seats():
    wins: dict[str, int] = {}
    trials = 60
    for seed in range(1, trials + 1):
        g = _world(seed=seed, years=1)
        w = g.world
        clubs = w.sorted_club_ids()
        low, high = clubs[2], clubs[12]     # far-apart club ids
        target = _pick_target(w, exclude={low, high})
        ask = market_ai.seller_ask(w, target.club_id, target.pid, low)
        amt = int(ask * 1.3)                 # comfortably clears eligibility
        # bots bid from transfer budget (cash - 0.3x wage bill): fund them
        w.clubs[low].cash = max(w.clubs[low].cash, amt * 2 + 30 * 10**8)
        w.clubs[high].cash = max(w.clubs[high].cash, amt * 2 + 30 * 10**8)
        _mk_offer(w, low, target.club_id, target.pid, amt)
        _mk_offer(w, high, target.club_id, target.pid, amt)
        market_ai.resolve_sealed(w)
        buyer = target and w.players[target.pid].club_id
        assert buyer in (low, high), "one of the two identical bids must win"
        wins[buyer] = wins.get(buyer, 0) + 1
    # binomial(60, .5): P(min < 18) < 0.03 — generous band, catches any
    # systematic ordering bias while staying flake-free
    assert len(wins) == 2 and min(wins.values()) >= 18, wins


# ---------------------------------------------------------------------------
# exposure cap
# ---------------------------------------------------------------------------

def test_exposure_cap_blocks_overcommit():
    g = Game(seed=3, years=2, param_overrides=SEALED)
    g.start()
    w = g.world
    me = w.player_club_id
    w.clubs[me].cash = 20 * 10**8  # 20M
    targets = [p for pid, p in sorted(w.players.items())
               if p.club_id and p.club_id != me and not p.is_youth][:3]
    r1 = g.call_tool("make_transfer_offer",
                     {"player_id": targets[0].pid, "amount_m": 12})
    assert r1["ok"] and r1["data"]["status"] == "queued", r1
    r2 = g.call_tool("make_transfer_offer",
                     {"player_id": targets[1].pid, "amount_m": 12})
    assert not r2["ok"] and r2["error"]["code"] == "exposure_cap", r2
    r3 = g.call_tool("make_transfer_offer",
                     {"player_id": targets[2].pid, "amount_m": 6})
    assert r3["ok"], r3


# ---------------------------------------------------------------------------
# single bidder: bilateral semantics preserved (accept/counter/reject only,
# never an auction; a lone countered offer waits for its buyer)
# ---------------------------------------------------------------------------

def test_single_bidder_keeps_bilateral_flow():
    g = _world(seed=5, years=1)
    w = g.world
    clubs = w.sorted_club_ids()
    buyer = clubs[4]
    target = _pick_target(w, exclude={buyer})
    ask = market_ai.seller_ask(w, target.club_id, target.pid, buyer)
    w.clubs[buyer].cash = ask * 3
    o = _mk_offer(w, buyer, target.club_id, target.pid, int(ask * 0.7))
    market_ai.resolve_sealed(w)
    assert o.status in ("countered", "rejected", "accepted", "pending")
    if o.status == "countered":
        st = o.status
        market_ai.resolve_sealed(w)   # next day: still waiting on buyer
        assert o.status == st


# ---------------------------------------------------------------------------
# tie-break: deterministic, and never keyed to club order
# ---------------------------------------------------------------------------

def test_tiebreak_deterministic():
    def run(seed):
        g = _world(seed=seed, years=1)
        w = g.world
        clubs = w.sorted_club_ids()
        a, b = clubs[1], clubs[14]
        t = _pick_target(w, exclude={a, b})
        ask = market_ai.seller_ask(w, t.club_id, t.pid, a)
        amt = int(ask * 1.4)
        w.clubs[a].cash = w.clubs[b].cash = amt * 2 + 30 * 10**8
        _mk_offer(w, a, t.club_id, t.pid, amt)
        _mk_offer(w, b, t.club_id, t.pid, amt)
        market_ai.resolve_sealed(w)
        return w.players[t.pid].club_id
    assert run(9) == run(9)


# ---------------------------------------------------------------------------
# non-disclosure: archive hides fees from non-participants; outbid inbox
# carries no amounts
# ---------------------------------------------------------------------------

def test_prices_not_disclosed():
    from engine.obs.observation import history_view
    g = _world(seed=11, years=1)
    w = g.world
    clubs = w.sorted_club_ids()
    a, b = clubs[3], clubs[9]
    t = _pick_target(w, exclude={a, b})
    seller = t.club_id
    ask = market_ai.seller_ask(w, seller, t.pid, a)
    w.clubs[a].cash = w.clubs[b].cash = ask * 6 + 40 * 10**8
    _mk_offer(w, a, seller, t.pid, int(ask * 1.5))
    _mk_offer(w, b, seller, t.pid, int(ask * 1.2))
    market_ai.resolve_sealed(w)
    assert w.players[t.pid].club_id == a  # higher bid won at its own price
    # archive: outsider sees no fee; participants see their own
    outsider = next(c for c in clubs if c not in (a, b, seller))
    rows = history_view(w, outsider, "transfers", None)["transfers"]
    assert rows and all(r["fee_m"] is None for r in rows)
    rows_buyer = history_view(w, a, "transfers", None)["transfers"]
    assert any(r["fee_m"] is not None for r in rows_buyer)
    # loser offer marked outbid, no amount in any hypothetical payload
    loser = [o for o in w.market.offers.values()
             if o.buyer_cid == b and o.player_id == t.pid][0]
    assert loser.status == "outbid"


def test_outbid_inbox_has_no_amounts():
    g = Game(seed=13, years=2, param_overrides=SEALED)
    g.start()
    w = g.world
    me = w.player_club_id
    clubs = [c for c in w.sorted_club_ids() if c != me]
    rival = clubs[5]
    t = _pick_target(w, exclude={me, rival})
    ask = market_ai.seller_ask(w, t.club_id, t.pid, me)
    w.clubs[me].cash = ask * 4
    w.clubs[rival].cash = ask * 6 + 40 * 10**8
    r = g.call_tool("make_transfer_offer",
                    {"player_id": t.pid,
                     "amount_m": round(ask * 1.2 / 10**8, 1)})
    assert r["ok"], r
    _mk_offer(w, rival, t.club_id, t.pid, int(ask * 1.8))
    market_ai.resolve_sealed(w)
    msgs = [i for i in w.inbox if i["type"] == "offer_outbid"]
    assert msgs, [i["type"] for i in w.inbox]
    blob = json.dumps(msgs)
    assert "amount" not in blob and "fee" not in blob, blob
    assert w.players[t.pid].club_id == rival


# ---------------------------------------------------------------------------
# sealed + draft end-to-end determinism
# ---------------------------------------------------------------------------

def test_sealed_draft_headless_determinism():
    ov = {"draft": {"enabled": True}, "market_ai": {"sealed_bids": True}}
    h = [Game(seed=4, years=2, player_club=None,
              param_overrides=ov).simulate_headless()["final_hash"]
         for _ in range(2)]
    assert h[0] == h[1]
