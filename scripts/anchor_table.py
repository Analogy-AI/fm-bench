"""Regenerate the scripted-anchor ladder table from the engine as shipped.

    python scripts/anchor_table.py                      # seeds 1-5, 5-year
    python scripts/anchor_table.py --seeds 1 2 3 --years 20
    python scripts/anchor_table.py --out docs/ANCHORS.md

Why this exists: anchor numbers quoted in prose go stale the moment
`params.yaml` or a baseline changes, and a stale ladder silently
mis-scales every score on it. This script re-measures every scripted
anchor on PUBLIC seeds and stamps the output with
`engine_commit + params_hash + score version`, so a reader can tell at a
glance whether a quoted table still describes the code in front of them.

Scripted anchors only: they are free, deterministic, and need no API key,
so the whole table reproduces on a laptop. LLM seats are not anchors and
are deliberately absent.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine.core.version import stamp
from run_benchmark import run_benchmark
from score.composite import SCORE_VERSION

# Ladder order is the CLAIM being checked: each anchor should outscore the
# one above it. Printed in this order so a broken ordering is visible.
ANCHORS = [
    ("random", "uniform sampling of legal actions"),
    ("idle", "pure AFK (never acts, only advances)"),
    ("greedy", "buys expensive, no long-term investment"),
    ("heuristic", "disciplined deterministic FM script"),
    ("oracle_v0", "god-mode script, conservative historical ceiling"),
    ("oracle", "god-mode script (oracle_v2), calibrated ceiling"),
]


def measure(agent: str, seeds: list[int], years: int) -> dict:
    summary = run_benchmark(agent=agent, seeds=seeds, years=years,
                            verbose=False, run_label=f"anchor_{agent}")
    per = summary["per_seed"]
    scores = [r["s_final"] for r in per]
    settles: dict[str, int] = {}
    for r in per:
        settles[r["settle_reason"]] = settles.get(r["settle_reason"], 0) + 1
    return {
        "mean": summary["mean_score"],
        "min": round(min(scores), 2),
        "max": round(max(scores), 2),
        "settles": settles,
        "n": len(per),
    }


def fmt_settles(settles: dict[str, int], n: int) -> str:
    if len(settles) == 1:
        only = next(iter(settles))
        return f"all {only}"
    parts = sorted(settles.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{c}/{n} {reason}" for reason, c in parts)


def build_table(seeds: list[int], years: int) -> str:
    st = stamp()
    lines = [
        f"### Scripted anchor ladder — {years}-year track, "
        f"public seeds {seeds[0]}-{seeds[-1]}"
        if seeds == list(range(seeds[0], seeds[-1] + 1))
        else f"### Scripted anchor ladder — {years}-year track, seeds {seeds}",
        "",
        f"Measured by `scripts/anchor_table.py` on `engine_commit "
        f"{st['engine_commit']}` / `params_hash {st['params_hash']}` / "
        f"`{SCORE_VERSION}`. Reproduce with:",
        "",
        "```bash",
        f"python scripts/anchor_table.py --seeds {' '.join(map(str, seeds))} "
        f"--years {years}",
        "```",
        "",
        f"| Anchor | Definition | Mean | Range (min..max) | Outcome |",
        "|---|---|---:|---|---|",
    ]
    results = {}
    for agent, desc in ANCHORS:
        r = measure(agent, seeds, years)
        results[agent] = r
        lines.append(
            f"| `{agent}` | {desc} | **{r['mean']:+.2f}** | "
            f"{r['min']:+.2f} .. {r['max']:+.2f} | "
            f"{fmt_settles(r['settles'], r['n'])} |")
    lines += ["",
              f"Scores are means over {len(seeds)} seeds, one repeat each; "
              "single-seed spread is wide, so read the range column too. "
              "Scores are comparable **only** within one track length, one "
              "`params_hash`, and one score version."]

    # Ordering is the load-bearing property of a ladder; say so out loud
    # rather than leaving a silent inversion in a published table.
    order = [a for a, _ in ANCHORS]
    means = [results[a]["mean"] for a in order]
    inversions = [(order[i], means[i], order[i + 1], means[i + 1])
                  for i in range(len(order) - 1) if means[i] > means[i + 1]]
    if inversions:
        lines += ["", "**Ordering inversions on this seed set** (an anchor "
                      "scoring above the anchor below it):", ""]
        lines += [f"- `{a}` ({am:+.2f}) > `{b}` ({bm:+.2f})"
                  for a, am, b, bm in inversions]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    table = build_table(sorted(args.seeds), args.years)
    if args.out:
        args.out.write_text(table)
        print(f"-> {args.out}")
    else:
        print(table, end="")


if __name__ == "__main__":
    main()
