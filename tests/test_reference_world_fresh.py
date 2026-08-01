"""The published reference world must still be what the engine generates.

`data/reference_world_seed1.json` is the one place true player abilities are
published, and readers use it to check that the observation layer really does
hide them. If worldgen or params.yaml drifts and the file is not regenerated,
that check silently compares against a world nobody plays.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "data" / "reference_world_seed1.json"


def test_reference_world_matches_current_engine(tmp_path):
    out = tmp_path / "regen.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dump_reference_world.py"),
         "--seed", "1", "--out", str(out)],
        cwd=ROOT, check=True, capture_output=True)
    published = json.loads(REFERENCE.read_text())
    regenerated = json.loads(out.read_text())
    assert published == regenerated, (
        "data/reference_world_seed1.json is stale — regenerate it with "
        "`python scripts/dump_reference_world.py --seed 1 "
        "--out data/reference_world_seed1.json`")


def test_reference_world_publishes_truth_and_band():
    """Guards the file's purpose: it must carry BOTH the hidden truth and the
    band a manager sees, otherwise it cannot demonstrate the gap."""
    players = json.loads(REFERENCE.read_text())["players"]
    assert players, "reference world has no players"
    for p in players[:20]:
        assert "true_ca" in p
        band = p["scout_band"]
        assert band["ca_low"] <= band["ca_high"]
