"""Open Track: HMAC signing + server-side verification of action logs.

Open Track lets a provider run the 1v15 game with their OWN harness and submit
a *replayable, signed* action log; we verify it server-side. This module is the
signing/verification layer on top of the deterministic replay substrate
(runner.manifest.rebuild_game): `seed + action log = bit-identical replay`.

Two independent checks, both must pass for a submission to count:

  1. INTEGRITY (HMAC-SHA256) — the submitted log + config were not altered after
     signing. We canonicalize the run into a stable byte payload and HMAC it with
     a shared secret. Any edit to actions.jsonl or the signed manifest fields
     flips the digest.
  2. REPLAY — re-applying the action log against a fresh engine reproduces the
     claimed final score. This is what stops a forged/edited log: even a
     correctly-HMAC'd log is rejected if it doesn't replay to its stated score.
     (Integrity proves "not tampered since signing"; replay proves "these
     actions actually produce this score". Open Track needs both.)

The secret is NEVER passed on argv or stored in the repo. `resolve_secret`
reads it from an env var or a file (mirroring league.config.resolve_key).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib

_ALGO = "HMAC-SHA256"
_ENV_SECRET = "FMBENCH_OPENTRACK_SECRET"
# manifest fields that are bound into the signature (the claim being attested).
_SIGNED_MANIFEST_FIELDS = (
    "run_id", "config", "s_final", "settle_reason",
    "engine_commit", "params_hash", "score_version",
)


def resolve_secret(ref: str | None = None) -> str:
    """Resolve the HMAC secret from `env:NAME` / `file:PATH`, or the default env
    var when `ref` is None. Never accepts a literal secret (keeps it off argv)."""
    if ref is None:
        val = os.environ.get(_ENV_SECRET)
        if not val:
            raise RuntimeError(
                f"no Open Track secret: set ${_ENV_SECRET} or pass "
                "--secret env:NAME / file:PATH")
        return val
    if ref.startswith("env:"):
        val = os.environ.get(ref[4:])
        if not val:
            raise RuntimeError(f"env var {ref[4:]} is empty/unset")
        return val
    if ref.startswith("file:"):
        return pathlib.Path(ref[5:]).read_text(encoding="utf-8").strip()
    raise ValueError("--secret must be env:NAME or file:PATH (never a literal)")


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    if path.exists():
        h.update(path.read_bytes())
    return h.hexdigest()


def canonical_payload(run_dir: str | pathlib.Path) -> dict:
    """The stable, order-independent claim we sign: selected manifest fields +
    a content hash of the exact actions.jsonl bytes."""
    d = pathlib.Path(run_dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    payload = {k: manifest.get(k) for k in _SIGNED_MANIFEST_FIELDS}
    payload["actions_sha256"] = _sha256_file(d / "actions.jsonl")
    payload["algo"] = _ALGO
    return payload


def _digest(payload: dict, secret: str) -> str:
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_run(run_dir: str | pathlib.Path, secret: str) -> dict:
    """Write signature.json into run_dir and return it."""
    d = pathlib.Path(run_dir)
    payload = canonical_payload(d)
    sig = {"algo": _ALGO, "digest": _digest(payload, secret), "payload": payload}
    (d / "signature.json").write_text(
        json.dumps(sig, indent=2, sort_keys=True), encoding="utf-8")
    return sig


def verify_integrity(run_dir: str | pathlib.Path, secret: str) -> dict:
    """Check signature.json against the current run contents. Returns
    {ok, reason}. Fails if the log or any signed manifest field changed, or the
    HMAC doesn't match (constant-time compare)."""
    d = pathlib.Path(run_dir)
    sig_path = d / "signature.json"
    if not sig_path.exists():
        return {"ok": False, "reason": "no signature.json"}
    sig = json.loads(sig_path.read_text(encoding="utf-8"))
    current = canonical_payload(d)
    if sig.get("payload") != current:
        return {"ok": False, "reason": "payload mismatch (log or config edited "
                "after signing)"}
    expected = _digest(current, secret)
    if not hmac.compare_digest(str(sig.get("digest", "")), expected):
        return {"ok": False, "reason": "HMAC digest mismatch (wrong secret or "
                "forged signature)"}
    return {"ok": True, "reason": "integrity ok"}


