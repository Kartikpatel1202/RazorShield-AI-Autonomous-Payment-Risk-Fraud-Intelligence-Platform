"""In-process fixed-window rate limiting.

## The limitation, stated up front

Counters live in this process's memory. Run two Uvicorn workers and each keeps
its own tally, so a limit of N per minute becomes N x workers per minute. Run
two containers behind a load balancer and the same thing happens again.

This is a real weakness and it is *not* fixed by making the code cleverer; it is
fixed by moving the counter somewhere shared - Redis, or the rate limiting a
reverse proxy or API gateway already offers. The Phase 10 brief says not to
build a distributed limiter without a demonstrated need, and the deployment
under test is a single container with a single worker, where an in-process
counter is exactly correct. `docs/security.md` records this so it cannot be
mistaken for an oversight.

## Why fixed windows

A fixed window admits at most 2N requests across a window boundary, which a
sliding log avoids at the cost of retaining every request timestamp. For the
threat here - credential stuffing and accidental floods, not a precisely metered
API product - 2N is not a meaningful difference, and bounded memory is.

Memory *is* bounded: keys are evicted when their window closes, so the map never
holds more than the distinct clients seen in the last window.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from time import monotonic

#: How long a window lasts. Every configured limit is expressed per minute.
WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of one check."""

    allowed: bool
    limit: int
    remaining: int
    #: Seconds until the current window closes; the value for ``Retry-After``.
    retry_after: int


class FixedWindowRateLimiter:
    """Counts requests per (bucket, client) pair inside a fixed window.

    Thread-safe: FastAPI runs synchronous endpoints in a worker thread pool, so
    two requests can land here at once even in a single-worker deployment.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        # (bucket, client) -> (window_started_at, count)
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def check(self, bucket: str, client: str, limit: int) -> RateLimitDecision:
        """Record a request and say whether it may proceed.

        A ``limit`` of zero or less disables the bucket, which is how an
        operator turns one off without a code change.
        """
        if not self.enabled or limit <= 0:
            return RateLimitDecision(allowed=True, limit=limit, remaining=limit, retry_after=0)

        now = monotonic()
        key = (bucket, client)
        with self._lock:
            self._evict_expired(now)
            started, count = self._windows.get(key, (now, 0))
            if now - started >= WINDOW_SECONDS:
                started, count = now, 0

            count += 1
            self._windows[key] = (started, count)

        remaining = max(0, limit - count)
        retry_after = max(1, int(WINDOW_SECONDS - (now - started)) + 1)
        if count > limit:
            return RateLimitDecision(
                allowed=False, limit=limit, remaining=0, retry_after=retry_after
            )
        return RateLimitDecision(allowed=True, limit=limit, remaining=remaining, retry_after=0)

    def _evict_expired(self, now: float) -> None:
        """Drop windows that have closed. Caller holds the lock."""
        stale = [
            key for key, (started, _) in self._windows.items() if now - started >= WINDOW_SECONDS
        ]
        for key in stale:
            del self._windows[key]

    def reset(self) -> None:
        """Forget every window. Test support and process start only."""
        with self._lock:
            self._windows.clear()

    def tracked_clients(self) -> int:
        """How many (bucket, client) windows are currently held."""
        with self._lock:
            return len(self._windows)


#: The process-wide limiter. A module-level singleton rather than app state so
#: the CLI scripts and the tests reach the same object the middleware does.
limiter = FixedWindowRateLimiter()
