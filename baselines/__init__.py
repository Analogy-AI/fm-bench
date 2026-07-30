"""Scripted baseline policies (DESIGN §10.3): random_v1 / greedy_v1 / heuristic_v1.

A policy is `fn(game, packet) -> None`: inspect the stop packet, call tools via
game.call_tool, return; the outer loop advances. All deterministic given seed.
"""

from baselines.random_v1 import random_v1
from baselines.greedy_v1 import greedy_v1
from baselines.heuristic_v1 import heuristic_v1
from baselines.oracle import oracle_v1
from baselines.oracle_v2 import oracle_v2
from baselines.probes import slim_squad_v1


def idle_v1(game, packet) -> None:
    """Do nothing every stop — the floor below random."""
    from baselines.base import handle_draft
    handle_draft(game, packet, "floor")


POLICIES = {
    "greedy": greedy_v1,
    "heuristic": heuristic_v1,
    "idle": idle_v1,
    # INTENTIONALLY PRIVILEGED (reads true hidden state): information-ceiling
    # anchor and leak alarm. Report alongside, never inside, the legit ladder.
    # `oracle` (v2) exploits truth aggressively; `oracle_v0` is the original
    # conservative variant kept for leaderboard-history comparability.
    "oracle": oracle_v2,
    "oracle_v0": oracle_v1,
    "random": random_v1,
    # exploit probe (regression anchor, never on the ladder): must lose to
    # heuristic — see baselines/probes.py
    "slim_squad": slim_squad_v1,
}