def verify_replay(run_dir: str | pathlib.Path, tol: float = 1e-6) -> dict:
    """Re-apply the action log against a fresh engine and confirm it reproduces
    the manifest's claimed s_final. Returns {ok, reason, claimed, replayed}."""
    from runner.manifest import rebuild_game

    d = pathlib.Path(run_dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    records = []
    ap = d / "actions.jsonl"
    if ap.exists():
        with open(ap, encoding="utf-8") as f:
            records = [json.loads(x) for x in f if x.strip()]
    try:
        game, packet = rebuild_game(manifest, records)
    except Exception as exc:  # replay diverged => log doesn't reproduce
        return {"ok": False, "reason": f"replay failed: {exc}"}
    if not packet.get("game_over"):
        return {"ok": False, "reason": "action log does not play to settlement"}
    replayed = game.final_result().get("s_final")
    claimed = manifest.get("s_final")
    if claimed is None:
        return {"ok": False, "reason": "manifest has no claimed s_final"}
    if replayed is None:
        return {"ok": False, "reason": "replay settled without an s_final"}
    if abs(float(replayed) - float(claimed)) > tol:
        return {"ok": False, "reason": "score mismatch", "claimed": claimed,
                "replayed": replayed}
    return {"ok": True, "reason": "replay reproduces claimed score",
            "claimed": claimed, "replayed": replayed}


def official_deviations(run_dir: str | pathlib.Path) -> dict:
    """Compare the signed run config against the official Open Track shape.

    Integrity + replay prove "this log is genuine and reproduces its score" —
    they do NOT prove the game played was the official one. A run with
    self-chosen param_overrides (easier board/economy/world), ablations, a
    non-standard track length, or a different starting club would verify
    fine while being incomparable. This is that missing gate. Harness-side
    knobs (max_tokens, turn limits, memory_mode) are NOT gated — Open Track
    explicitly allows a custom harness — but are surfaced for the reviewer.
    """
    d = pathlib.Path(run_dir)
    cfg = (json.loads((d / "manifest.json").read_text(encoding="utf-8"))
           .get("config") or {})
    devs = []
    if cfg.get("years") != 20:
        devs.append(f"years={cfg.get('years')} (official track: 20)")
    if (cfg.get("club") or "mid") != "mid":
        devs.append(f"club={cfg.get('club')} (official: mid)")
    if cfg.get("ablations"):
        devs.append(f"ablations={cfg['ablations']} (official: none)")
    if cfg.get("param_overrides"):
        devs.append("param_overrides set — game parameters modified")
    harness = {k: cfg.get(k) for k in ("max_tokens", "soft_turn_limit",
                                       "hard_turn_limit", "memory_mode")}
    return {"ok": not devs, "deviations": devs, "harness": harness}


def verify_submission(run_dir: str | pathlib.Path, secret: str,
                      replay: bool = True, official: bool = False) -> dict:
    """Full Open Track gate: integrity AND (optionally) replay must pass;
    with official=True the run config must also match the official track."""
    integ = verify_integrity(run_dir, secret)
    out = {"integrity": integ, "ok": integ["ok"]}
    if replay:
        rep = verify_replay(run_dir)
        out["replay"] = rep
        out["ok"] = out["ok"] and rep["ok"]
    if official:
        off = official_deviations(run_dir)
        out["official"] = off
        out["ok"] = out["ok"] and off["ok"]
    return out


def _main() -> None:
    ap = argparse.ArgumentParser(prog="fm-opentrack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sign", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True, help="results/runs/<run_id> path")
        p.add_argument("--secret", default=None,
                       help="env:NAME or file:PATH (default: $%s)" % _ENV_SECRET)
        if name == "verify":
            p.add_argument("--no-replay", action="store_true",
                           help="skip replay-score check (integrity only)")
            p.add_argument("--official", action="store_true",
                           help="also require the official track shape "
                                "(20y, mid club, no ablations/overrides)")
    args = ap.parse_args()
    secret = resolve_secret(args.secret)
    if args.cmd == "sign":
        sig = sign_run(args.run, secret)
        print(f"signed: {sig['digest']}")
    else:
        res = verify_submission(args.run, secret, replay=not args.no_replay,
                                official=args.official)
        print(json.dumps(res, indent=2, sort_keys=True))
        raise SystemExit(0 if res["ok"] else 1)


if __name__ == "__main__":
    _main()
