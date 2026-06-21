"""Public-endpoint hardening seams (the ingest endpoint is unauthenticated).

Phase 1 ships the seams + simple in-memory defaults: an origin allow-list check, a
payload-size guard (enforced in the router), and a fixed-window rate limiter.
Production hardening (Cloudflare Turnstile, a Redis-backed limiter) plugs in behind the
same interfaces — see issue #5.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from typing import Protocol


def origin_allowed(origin: str | None, allowed: Iterable[str] | None) -> bool:
    """True if origin is permitted; ``allowed=None`` allows any (the dev default)."""
    if allowed is None:
        return True
    return origin is not None and origin in set(allowed)


class RateLimiter(Protocol):
    """Decide whether a request keyed by ``key`` (e.g. client IP) is allowed now."""

    def allow(self, key: str) -> bool: ...


class InMemoryRateLimiter:
    """Fixed-window, in-memory limiter. NOT multi-process safe — see issue #5.

    Suitable for a single-worker dev/standalone deployment; swap for a Redis-backed
    limiter in production.
    """

    def __init__(
        self,
        *,
        max_per_window: int = 30,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self.max_per_window:
            return False
        hits.append(now)
        return True
