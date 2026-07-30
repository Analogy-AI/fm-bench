"""Load params.yaml once; expose read-only nested access."""

from __future__ import annotations

import copy
import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

CENTS_PER_M = 100_000_000  # money is integer cents; 1 M = 1 million credits


def M(millions: float) -> int:
    """Millions of credits -> integer cents."""
    return int(round(millions * CENTS_PER_M))


def as_m(cents: int) -> float:
    return cents / CENTS_PER_M


class Params(dict):
    """Dict with attribute access, recursively."""

    def __getattr__(self, item):
        try:
            v = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        if isinstance(v, dict) and not isinstance(v, Params):
            v = Params(v)
            self[item] = v
        return v


_cache: Params | None = None


def load_params(path: str | None = None) -> Params:
    global _cache
    if path is None and _cache is not None:
        return _cache
    p = pathlib.Path(path) if path else _ROOT / "params.yaml"
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    params = Params(data)
    if path is None:
        _cache = params
    return params


def _deep_merge(base: dict, over: dict) -> None:
    """Recursively merge `over` into `base` in place."""
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def apply_overrides(params: Params, overrides: dict | None) -> Params:
    """Return a params copy with `overrides` deep-merged in.

    Deep-copies first so the module-level `_cache` is never mutated — a
    single scalable injection point for run-config (world size, opponent
    advantages, notebook cap, ...). With no overrides the ORIGINAL cached
    object is returned unchanged, so the default path stays byte-identical.
    """
    if not overrides:
        return params
    clone = Params(copy.deepcopy(dict(params)))
    _deep_merge(clone, overrides)
    return clone
