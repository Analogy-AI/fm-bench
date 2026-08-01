"""`rescore.py results/*.json` must survive a mixed results directory.

results/ accumulates more than scored run summaries: per-run manifests, arena
aggregates, prompt smoke tests with no seeds, and derived visualization data.
The natural glob invocation hits all of them, and dying on the first one made
the tool unusable against a real archive (43 of 158 files in the dev archive
are not run summaries).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESCORE = ROOT / "scripts" / "rescore.py"

VALID = {
    "agent": "heuristic", "club": "mid", "years": 5,
    "mean_score": 1.0, "min_score": 1.0, "max_score": 1.0,
    "per_seed": [{
        "seed": 1, "repeat": 0, "s_final": 1.0, "settle_reason": "completed",
        "settle_t": 5.0, "years": 5, "rho": 1.0,
        # channel keys must match score/composite.py's output; rescore_run
        # reads net_worth_real_m / honors_points / squad_value_real_m.
        "channels": {
            "honors_points": 20, "honors_term": 5.1783,
            "net_worth_baseline_m": 180.61, "net_worth_real_m": 195.84,
            "net_worth_term": 3.2275, "net_worth_va_m": 15.24,
            "squad_value_real_m": 68.06, "squad_value_term": 3.6937,
        },
    }],
}


def _run(*paths):
    return subprocess.run([sys.executable, str(RESCORE), *map(str, paths)],
                          cwd=ROOT, capture_output=True, text=True)


def test_skips_non_summaries_instead_of_crashing(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(VALID))
    (tmp_path / "manifest.json").write_text(json.dumps({"run_id": "x"}))
    (tmp_path / "arena.json").write_text(json.dumps({"seats": [1, 2]}))
    (tmp_path / "empty.json").write_text(json.dumps({**VALID, "per_seed": []}))
    (tmp_path / "alist.json").write_text(json.dumps([1, 2, 3]))
    (tmp_path / "broken.json").write_text("{not json")

    r = _run(*sorted(tmp_path.glob("*.json")))
    assert r.returncode == 0, r.stderr
    # the one real summary was processed...
    assert "good.json" in r.stdout
    # ...and every skip is REPORTED, not silent
    assert "skipped 5 file(s)" in r.stdout
    for name in ("manifest.json", "arena.json", "empty.json", "alist.json",
                 "broken.json"):
        assert name in r.stdout


def test_still_rescores_a_valid_summary(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(VALID))
    r = _run(good)
    assert r.returncode == 0, r.stderr
    assert "seed 1 rep 0" in r.stdout
    assert "mean:" in r.stdout


def test_write_mode_never_mutates_the_input(tmp_path):
    good = tmp_path / "good.json"
    original = json.dumps(VALID)
    good.write_text(original)
    r = _run(good, "--write")
    assert r.returncode == 0, r.stderr
    assert good.read_text() == original
    assert (tmp_path / "good.rescored.json").exists()
