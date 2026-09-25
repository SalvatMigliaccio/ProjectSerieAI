"""
A per-address rate limit for the authentication endpoints.

WHAT IT IS FOR, given that accounts already lock out. The lockout protects one
account against many guesses. This protects everything else: signup floods,
reset-email floods aimed at someone else's mailbox, and password spraying —
one attempt each against thousands of accounts, which never trips a per-account
counter because no single account sees a second try.

DECLARED CEILING, because a limit that is trusted further than it reaches is
worse than none: this counter lives in **this process's memory**.

  - Two workers mean two independent counters, so the effective limit is the
    configured one times the number of workers.
  - A restart forgets everything.
  - It keys on the address `client_ip` reports, which behind a proxy is the
    proxy unless phase 3 configures forwarded headers.

It is the right size for today — one process, a handful of users — and the
wrong size the moment the API is scaled out. At that point this moves to Redis
and the interface stays. Until then, **the account lockout is the real defence
and this is a speed bump.**
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding window: at most `limit` events per `window_seconds` per key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # A single lock, not one per key: contention is irrelevant at this
        # scale, and per-key locks would need their own reaping.
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        """Clear one key or all of them. Used by tests."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def prune(self) -> int:
        """
        Drop keys with no recent events.

        Without this the dictionary grows once per distinct address seen, which
        over months is a slow leak in a long-running process.
        """
        now = time.monotonic()
        with self._lock:
            stale = [k for k, q in self._hits.items()
                     if not q or now - q[-1] > self.window]
            for k in stale:
                del self._hits[k]
            return len(stale)


# Sign-in is the noisiest legitimate endpoint (typos, password managers
# retrying), so it gets the loosest limit. Sending email is the most expensive
# to abuse and gets the tightest: each one lands in a real mailbox.
sign_in_limiter = RateLimiter(limit=20, window_seconds=300)
signup_limiter = RateLimiter(limit=5, window_seconds=3600)
reset_limiter = RateLimiter(limit=5, window_seconds=3600)
