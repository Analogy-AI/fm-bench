"""Ladder report: aggregate results/*.json into a comparison table.

    python scripts/ladder_report.py [--track 5] [--glob "results/*.json"]
                                    [--out results/LADDER.md]

- Groups runs by (track length, label); NEVER mixes track lengths in one
  table (rho bases differ — scores are only comparable within a track).
- label = model for LLM runs, agent name otherwise, plus ablation suffix.
- mean with bootstrap 95% CI (seed-level resampling, deterministic RNG).
- Oracle rows are annotated: privileged information ceiling, not a
  legitimate ladder entry.

Runs are tagged with `score_version/engine_commit@params_hash`; a track
pooling more than one tag is flagged with a MIXED VERSIONS warning, since
scores from different calibration rounds are not comparable. Filter with
--glob to build a single-version table.
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# `python scripts/ladder_report.py` puts scripts/ on sys.path, not the repo
# root, so the deferred `score.composite` import below needs this (mirrors
# scripts/rescore.py and scripts/dump_reference_world.py).
sys.path.insert(0, str(ROOT))
BOOT_N = 10_000
BOOT_SEED = 20260704


def label_of(summary: dict) -> str:
    base = summary.get("model") or summary["agent"]
    abl = summary.get("ablations") or []
    return f"{base} [ablate: {','.join(abl)}]" if abl else base


def load_runs(pattern: str) -> dict:
    """-> {(years, label): [run dicts]}"""
    groups: dict = {}
    for path in sorted(glob.glob(pattern)):
        p = pathlib.Path(path)
        if p.name == "LADDER.md" or p.is_dir():
            continue
        try:
            summary = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(summary, dict) or "per_seed" not in summary:
            continue
        years = summary.get("years")
        label = label_of(summary)
        version = "@".join([
            summary.get("engine_commit", "unstamped"),
            summary.get("params_hash", "unstamped"),
        ])
        for run in summary["per_seed"]:
            run = dict(run)
            run["_file"] = p.name
            run["_label"] = label
            run["_version"] = f"{run.get('score_version', '?')}/{version}"
            groups.setdefault((years, label), []).append(run)
    return groups


def bootstrap_ci(scores: list[float]) -> tuple[float, float]:
    if len(scores) == 1:
        return scores[0], scores[0]
    rng = random.Random(BOOT_SEED)
    means = sorted(
        sum(rng.choice(scores) for _ in scores) / len(scores)
        for _ in range(BOOT_N))
    return means[int(0.025 * BOOT_N)], means[int(0.975 * BOOT_N)]


def survival_curve(runs: list[dict], years: int) -> list[float]:
    out = []
    for y in range(1, years + 1):
        alive = sum(1 for r in runs
                    if r.get("settle_reason") == "completed"
                    or (r.get("settle_t") or 0) >= y)
        out.append(alive / len(runs))
    return out


def fmt_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def build_report(groups: dict, track: int | None) -> str:
    lines = ["# FM Bench Ladder Report", ""]
    lines.append("Scores are comparable ONLY within one track length "
                 "(rho settlement bases differ across tracks).")
    lines.append("`oracle` is intentionally privileged (reads true hidden "
                 "state): it anchors the information ceiling and is not a "
                 "legitimate ladder entry.")
    lines.append("")
    tracks = sorted({y for (y, _) in groups if y is not None})
    if track is not None:
        tracks = [t for t in tracks if t == track]
    for years in tracks:
        rows = [(label, runs) for (y, label), runs in sorted(groups.items())
                if y == years]
        if not rows:
            continue
        lines.append(f"## {years}-year track")
        lines.append("")
        versions = sorted({r["_version"] for _, runs in rows for r in runs})
        if len(versions) > 1:
            lines.append("⚠ **MIXED VERSIONS pooled in this table** — "
                         "runs came from different score/engine/params "
                         "revisions; treat cross-row comparisons as invalid:")
            for v in versions:
                lines.append(f"  - `{v}`")
        else:
            lines.append(f"version: `{versions[0]}`")
        lines.append("")
        lines.append(fmt_row(["agent/model", "runs", "mean", "display",
                              "95% CI", "min…max", "survival",
                              "settle reasons", "mean cost $"]))
        lines.append(fmt_row(["---"] * 9))
        rows.sort(key=lambda kv: -(sum(r["s_final"] for r in kv[1])
                                   / len(kv[1])))
        for label, runs in rows:
            scores = [r["s_final"] for r in runs]
            mean = sum(scores) / len(scores)
            lo, hi = bootstrap_ci(scores)
            reasons: dict[str, int] = {}
            for r in runs:
                reasons[r["settle_reason"]] = \
                    reasons.get(r["settle_reason"], 0) + 1
            completed = reasons.get("completed", 0)
            costs = [r["cost"]["cost_usd"] for r in runs if "cost" in r]
            mark = " ⚠privileged" if label.startswith("oracle") else ""
            from score.composite import display_score
            lines.append(fmt_row([
                f"{label}{mark}", str(len(runs)), f"{mean:.2f}",
                f"{display_score(mean):.1f}",
                f"[{lo:.2f}, {hi:.2f}]",
                f"{min(scores):.2f}…{max(scores):.2f}",
                f"{completed}/{len(runs)} completed",
                ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items())),
                f"{sum(costs) / len(costs):.2f}" if costs else "0 (scripted)",
            ]))
        lines.append("")
        # survival curves
        lines.append("Survival (fraction of runs alive through year N):")
        lines.append("")
        lines.append(fmt_row(["agent/model"]
                             + [f"Y{y}" for y in range(1, years + 1)]))
        lines.append(fmt_row(["---"] * (years + 1)))
        for label, runs in rows:
            curve = survival_curve(runs, years)
            lines.append(fmt_row([label] + [f"{v:.0%}" for v in curve]))
        lines.append("")
        # per-seed breakdown
        lines.append("<details><summary>Per-run breakdown</summary>")
        lines.append("")
        lines.append(fmt_row(["agent/model", "seed", "rep", "score",
                              "settle", "t", "file"]))
        lines.append(fmt_row(["---"] * 7))
        for label, runs in rows:
            for r in sorted(runs, key=lambda r: (r["seed"],
                                                 r.get("repeat", 0))):
                lines.append(fmt_row([
                    label, str(r["seed"]), str(r.get("repeat", 0)),
                    f"{r['s_final']:.2f}", r["settle_reason"],
                    f"{r.get('settle_t', 0):.2f}", r["_file"]]))
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default=str(ROOT / "results" / "*.json"))
    ap.add_argument("--track", type=int, default=None,
                    help="only this track length (e.g. 5)")
    ap.add_argument("--out", default=str(ROOT / "results" / "LADDER.md"))
    args = ap.parse_args()
    groups = load_runs(args.glob)
    if not groups:
        raise SystemExit(f"no result files matched {args.glob}")
    report = build_report(groups, args.track)
    pathlib.Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
