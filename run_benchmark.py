"""FM Bench — THE entry point (DECISIONS #3/#4).

    from run_benchmark import run_benchmark
    run_benchmark()                        # default tier: cheap smoke run
    run_benchmark(agent="heuristic", years=5, seeds=[1, 2, 3])

    python run_benchmark.py --agent heuristic --years 5 --seeds 1 2 3
    python run_benchmark.py --agent claude --tier default
    python run_benchmark.py --tier official --confirm-official   # $$$$

Budget tiers are config presets over the same code path, not separate modes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

from baselines import POLICIES
from engine.core.version import stamp
from engine.game import Game

RESULTS_DIR = pathlib.Path(__file__).resolve().parent / "results"

def derive_salted_seed(nonce: int, salt: str) -> int:
    """Official-run anti-fingerprinting: effective seed = HMAC(salt, nonce).

    The engine is deliberately open source, so world contents (player names,
    squads) are a pure function of the seed — with small public seeds an
    adversary can pre-generate worlds offline and identify the live seed from
    a few observed names, unlocking all hidden truth. Deriving the seed
    through a SECRET salt (never in the repo; .env / env var
    FMBENCH_WORLD_SALT) makes offline enumeration impossible while keeping
    everything downstream deterministic and replayable (by us, salt holders).
    Publish nonces; disclose effective seeds only after an eval round closes.
    """
    import hmac as _hmac
    digest = _hmac.new(salt.encode("utf-8"), f"fmb1:{nonce}".encode("utf-8"),
                       "sha256").digest()
    return int.from_bytes(digest[:8], "big") & (2**63 - 1)


TIERS = {
    # cheap by default: short track, one seed, cheap model, hard spend cap
    "default": {"model": "claude-haiku-4-5-20251001", "repeats": 1,
                "seeds": [1], "spend_cap_usd": 5.0, "years": 5},
    # cheapest possible: shrunk world (1 division x 8 teams) + 3y track for
    # near-free pipeline validation (reviewer's "money-saving mode")
    "cheap": {"model": "claude-haiku-4-5-20251001", "repeats": 1,
              "seeds": [1], "spend_cap_usd": 2.0, "years": 3,
              "world_size": "1x8"},
    # full protocol; a large paid run — never
    # runs without --confirm-official. Harness limits are raised here so the
    # official run never handicaps the model on output length or reasoning
    # rounds; these values are FIXED (harness frozen), not per-model tuned.
    # official seeds are NONCES: real world seeds derive through the secret
    # salt (see derive_salted_seed) so the open engine can't be used to
    # pre-generate and fingerprint official worlds.
    "official": {"model": "claude-opus-4-8", "repeats": 3,
                 "seeds": list(range(1, 31)), "spend_cap_usd": None,
                 "years": 20, "max_tokens": 8000, "salted": True,
                 "soft_turn_limit": 40, "hard_turn_limit": 60},
}

# opponent-advantage presets for hard_offline mode (applied to AI clubs only)
MODE_OVERRIDES = {
    "easy": {},                                    # current behavior, no change
    "hard": {"world": {"opponent_cash_mult": 1.8}},  # AI clubs start richer
}


def build_overrides(mode: str = "easy", notebook_cap: int | None = None,
                    world_size: str | None = None, extra: dict | None = None,
                    tier_overrides: dict | None = None,
                    tier_mix: str = "none", draft: bool = False,
                    sealed: bool = False, revival: bool = False) -> dict:
    """Translate scalable run-config into one param_overrides dict.

    Every knob defaults to current behavior, so an unconfigured run merges an
    empty dict and stays byte-identical. Merge order (later wins): mode preset
    -> world size -> notebook cap -> tier overrides -> explicit extra.
    """
    from engine.core.params import _deep_merge
    ov: dict = {}
    _deep_merge(ov, MODE_OVERRIDES.get(mode, {}))
    if world_size:
        div, teams = (int(x) for x in world_size.lower().split("x"))
        # double round-robin => 2*(teams-1) rounds; keep w.rounds consistent
        # with the actual fixture length so the schedule doesn't over/under-run.
        _deep_merge(ov, {"world": {"divisions": div, "clubs_per_division": teams,
                                   "rounds": 2 * (teams - 1)}})
    if notebook_cap is not None:
        _deep_merge(ov, {"stops": {"notebook_max_chars": int(notebook_cap)}})
    if draft:
        # v0.3 equal-endowment draft world (protocol v0.3, change 1)
        _deep_merge(ov, {"draft": {"enabled": True}})
    if sealed:
        # v0.3 sealed-bid market (DESIGN change 2)
        _deep_merge(ov, {"market_ai": {"sealed_bids": True}})
    if revival:
        # v0.3 revival + uniform bailout (DESIGN change 3)
        _deep_merge(ov, {"revival": {"enabled": True}})
    if tier_mix and tier_mix != "none":
        # 5/5/5 opponent competence tiers (fixed wealth-interleaved assignment
        # in params.opponents; "none" merges nothing = byte-identical default)
        _deep_merge(ov, {"opponents": {"tier_mix": str(tier_mix)}})
    if tier_overrides:
        _deep_merge(ov, tier_overrides)
    if extra:
        _deep_merge(ov, extra)
    return ov


def run_benchmark(agent: str = "claude", seeds: list[int] | None = None,
                  years: int | None = None, model: str | None = None,
                  budget_tier: str = "default",
                  spend_cap_usd: float | None = None, repeats: int | None = None,
                  club: str = "mid", verbose: bool = True,
                  run_label: str | None = None,
                  adapter_factory=None,
                  ablations: list[str] | None = None,
                  mode: str = "easy", notebook_cap: int | None = None,
                  world_size: str | None = None,
                  max_tokens: int | None = None,
                  soft_turn_limit: int | None = None,
                  hard_turn_limit: int | None = None,
                  memory_mode: str = "flat",
                  parallel_seeds: int = 1, salted: bool = False,
                  param_overrides: dict | None = None,
                  cap_mode: str = "settle", tier_mix: str = "none",
                  draft: bool = False, sealed: bool = False,
                  revival: bool = False) -> dict:
    """Run one benchmark evaluation; explicit args override the tier preset.

    parallel_seeds > 1 runs that many seeds concurrently (each seed is an
    independent deterministic world and an independent conversation, so
    parallelism does not affect results — only wall clock). The spend cap
    stays INVOCATION-cumulative via a shared thread-safe pool; seed starts
    are staggered a few seconds so the first request warms the prompt cache
    for the rest."""
    tier = TIERS[budget_tier]
    ablations = sorted(ablations or [])
    seeds = seeds if seeds is not None else tier["seeds"]
    years = years if years is not None else tier.get("years")
    model = model if model is not None else tier["model"]
    repeats = repeats if repeats is not None else tier["repeats"]
    cap = spend_cap_usd if spend_cap_usd is not None else tier["spend_cap_usd"]
    world_size = world_size if world_size is not None else tier.get("world_size")
    # harness limits: explicit arg > tier preset > LLMAgent default (None)
    max_tokens = max_tokens if max_tokens is not None else tier.get("max_tokens")
    soft_turn_limit = (soft_turn_limit if soft_turn_limit is not None
                       else tier.get("soft_turn_limit"))
    hard_turn_limit = (hard_turn_limit if hard_turn_limit is not None
                       else tier.get("hard_turn_limit"))
    # scalable run-config -> a single param_overrides dict (see build_overrides)
    overrides = build_overrides(mode=mode, notebook_cap=notebook_cap,
                                world_size=world_size, extra=param_overrides,
                                tier_overrides=tier.get("param_overrides"),
                                tier_mix=tier_mix, draft=draft,
                                sealed=sealed, revival=revival)

    # salted mode: the requested seeds are public NONCES; the worlds actually
    # played derive via HMAC(secret salt, nonce) — see derive_salted_seed.
    salted = salted or bool(tier.get("salted"))
    nonce_of: dict[int, int] = {}
    if salted:
        from runner.keys import get_secret
        salt = get_secret("FMBENCH_WORLD_SALT")
        if not salt:
            raise RuntimeError("salted run requires FMBENCH_WORLD_SALT "
                               "(env var or .env entry)")
        eff = [derive_salted_seed(n, salt) for n in seeds]
        nonce_of = dict(zip(eff, seeds))
        seeds = eff

    per_seed = []
    t0 = time.time()
    # the cap is cumulative across the WHOLE invocation (all seeds x repeats),
    # not per seed: each run inherits the spend committed by earlier runs
    jobs = [(seed, rep) for seed in seeds for rep in range(repeats)]
    skipped: list[dict] = []
    suspended: list[dict] = []
    from runner.costs import RunSuspended

    if parallel_seeds > 1 and agent == "claude" and len(jobs) > 1:
        # parallel path: shared thread-safe pool keeps the cap cumulative;
        # results are re-ordered to job order for a deterministic summary.
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from runner.costs import SharedSpendPool
        pool = SharedSpendPool()
        stagger = threading.Semaphore(1)

        def one(job):
            seed, rep = job
            if pool.total() > (cap or float("inf")):
                return {"skipped": True, "repeat": rep, "seed": seed}
            with stagger:  # first request warms the prompt cache for the rest
                time.sleep(0.1)
            try:
                result = _run_one(agent, seed, years, model, cap, club,
                                  verbose, adapter_factory=adapter_factory,
                                  ablations=ablations,
                                  param_overrides=overrides,
                                  max_tokens=max_tokens,
                                  soft_turn_limit=soft_turn_limit,
                                  hard_turn_limit=hard_turn_limit,
                                  memory_mode=memory_mode, spend_pool=pool,
                                  cap_mode=cap_mode)
            except RunSuspended as e:
                return {"skipped": True, "repeat": rep, "seed": seed,
                        "suspended": {"repeat": rep, "run_id": e.run_id,
                                      "seed": nonce_of.get(seed, seed),
                                      "spent_usd": round(e.spent_usd, 4)}}
            result["repeat"] = rep
            if verbose:
                shown = nonce_of.get(seed, seed)
                print(f"[bench] agent={agent} seed={shown} rep={rep} "
                      f"score={result['s_final']:.2f} "
                      f"settle={result['settle_reason']} "
                      f"pool=${pool.total():.2f}")
            return result

        with ThreadPoolExecutor(max_workers=parallel_seeds) as ex:
            outs = list(ex.map(one, jobs))
        for r in outs:
            if r.get("skipped"):
                if r.get("suspended"):
                    suspended.append(r["suspended"])
                skipped.append({"repeat": r["repeat"], "seed": r["seed"]})
            else:
                if ablations:
                    r["ablations"] = ablations
                per_seed.append(r)
        cap_tripped = (any(r.get("aborted_by_spend_cap") for r in per_seed)
                       or bool(suspended))
    else:
        spent_so_far = 0.0
        cap_tripped = False
        for seed, rep in jobs:
            if cap_tripped:
                skipped.append({"repeat": rep, "seed": seed})
                continue
            try:
                result = _run_one(agent, seed, years, model, cap, club,
                                  verbose, prior_spend_usd=spent_so_far,
                                  adapter_factory=adapter_factory,
                                  ablations=ablations,
                                  param_overrides=overrides,
                                  max_tokens=max_tokens,
                                  soft_turn_limit=soft_turn_limit,
                                  hard_turn_limit=hard_turn_limit,
                                  memory_mode=memory_mode, cap_mode=cap_mode)
            except RunSuspended as e:
                suspended.append({"repeat": rep, "run_id": e.run_id,
                                  "seed": nonce_of.get(seed, seed),
                                  "spent_usd": round(e.spent_usd, 4)})
                cap_tripped = True
                if verbose:
                    print(f"[bench] suspended at cap — continue with "
                          f"--resume {e.run_id} --spend-cap-usd <new cap>")
                continue
            result["repeat"] = rep
            if ablations:
                result["ablations"] = ablations
            per_seed.append(result)
            spent_so_far += result.get("cost", {}).get("cost_usd", 0.0)
            if result.get("aborted_by_spend_cap"):
                cap_tripped = True
                if verbose:
                    print(f"[bench] spend cap ${cap:.2f} reached "
                          f"(${spent_so_far:.2f} cumulative) — "
                          f"skipping remaining seeds")
            if verbose:
                shown = nonce_of.get(seed, seed)
                print(f"[bench] agent={agent} seed={shown} rep={rep} "
                      f"score={result['s_final']:.2f} "
                      f"settle={result['settle_reason']} "
                      f"spent=${spent_so_far:.2f}")

    if salted:
        # shareable results carry NONCES only; effective seeds stay in the
        # private per-run manifests (needed for our replay/resume)
        for r in per_seed:
            if r.get("seed") in nonce_of:
                r["seed_nonce"] = nonce_of[r["seed"]]
                r["seed"] = nonce_of[r["seed"]]
        seeds = sorted({nonce_of.get(s, s) for s in seeds})

    scores = [r["s_final"] for r in per_seed]
    summary = {
        **stamp(),
        "ablations": ablations,
        "agent": agent,
        "budget_tier": budget_tier,
        "club": club,
        "cost_usd_total": round(sum(
            r.get("cost", {}).get("cost_usd", 0.0) for r in per_seed), 4),
        # comparability stamp: every knob that changes what was run — without
        # these, a --mode hard --world 1x8 --memory-mode retrieval run is
        # indistinguishable from an official-shape run in results/*.json and
        # ladder aggregation can silently mix pools.
        "hard_turn_limit": hard_turn_limit,
        "max_tokens": max_tokens,
        "memory_mode": memory_mode,
        "mode": mode,
        "tier_mix": tier_mix,
        "notebook_cap": notebook_cap,
        "param_overrides": overrides or None,
        "protocol_draft": bool(draft),
        "protocol_sealed": bool(sealed),
        "protocol_revival": bool(revival),
        "protocol_version": ("fm_v0.3" if (draft and sealed and revival)
                             else "fm_v0.2" if not (draft or sealed or revival)
                             else "fm_v0.3_partial"),
        "salted": salted,
        "soft_turn_limit": soft_turn_limit,
        "world_size": world_size,
        "mean_score": round(sum(scores) / len(scores), 3) if scores else None,
        "min_score": round(min(scores), 3) if scores else None,
        "max_score": round(max(scores), 3) if scores else None,
        "model": model if agent == "claude" else None,
        # wire-path stamp (from the runs themselves): "openai-responses:*"
        # runs reason; legacy "openai-chat" gpt-5.6 runs were reasoning=none
        "provider_adapter": next(
            (r.get("provider_adapter") for r in per_seed
             if r.get("provider_adapter")), None),
        "per_seed": per_seed,
        "repeats": repeats,
        "seeds": seeds,
        "wall_clock_s": round(time.time() - t0, 1),
        "years": years,
    }
    if cap_tripped:
        summary["aborted_by_spend_cap"] = True
        summary["seeds_skipped"] = skipped
    if suspended:
        # unscored, resumable: NOT results — pointers to paused archives
        summary["suspended_runs"] = suspended
    RESULTS_DIR.mkdir(exist_ok=True)
    label = run_label or f"{agent}_{years}y_{len(seeds)}seeds_{int(time.time())}"
    out = RESULTS_DIR / f"{label}.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True))
    if verbose:
        print(f"[bench] mean={summary['mean_score']} -> {out}")
    return summary


def _run_one(agent: str, seed: int, years: int, model: str,
             spend_cap_usd: float | None, club: str, verbose: bool,
             prior_spend_usd: float = 0.0, adapter_factory=None,
             ablations: list[str] | None = None,
             param_overrides: dict | None = None,
             max_tokens: int | None = None,
             soft_turn_limit: int | None = None,
             hard_turn_limit: int | None = None,
             memory_mode: str = "flat", spend_pool=None,
             cap_mode: str = "settle") -> dict:
    game = Game(seed=seed, years=years, player_club=club,
                ablations=ablations, param_overrides=param_overrides or None)
    if agent == "claude":
        from runner.keys import find_api_key, provider_for_model
        from runner.llm_agent import LLMAgent
        from runner.manifest import RunRecorder, new_run_id
        from runner.providers import adapter_name_for_model
        adapter = adapter_factory(model) if adapter_factory else None
        key = "unused"
        if adapter is None:
            provider = provider_for_model(model)
            # "openai" is the deliberate catch-all for OpenAI-compatible
            # endpoints, so an unrecognized id cannot be a hard error -- but a
            # typo'd id resolves the same silent way, and the failure only
            # surfaces later as an opaque 404 mid-run. Say so up front.
            if adapter_name_for_model(model) == "unknown":
                print(f"[bench] WARNING: model id {model!r} matches no known "
                      f"provider prefix; routing to '{provider}' as an "
                      f"OpenAI-compatible id. Check for a typo.")
            key = find_api_key(provider)
            if key is None:
                raise RuntimeError(
                    f"no {provider} API key found (env var or fallback config)")
        # wire-path stamp: distinguishes reasoning-capable openai-responses
        # runs from legacy openai-chat (reasoning=none) runs in results
        provider_adapter = (
            getattr(adapter, "adapter_name", type(adapter).__name__)
            if adapter is not None else adapter_name_for_model(model))
        run_id = new_run_id("claude", seed, model)
        recorder = RunRecorder(run_id, {
            "ablations": sorted(ablations or []), "agent": "claude",
            "club": club, "model": model, "seed": seed,
            "provider_adapter": provider_adapter,
            "spend_cap_usd": spend_cap_usd, "years": years,
            # persist harness limits so --resume replays the same harness
            "max_tokens": max_tokens, "soft_turn_limit": soft_turn_limit,
            "hard_turn_limit": hard_turn_limit, "memory_mode": memory_mode,
            # persist resolved param overrides so a non-default world replays
            # bit-identically (resume + Open Track verification)
            "param_overrides": param_overrides or None})
        if verbose:
            print(f"[bench] run_id={run_id} (crash-safe; continue with "
                  f"--resume {run_id})")
        llm = LLMAgent(model=model, api_key=key, spend_cap_usd=spend_cap_usd,
                       verbose=verbose, recorder=recorder, adapter=adapter,
                       prior_spend_usd=prior_spend_usd, ablations=ablations,
                       max_tokens=max_tokens, soft_turn_limit=soft_turn_limit,
                       hard_turn_limit=hard_turn_limit, memory_mode=memory_mode,
                       spend_pool=spend_pool, cap_mode=cap_mode)
        result = llm.play(game)
        result["run_id"] = run_id
        result["provider_adapter"] = provider_adapter
        result["sample_decisions"] = llm.decision_log[:40]
        return result
    policy = POLICIES.get(agent)
    if policy is None:
        raise ValueError(f"unknown agent {agent!r}; "
                         f"choose claude or one of {sorted(POLICIES)}")
    packet = game.start()
    while not packet.get("game_over"):
        policy(game, packet)
        packet = game.advance()
    return game.final_result()


def resume_run(run_id: str, verbose: bool = True,
               spend_cap_usd: float | None = None, reopen: bool = False,
               adapter_factory=None, cap_mode: str = "suspend") -> dict:
    """Continue a crashed/suspended LLM run from its persisted action log.

    spend_cap_usd, when given, REPLACES the run's cap (budget top-up) and is
    persisted to the manifest. reopen=True additionally allows continuing a
    run that was already SETTLED early by a cap trip: the truncated result
    stays on disk but the resumed result is stamped reopened=True and
    supersedes it for leaderboards. Runs that completed their full horizon
    never reopen — one world, one final score.
    """
    from runner.keys import find_api_key, provider_for_model
    from runner.llm_agent import LLMAgent
    from runner.manifest import RunRecorder, load_run, rebuild_game

    manifest, records = load_run(run_id)
    status = manifest.get("status")
    if status == "finished":
        if not reopen:
            raise SystemExit(
                f"run {run_id} already finished "
                f"(score {manifest.get('s_final')}); a cap-truncated run can "
                "be continued with --reopen plus a higher --spend-cap-usd")
        if manifest.get("settle_reason") == "completed":
            raise SystemExit(f"run {run_id} completed its full horizon; "
                             "nothing to reopen")
    cfg = manifest["config"]
    if spend_cap_usd is not None:
        cfg["spend_cap_usd"] = spend_cap_usd
    game, packet = rebuild_game(manifest, records)
    if verbose:
        done = manifest.get("stops_completed", 0)
        print(f"[bench] resuming {run_id}: {done} stops already played, "
              f"spend so far ${manifest.get('cost', {}).get('cost_usd', 0)}")
    adapter = adapter_factory(cfg["model"]) if adapter_factory else None
    key = "unused"
    if adapter is None:
        provider = provider_for_model(cfg["model"])
        key = find_api_key(provider)
        if key is None:
            raise RuntimeError(f"no {provider} API key found")
    recorder = RunRecorder(run_id, cfg, resume=True)
    recorder.manifest["config"] = cfg      # persist a topped-up cap
    if reopen and status == "finished":
        recorder.manifest["reopened"] = True
    llm = LLMAgent(model=cfg["model"], api_key=key, adapter=adapter,
                   spend_cap_usd=cfg.get("spend_cap_usd"), verbose=verbose,
                   recorder=recorder, ablations=cfg.get("ablations"),
                   max_tokens=cfg.get("max_tokens"),
                   soft_turn_limit=cfg.get("soft_turn_limit"),
                   hard_turn_limit=cfg.get("hard_turn_limit"),
                   memory_mode=cfg.get("memory_mode", "flat"),
                   cap_mode=cap_mode)
    if manifest.get("cost"):
        llm.cost.restore(manifest["cost"])
    result = llm.play(game, packet=packet)
    result["run_id"] = run_id
    result["resumed"] = True
    if reopen and status == "finished":
        # supersedes the truncated settle; leaderboards use this one
        result["reopened"] = True
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{run_id}_resumed.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True))
    if verbose:
        print(f"[bench] resumed run settled: {result['settle_reason']} "
              f"score={result['s_final']:.2f} -> {out}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(prog="fm-bench")
    ap.add_argument("--agent", default="claude",
                    choices=["claude", *sorted(POLICIES)])
    ap.add_argument("--tier", default="default", choices=sorted(TIERS))
    ap.add_argument("--seeds", type=int, nargs="+")
    ap.add_argument("--years", type=int)
    ap.add_argument("--model")
    ap.add_argument("--repeats", type=int)
    ap.add_argument("--spend-cap-usd", type=float)
    ap.add_argument("--club", default="mid", choices=["big", "mid", "small"])
    ap.add_argument("--mode", default="easy", choices=sorted(MODE_OVERRIDES),
                    help="easy = 15 rule-based opponents (default); "
                         "hard = opponents given advantages")
    ap.add_argument("--tier-mix", default="none", dest="tier_mix",
                    choices=["none", "555"],
                    help="opponent competence tiers: none = all-medium "
                         "(default, byte-identical); 555 = 5 easy / 5 medium "
                         "/ 5 hard scripted rivals, wealth-interleaved, fixed "
                         "assignment (same exam for every model)")
    ap.add_argument("--notebook-cap", type=int, default=None, dest="notebook_cap",
                    help="max notebook chars (0 = unlimited, the default); "
                         ">0 caps memory to test compression discipline")
    ap.add_argument("--world", default=None, dest="world_size",
                    help="world size DIVxTEAMS, e.g. 1x8 for a cheap fast sim "
                         "(engine default 1x16, the official standard)")
    ap.add_argument("--max-tokens", type=int, default=None, dest="max_tokens",
                    help="output-token cap per LLM call (default 1500; "
                         "official tier raises it to 8000)")
    ap.add_argument("--soft-turn-limit", type=int, default=None,
                    dest="soft_turn_limit",
                    help="round at which the 'converge' reminder fires "
                         "(default 25; official 40)")
    ap.add_argument("--hard-turn-limit", type=int, default=None,
                    dest="hard_turn_limit",
                    help="hard cap on rounds per decision stop "
                         "(default 40; official 60)")
    ap.add_argument("--draft", action="store_true",
                    help="v0.3 equal-endowment draft world (pool of 50, "
                         "uniform club shells)")
    ap.add_argument("--sealed", action="store_true",
                    help="v0.3 sealed-bid market (conflict-triggered "
                         "first-price auctions; DESIGN change 2)")
    ap.add_argument("--revival", action="store_true",
                    help="v0.3 revival + uniform bailout, max_deaths cap "
                         "(DESIGN change 3)")
    ap.add_argument("--salted", action="store_true",
                    help="treat --seeds as public nonces; real world seeds "
                         "derive via HMAC(FMBENCH_WORLD_SALT, nonce) so the "
                         "open engine can't fingerprint official worlds")
    ap.add_argument("--parallel-seeds", type=int, default=1,
                    dest="parallel_seeds",
                    help="run up to N seeds concurrently (results unchanged — "
                         "independent worlds; spend cap stays cumulative)")
    ap.add_argument("--memory-mode", default="flat",
                    choices=["flat", "retrieval"], dest="memory_mode",
                    help="flat (default) = whole notebook shown every stop; "
                         "retrieval = notebook shown relevance-ordered for the "
                         "current situation (deterministic tag/keyword match)")
    ap.add_argument("--ablate", nargs="+", default=None, dest="ablations",
                    choices=["no-notebook", "no-insolvency-warnings",
                             "no-history"],
                    help="disable capabilities to measure their score value")
    ap.add_argument("--label")
    ap.add_argument("--confirm-official", action="store_true",
                    help="required to actually run the official tier")
    ap.add_argument("--resume", metavar="RUN_ID",
                    help="continue a crashed LLM run from results/runs/")
    ap.add_argument("--cap-mode", default="suspend",
                    choices=["settle", "suspend"],
                    help="what a spend-cap trip does: 'suspend' (default) "
                         "pauses the run unscored — top up and --resume; "
                         "'settle' scores it early on the spot")
    ap.add_argument("--reopen", action="store_true",
                    help="with --resume: continue a run that was already "
                         "SETTLED early by a cap trip (needs a higher "
                         "--spend-cap-usd); the new result supersedes")
    args = ap.parse_args()
    if args.resume:
        resume_run(args.resume, spend_cap_usd=args.spend_cap_usd,
                   reopen=args.reopen, cap_mode=args.cap_mode)
        return
    if args.tier == "official" and not args.confirm_official:
        raise SystemExit("official tier is a large paid run; "
                         "re-run with --confirm-official")
    run_benchmark(agent=args.agent, seeds=args.seeds, years=args.years,
                  model=args.model, budget_tier=args.tier,
                  spend_cap_usd=args.spend_cap_usd, repeats=args.repeats,
                  club=args.club, run_label=args.label,
                  ablations=args.ablations, mode=args.mode,
                  notebook_cap=args.notebook_cap, world_size=args.world_size,
                  max_tokens=args.max_tokens,
                  soft_turn_limit=args.soft_turn_limit,
                  hard_turn_limit=args.hard_turn_limit,
                  memory_mode=args.memory_mode,
                  parallel_seeds=args.parallel_seeds, salted=args.salted,
                  cap_mode=args.cap_mode, tier_mix=args.tier_mix,
                  draft=args.draft, sealed=args.sealed, revival=args.revival)


if __name__ == "__main__":
    main()
