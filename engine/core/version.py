"""Provenance stamp for result files (stats-harness requirement).

Results are only comparable when produced by the same engine code and the
same calibration constants; the stamp makes mixing detectable after the fact.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def engine_commit() -> str:
    """Short git commit of the tree the engine ran from ('unknown' outside git)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            commit = out.stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(_REPO_ROOT), "status", "--porcelain",
                 "--untracked-files=no"],
                capture_output=True, text=True, timeout=5)
            if dirty.returncode == 0 and dirty.stdout.strip():
                commit += "-dirty"
            return commit
    except Exception:
        pass
    return "unknown"


def params_hash() -> str:
    """First 12 hex of sha256 over params.yaml bytes (calibration identity)."""
    try:
        blob = (_REPO_ROOT / "params.yaml").read_bytes()
        return hashlib.sha256(blob).hexdigest()[:12]
    except Exception:
        return "unknown"


def stamp() -> dict:
    return {"engine_commit": engine_commit(), "params_hash": params_hash()}
