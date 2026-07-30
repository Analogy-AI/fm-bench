"""Pluggable seam for opponent-club (market_ai) behaviour.

Reviewer Q7: should the 31 opponent clubs' pricing/negotiation be scripted or
LLM-driven? The DEFAULT and the ONLY option valid for OFFICIAL SCORED runs is
`scripted` — an LLM-driven market maker would (a) break cross-model score
comparability (the opponents differ per evaluation, so scores stop being
comparable across the model under test) and (b) explode cost (31 extra LLM
agents per day-tick). LLM-driven opponents are only meaningful for the
`online` / exhibition mode, never for the single-model benchmark.

This module is the seam so that mode can be added later WITHOUT touching the
core tick: register a strategy, select it via params.market_ai.strategy. The
default path delegates to the existing scripted market_ai unchanged, so golden
hashes are byte-identical.
"""

from __future__ import annotations

from engine.sim import market_ai


def run_daily(world) -> None:
    """Dispatch the daily opponent-club decisions to the selected strategy."""
    name = getattr(world.params.market_ai, "strategy", "scripted")
    strategy = _REGISTRY.get(name)
    if strategy is None:
        raise ValueError(f"unknown market strategy {name!r}; "
                         f"choose one of {sorted(_REGISTRY)}")
    strategy(world)


def _scripted(world) -> None:
    """Default: the hand-tuned heuristic opponents (official-run behaviour)."""
    market_ai.daily(world)


def _llm_stub(world) -> None:
    raise NotImplementedError(
        "LLM-driven market maker is not implemented. It is intended only for "
        "the online/exhibition mode; it must never be used for official scored "
        "runs (it breaks cross-model comparability). Implement an adapter that "
        "drives each opponent club through the same obs/actions interface the "
        "player agent uses, then register it below.")


_REGISTRY = {
    "scripted": _scripted,
    "llm": _llm_stub,
}
