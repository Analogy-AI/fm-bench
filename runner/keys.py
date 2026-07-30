"""API key discovery + key pool with per-key rate limiting.

Key values must never be printed or logged. Pool semantics: seats/agents
round-robin over the pool; each distinct key gets its own RPM token bucket so
one shared key across 31 seats degrades gracefully instead of tripping 429s.
"""

from __future__ import annotations

import os
import pathlib
import re
import threading
import time

# repo-local, gitignored secrets file (VAR=value lines). Checked after real
# env vars but before the optional FALLBACK_CONFIG below.
REPO_ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"
# Optional extra secrets file: set FMBENCH_KEYS_FILE to a path of VAR="..."
# lines to source keys from. Empty by default (env vars / .env are preferred).
FALLBACK_CONFIG = os.environ.get("FMBENCH_KEYS_FILE", "")

# Per provider: env vars checked in order, then a regex applied to the
# fallback config file. Assignment-style regexes (VAR = "...") are preferred
# over bare token patterns so we never grab a key of the wrong provider.
_PROVIDERS = {
    "anthropic": {
        "env": ("ANTHROPIC_API_KEY",),
        "config_res": (re.compile(r"(sk-ant-[A-Za-z0-9_\-]{20,})"),),
    },
    "openai": {
        "env": ("OPENAI_API_KEY",),
        "config_res": (
            re.compile(r"OPENAI_API_KEY\s*=\s*[\"']([^\"']{20,})[\"']"),
            # bare token (e.g. an os.getenv(..., "sk-proj-...") default)
            re.compile(r"(sk-proj-[A-Za-z0-9_\-]{20,})"),),
    },
    "together": {   # OpenAI-compatible endpoint; DeepSeek etc. via base_url
        "env": ("TOGETHER_API_KEY",),
        "config_res": (re.compile(r"(tgp_[A-Za-z0-9_\-]{20,})"),),
    },
    "meta": {       # Meta Llama API, OpenAI-compatible; via base_url
        "env": ("META_API_KEY", "LLAMA_API_KEY"),
        "config_res": (re.compile(r"(LLM_[A-Za-z0-9_\-]{20,})"),),
    },
    "xai": {        # xAI Grok, OpenAI-compatible; via base_url
        "env": ("XAI_API_KEY", "GROK_API_KEY"),
        "config_res": (re.compile(r"(xai-[A-Za-z0-9_\-]{20,})"),),
    },
    "google": {
        "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "config_res": (
            re.compile(r"GEMINI_API_KEY\s*=\s*[\"']([^\"']{20,})[\"']"),
            re.compile(r"GOOGLE_API_KEY\s*=\s*[\"']([^\"']{20,})[\"']"),
            re.compile(r"(AIzaSy[A-Za-z0-9_\-]{30,})"),),
    },
}


def get_secret(name: str) -> str | None:
    """Generic secret lookup: env var, then the repo-local gitignored .env.
    Used for non-provider secrets (e.g. FMBENCH_WORLD_SALT)."""
    val = os.environ.get(name)
    if val:
        return val
    if REPO_ENV.exists():
        m = re.search(rf'^(?:export\s+)?{re.escape(name)}\s*=\s*["\']?([^"\'\r\n]+)',
                      REPO_ENV.read_text(encoding="utf-8"), re.M)
        if m:
            v = m.group(1).split("#")[0].strip()
            if v:
                return v
    return None


def provider_for_model(model: str) -> str:
    """Model-id prefix -> provider name (mirrors make_adapter routing)."""
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "google"
    if "/" in model:            # Together/HF-style namespaced id (deepseek-ai/…)
        return "together"
    if model.lower().startswith(("llama", "muse")):  # Meta Model API (Muse
        return "meta"                                # Spark) / legacy Llama ids
    if model.lower().startswith("grok"):    # xAI, e.g. grok-4.5
        return "xai"
    return "openai"


def find_api_key(provider: str = "anthropic") -> str | None:
    spec = _PROVIDERS.get(provider)
    if spec is None:
        return None
    for var in spec["env"]:
        key = os.environ.get(var)
        if key:
            return key
    # repo-local gitignored .env (VAR=value); never committed, never logged.
    # Accepts optional `export ` prefixes and strips inline `# comments`.
    if REPO_ENV.exists():
        env_text = REPO_ENV.read_text(encoding="utf-8")
        for var in spec["env"]:
            m = re.search(
                rf'^(?:export\s+)?{re.escape(var)}\s*=\s*["\']?([^"\'\r\n]+)',
                env_text, re.M)
            if m:
                val = m.group(1).split("#")[0].strip()
                if val:
                    return val
    try:
        with open(FALLBACK_CONFIG, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    for regex in spec["config_res"]:
        m = regex.search(text)
        if m:
            return m.group(1)
    return None


class KeyRateLimiter:
    """Token bucket: at most `rpm` request starts per rolling minute per key.

    acquire() blocks the calling thread until a slot frees. clock/sleep are
    injectable so tests never actually wait.
    """

    def __init__(self, rpm: int, clock=time.monotonic, sleep=time.sleep):
        self.rpm = max(1, int(rpm))
        self._clock = clock
        self._sleep = sleep
        self._starts: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._starts = [t for t in self._starts if now - t < 60.0]
                if len(self._starts) < self.rpm:
                    self._starts.append(now)
                    return
                wait = 60.0 - (now - self._starts[0]) + 0.01
            self._sleep(max(wait, 0.01))


class KeyPool:
    """Round-robin key assignment + shared per-key limiters.

    lease() returns (key, limiter); consecutive leases walk the pool so N
    seats spread across all keys. The same key always maps to the same
    limiter object, so concurrency control is per real key, not per seat.
    """

    def __init__(self, keys: list[str], rpm_per_key: int | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        if not keys:
            raise ValueError("KeyPool needs at least one key")
        self._keys = list(keys)
        self._i = 0
        self._lock = threading.Lock()
        self._limiters: dict[str, KeyRateLimiter | None] = {}
        for k in self._keys:
            self._limiters.setdefault(
                k, KeyRateLimiter(rpm_per_key, clock, sleep)
                if rpm_per_key else None)

    def __len__(self) -> int:
        return len(self._keys)

    def lease(self) -> tuple[str, KeyRateLimiter | None]:
        with self._lock:
            key = self._keys[self._i % len(self._keys)]
            self._i += 1
        return key, self._limiters[key]

    def distribution(self, n_seats: int) -> dict[int, int]:
        """seats-per-key-index for n_seats leases (introspection/tests)."""
        counts: dict[int, int] = {}
        for s in range(n_seats):
            counts[s % len(self._keys)] = counts.get(s % len(self._keys), 0) + 1
        return counts
