"""
Rate limits: per address on the whole API, tighter on the costly routes.

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
  - It keys on the address `client_ip` reports. Behind a reverse proxy that is
    the proxy itself unless the deployment declares which proxy to believe:
    `compose.prod.yaml` sets `FORWARDED_ALLOW_IPS` to the Caddy container for
    exactly this reason. Get that wrong and this stops being a per-address
    limit and becomes a global one — every user sharing one bucket, which locks
    out everybody at the twentieth attempt from anybody.

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

    # How many calls between two automatic prunes. `prune` existed and nothing
    # called it, so the dictionary grew by one entry per address ever seen —
    # and an attacker rotating addresses (an IPv6 /64 is 2^64 of them) decides
    # how fast. Pruning from `allow` itself needs no scheduler to forget.
    PRUNE_EVERY = 1000

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        # A single lock, not one per key: contention is irrelevant at this
        # scale, and per-key locks would need their own reaping.
        self._lock = threading.Lock()
        self._calls = 0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._calls += 1
            if self._calls % self.PRUNE_EVERY == 0:
                self._prune_locked(now)
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """
        Seconds until `key` gets one more event — for the `Retry-After` header.

        A 429 without it leaves a well-behaved client guessing, and a guessing
        client retries at once, which is the traffic the limit exists to stop.
        """
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q or len(q) < self.limit:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

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
        with self._lock:
            return self._prune_locked(time.monotonic())

    def _prune_locked(self, now: float) -> int:
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

# THE WHOLE API, per address. Generous on purpose: one dashboard load is about
# ten requests and a refresh every ten minutes, so 300 a minute is thirty full
# reloads — far above a person, far below a script hammering the routes that
# recompute a score matrix on every call. A team behind one office NAT shares
# this bucket; that is the price of keying on addresses, and the number is set
# so a dozen people reloading at once do not reach it.
api_limiter = RateLimiter(limit=300, window_seconds=60)

# Refresh: one every ten minutes per tab is the normal rate; 60 in five
# minutes is a client stuck in a loop or someone testing stolen cookies.
refresh_limiter = RateLimiter(limit=60, window_seconds=300)

# The links from emails (verify, reset). The tokens are 256 bits and cannot be
# guessed, so this is not about guessing: reset hashes a password with argon2,
# which is deliberately expensive, and an unthrottled route that burns CPU on
# demand is a denial of service with a nicer name.
token_limiter = RateLimiter(limit=20, window_seconds=900)

# Changing the password, keyed by ACCOUNT rather than address: whoever holds a
# stolen access token could otherwise try current passwords at full speed —
# the account lockout counts sign-ins, not this route. Also argon2, as above.
password_change_limiter = RateLimiter(limit=8, window_seconds=900)

# Revoking sessions, per account: cheap, but a loop on it fills the audit log.
session_admin_limiter = RateLimiter(limit=30, window_seconds=300)

ALL_LIMITERS = (sign_in_limiter, signup_limiter, reset_limiter, api_limiter,
                refresh_limiter, token_limiter, password_change_limiter,
                session_admin_limiter)
