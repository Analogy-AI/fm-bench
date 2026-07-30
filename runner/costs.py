"""Token accounting and spend-cap tracking for LLM runs.

CostTracker never raises on its own: the runner checks over_cap() at safe
points (after a response's tool calls are executed) so a cap hit settles the
run gracefully instead of dropping work mid-flight.
"""

from __future__ import annotations

# $ per million tokens: (input, output, cache_write, cache_read)
# Anthropic rows are authoritative (official pricing, 2026-07). Together rows
# were sourced from the provider catalog 2026-07-09; OpenAI/xAI/Meta rows from
# launch announcements 2026-07. Rows in ESTIMATED_PRICE_KEYS carry estimated
# components (usually the cache-read rate) and are flagged price_estimated in
# cost summaries — pin them before publishing cross-provider cost comparisons.
PRICES = {
    # Anthropic (cache write 1.25x input, cache read 0.1x input)
    "claude-fable-5": (10.00, 50.00, 12.50, 1.00),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-5": (3.00, 15.00, 3.75, 0.30),
    # OpenAI (cached input ~0.1x; no separate write charge -> write = input)
    "gemini-2.5-flash": (0.30, 2.50, 0.30, 0.075),
    "gemini-2.5-pro": (1.25, 10.00, 1.25, 0.31),
    "gemini-3-flash-preview": (0.50, 3.00, 0.50, 0.125),
    "gemini-3.5-flash": (1.00, 6.00, 1.00, 0.25),
    "gpt-4o": (2.50, 10.00, 2.50, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.15, 0.075),
    "gpt-5": (1.25, 10.00, 1.25, 0.125),
    "gpt-5-mini": (0.25, 2.00, 0.25, 0.025),
    "gpt-5-nano": (0.05, 0.40, 0.05, 0.005),
    "gpt-5.6-luna": (1.00, 6.00, 1.00, 0.10),
    "gpt-5.6-sol": (5.00, 30.00, 5.00, 0.50),
    "gpt-5.6-terra": (2.50, 15.00, 2.50, 0.25),
    "o4-mini": (1.10, 4.40, 1.10, 0.275),
    # Together serverless (catalog 2026-07-09; V4-Pro cached input $0.20)
    "Qwen/Qwen3.7-Max": (1.00, 6.00, 1.00, 0.25),
    "deepseek-ai/DeepSeek-V4-Pro": (2.10, 4.40, 2.10, 0.20),
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": (0.88, 0.88, 0.88, 0.88),
    # Together catalog 2026-07-14 (queried live): MiniMax-M3 $0.3/$1.2, 512K ctx
    "MiniMaxAI/MiniMax-M3": (0.30, 1.20, 0.30, 0.30),
    "moonshotai/Kimi-K2.6": (1.20, 4.50, 1.20, 0.30),
    "zai-org/GLM-5.2": (1.40, 4.40, 1.40, 0.35),
    # xAI / Meta (launch pricing 2026-07)
    "Muse-Spark-1.1": (1.25, 4.25, 1.25, 0.31),
    "muse-spark-1.1": (1.25, 4.25, 1.25, 0.31),  # server's canonical (lower) id
    "grok-4.5": (2.00, 6.00, 2.00, 0.50),
}
# every non-Anthropic row has at least one estimated component
ESTIMATED_PRICE_KEYS = frozenset(
    k for k in PRICES if not k.startswith("claude-"))
FALLBACK_PRICE_KEY = "claude-sonnet-4-6"  # conservative


class BudgetExceeded(RuntimeError):
    pass


class RunSuspended(RuntimeError):
    """Cap tripped in suspend mode: run paused UNSCORED, archive resumable.

    Carries what the caller needs to print a top-up hint. Distinct from
    BudgetExceeded (which settles) so harnesses can't confuse the two."""

    def __init__(self, msg: str, run_id: str | None = None,
                 spent_usd: float = 0.0):
        super().__init__(msg)
        self.run_id = run_id
        self.spent_usd = spent_usd


class SharedSpendPool:
    """Thread-safe cumulative spend across parallel runs of one invocation.

    With --parallel-seeds, several seeds burn budget simultaneously; the
    invocation-wide cap must see the SUM as it accrues, not per-seed totals
    reconciled after the fact. Each CostTracker adds its per-call cost here
    the moment the call lands, so over_cap() is accurate across threads."""

    def __init__(self, prior_usd: float = 0.0):
        import threading
        self._usd = float(prior_usd)
        self._lock = threading.Lock()

    def add(self, usd: float) -> None:
        with self._lock:
            self._usd += usd

    def total(self) -> float:
        with self._lock:
            return self._usd


def _price_row(model: str) -> tuple[tuple, bool, bool]:
    """(prices, estimated, fallback). fallback=True means the model has NO
    price entry at all — billing runs at conservative fallback rates and the
    spend cap loses accuracy in BOTH directions (a cheap model aborts early, an
    expensive one overspends). Callers should warn loudly on fallback."""
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES[prefix], prefix in ESTIMATED_PRICE_KEYS, False
    return PRICES[FALLBACK_PRICE_KEY], True, True


class CostTracker:
    def __init__(self, model: str, spend_cap_usd: float | None,
                 prior_usd: float = 0.0, pool: SharedSpendPool | None = None):
        self.model = model
        self.spend_cap_usd = spend_cap_usd
        self.pool = pool  # shared invocation-wide spend (parallel seeds)
        # spend already committed by earlier seeds/repeats of the same
        # invocation: the cap is cumulative across the whole invocation,
        # while usd()/summary() stay per-run for per-seed reporting
        self.prior_usd = prior_usd
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_write_tokens = 0
        self.cache_read_tokens = 0
        self._prices, self.price_estimated, self.price_fallback = \
            _price_row(model)

    def add(self, usage) -> None:
        """usage: runner.providers.Usage (or anything with the same fields)."""
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        if self.pool is not None:
            i, o, cw, cr = self._prices
            self.pool.add((usage.input_tokens * i + usage.output_tokens * o
                           + usage.cache_write_tokens * cw
                           + usage.cache_read_tokens * cr) / 1e6)

    def usd(self) -> float:
        i, o, cw, cr = self._prices
        return (self.input_tokens * i + self.output_tokens * o
                + self.cache_write_tokens * cw + self.cache_read_tokens * cr) / 1e6

    def total_usd(self) -> float:
        """Cumulative invocation spend: earlier seeds + this run."""
        return self.prior_usd + self.usd()

    def over_cap(self) -> bool:
        if self.spend_cap_usd is None:
            return False
        spent = (self.pool.total() if self.pool is not None
                 else self.total_usd())
        return spent > self.spend_cap_usd

    def cache_hit_rate(self) -> float:
        denom = (self.input_tokens + self.cache_read_tokens
                 + self.cache_write_tokens)
        return self.cache_read_tokens / denom if denom else 0.0

    def restore(self, summary: dict) -> None:
        """Resume support: carry a prior run's spend into this tracker."""
        self.calls = summary.get("api_calls", 0)
        self.input_tokens = summary.get("input_tokens", 0)
        self.output_tokens = summary.get("output_tokens", 0)
        self.cache_write_tokens = summary.get("cache_write_tokens", 0)
        self.cache_read_tokens = summary.get("cache_read_tokens", 0)

    def summary(self) -> dict:
        return {
            "api_calls": self.calls,
            "cache_hit_rate": round(self.cache_hit_rate(), 4),
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.usd(), 4),
            "input_tokens": self.input_tokens,
            "model": self.model,
            "output_tokens": self.output_tokens,
            "price_estimated": self.price_estimated,
        }
