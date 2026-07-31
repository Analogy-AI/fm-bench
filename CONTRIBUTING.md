# Contributing to FM-Bench

Thanks for looking. FM-Bench is a *benchmark*, so the usual open-source bar is
joined by one extra requirement: **a change that silently moves scores is worse
than a bug.** Everything below exists to make score-moving changes explicit.

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # ~3 minutes, no API key needed
```

Python 3.11+. The engine is stdlib-only apart from `pyyaml`; provider SDKs are
needed only for LLM runs. Please don't add engine dependencies — determinism is
easier to defend with a small dependency surface.

## The four invariants

These are enforced by tests. If your change trips one, that is the test working.

1. **Determinism.** `seed + action log` must replay bit-identically
   (`tests/test_determinism.py`, `tests/test_replay.py`). No wall-clock time, no
   unseeded RNG, no set/dict iteration order leaking into simulation order.
   The RNG is counter-based (`engine/core/rng.py`); derive a new stream rather
   than reusing one, or you will shift every downstream draw.
2. **Truth isolation.** True abilities must never cross the observation
   boundary. `engine/obs/` is the only read path and filters everything into
   bands and tiers; AI decision code may not touch true attributes at all, which
   is checked at the AST level (`tests/test_isolation.py`). Adding a field to a
   stop packet is a hidden-information change — argue it explicitly.
3. **Single entrance.** `engine/obs/` (read) and `engine/actions/` (write) are
   the only doors. The runner, the web client and external harnesses all go
   through them; nothing outside `engine/` should import `engine.domain`.
4. **Frozen behavior.** `tests/golden_benchmark_hashes.json` pins state hashes,
   event-chain hashes and scores for known runs. A diff here means every
   previously published number is now incomparable.

## Two conventions worth knowing

**`params_hash` is a hash of `params.yaml` bytes, not of its parsed values.**
Editing a *comment* in `params.yaml` therefore changes the calibration identity
stamped into every result file, and makes new runs report as a mixed pool
against previously published ones. Treat `params.yaml` as append-only prose-wise:
if a comment must change, expect to re-measure and re-publish the anchor ladder,
or make the change at a point where you are re-measuring anyway.

**Citations like `DESIGN §2.2`, `DESIGN_v0.3 change 1` or `DECISIONS #5`** in
code comments refer to internal design records that are not part of this open
release. Where such a citation carries an invariant, the invariant is encoded in
`tests/`; the tag is provenance, not a document you are expected to find.

## Changes that move scores

Touching `params.yaml`, `score/`, `engine/sim/`, or a baseline changes the
meaning of every existing result. For those:

- Say so in the PR description, and say which direction and roughly how much.
- Regenerate the anchor ladder and include the before/after:
  ```bash
  python scripts/anchor_table.py --seeds 1 2 3 4 5 --years 5
  ```
- Re-measure, don't re-derive. Anchor numbers quoted in prose go stale silently;
  the script stamps `engine_commit + params_hash + score_version` so a stale
  table is detectable. If you update the ladder in `docs/FM_BENCH_GUIDE.md`
  §5.2, paste the script's output rather than editing cells by hand.
- Golden hashes are regenerated **deliberately**, never to make a red test go
  green. Explain in the PR why the old behavior was wrong.

A useful property of this benchmark's ladder: the anchors should stay in order
(random < greedy < heuristic < oracle). `scripts/anchor_table.py` reports
inversions. An inversion is a finding — report it rather than tuning until it
disappears.

## Reporting an information leak

If you find a way for an agent to recover hidden information — through the
observation layer, error hints, price channels, timing, or anything else — that
is the highest-value bug class here. Please report it privately first: see
[`SECURITY.md`](SECURITY.md).

## Style

Match the surrounding code: type hints, `from __future__ import annotations`,
comments that explain *why* a constant or an ordering matters rather than what
the line does. Docstrings on modules that carry an invariant should name the
invariant.
