"""Permanent exploit-probe regression (calibration v0.3, leak investigation):

The "slim squad" playbook — strip the roster to 15, bank wages, iron-man the
best XI, never buy — beat the oracle on the 3y track under v0.2 constants
(dead fatigue + roster-shrink sandbag + free free-agents). With squad depth
activated and board expectations floored it must lose to disciplined play.

Runs real 3y sims (the track where the exploit thrived); a couple of minutes,
deliberately kept in the default suite — this is the anti-regression anchor
for three engine mechanics at once.
"""

from __future__ import annotations

import statistics

from run_benchmark import run_benchmark

SEEDS = [1, 2]
YEARS = 3


def _mean(agent: str) -> float:
    out = run_benchmark(agent=agent, seeds=SEEDS, years=YEARS, club="mid",
                        verbose=False, run_label=f"probe_slim_{agent}")
    return statistics.mean(s["s_final"] for s in out["per_seed"])


def test_slim_squad_probe_does_not_beat_heuristic():
    slim = _mean("slim_squad")
    heuristic = _mean("heuristic")
    assert slim < heuristic, (
        f"slim-squad exploit scores {slim:.2f} >= heuristic {heuristic:.2f}: "
        "squad-depth / expectation-floor calibration has regressed")
