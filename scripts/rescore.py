"""Re-score stored result files under the CURRENT score version.

Usage:
    python scripts/rescore.py results/foo.json [results/bar.json ...] [--write]

Channels (honors, last-3-season deflated net worth / squad value), settle
reason/time and seed are score-version-independent raw measurements, so any
stored run can be re-scored offline. The day-0 net-worth baseline is
regenerated from the seed — for runs produced by OLDER ENGINE revisions the
regenerated baseline is an approximation (worldgen may have changed); such
comparisons are directional, not exact. Never mutates the input file;
--write emits <name>.rescored.json alongside.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.core.params import CENTS_PER_M, load_params
from engine.core.version import stamp
from engine.domain.finance import financial_snapshot
from engine.domain.worldgen import generate_world
from score.composite import SCORE_VERSION

_baseline_cache: dict = {}


def baseline_m(seed: int, years: int, cid_strata: str) -> float:
    key = (seed, years)
    if key not in _baseline_cache:
        params = load_params()
        w0 = generate_world(seed, params, player_club=cid_strata, years=years)
        snap = financial_snapshot(w0, w0.clubs[w0.player_club_id])
        _baseline_cache[key] = snap["net_worth"] / CENTS_PER_M
    return _baseline_cache[key]


def rescore_run(run: dict, sc, club: str) -> float:
    ch = run["channels"]
    va = ch["net_worth_real_m"] - baseline_m(run["seed"], run["years"], club)
    h = sc.w_honors * math.log(1.0 + max(0, ch["honors_points"]) / sc.h_div)
    w = sc.w_networth * math.copysign(math.log(1.0 + abs(va) / sc.va_w_div), va)
    m = sc.w_squadvalue * math.log(
        1.0 + max(0.0, ch["squad_value_real_m"]) / sc.m_div)
    raw = h + w + m
    completed = run["settle_reason"] == "completed"
    rho = 1.0 if completed else sc.rho_base * (
        min(run["settle_t"], run["years"]) / run["years"]) ** sc.rho_exp
    return round(rho * max(raw, 0.0) + min(raw, 0.0), 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    sc = load_params().score

    skipped = []
    for path in args.files:
        p = pathlib.Path(path)
        # `rescore.py results/*.json` is the natural invocation, and results/
        # also holds per-run manifests, arena aggregates and derived viz data.
        # Skip anything that isn't a scored run summary instead of dying on the
        # first one (mirrors the same guard in scripts/ladder_report.py).
        try:
            summary = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped.append((p.name, "not valid JSON"))
            continue
        if not isinstance(summary, dict) or "per_seed" not in summary:
            skipped.append((p.name, "not a run summary (no per_seed)"))
            continue
        if not summary["per_seed"]:
            skipped.append((p.name, "run summary with no seeds"))
            continue
        club = summary.get("club", "mid")
        label = summary.get("model") or summary.get("agent")
        news = []
        print(f"\n== {p.name} ({label}, {summary.get('years')}y) ==")
        for run in summary["per_seed"]:
            old = run["s_final"]
            new = rescore_run(run, sc, club)
            news.append(new)
            print(f"  seed {run['seed']} rep {run.get('repeat', 0)}: "
                  f"{old:8.2f} -> {new:8.2f}   ({run['settle_reason']})")
            run["s_final"] = new
            run["score_version"] = SCORE_VERSION + "-rescored"
        old_mean = summary["mean_score"]
        summary["mean_score"] = round(sum(news) / len(news), 3)
        summary["min_score"] = round(min(news), 3)
        summary["max_score"] = round(max(news), 3)
        summary.update({f"rescored_{k}": v for k, v in stamp().items()})
        summary["rescore_note"] = ("baseline regenerated on current engine; "
                                   "directional for runs from older engines")
        print(f"  mean: {old_mean:.2f} -> {summary['mean_score']:.2f}")
        if args.write:
            out = p.with_suffix(".rescored.json")
            out.write_text(json.dumps(summary, indent=2, sort_keys=True))
            print(f"  wrote {out.name}")

    if skipped:
        # named, not silent: a file you expected to be re-scored and which was
        # quietly ignored is the failure mode this guard could introduce.
        print(f"\nskipped {len(skipped)} file(s) that are not run summaries:")
        for name, why in skipped:
            print(f"  {name}: {why}")


if __name__ == "__main__":
    main()
