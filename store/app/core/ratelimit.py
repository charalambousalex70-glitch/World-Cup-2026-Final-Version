"""A small in-memory rate limiter.

Purpose: stop someone hammering checkout or guessing order numbers thousands of
times a minute.

HONEST LIMITATION: this counts requests inside ONE server process. Run two
copies of the app and each keeps its own tally, so the real limit doubles. For
a shop this size that is fine and costs nothing. If traffic ever justifies
running several instances, swap this for Redis — the `check()` signature is
designed to stay the same so nothing else has to change.
"""
import threading
import time
from collections import deque

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        """Raise 429 if `key` has been seen more than `limit` times recently."""
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Occasionally discard keys nobody has used lately, so a long-running
            # process does not accumulate one entry per visitor forever.
            if now - self._last_sweep > 300:
                for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    self._hits.pop(k, None)
                self._last_sweep = now

            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + window_seconds - now))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many attempts. Please wait a moment and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

    def reset(self) -> None:
        """Used by tests so one test's requests cannot fail the next."""
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
