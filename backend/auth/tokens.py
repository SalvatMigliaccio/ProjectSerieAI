"""
Session and email tokens: generation and comparison.

ONE PRINCIPLE, APPLIED EVERYWHERE: **what reaches the database is a hash; the
cleartext value exists only in the browser cookie or in the emailed link.**
Whoever reads the table cannot impersonate anyone.

WHY SHA-256 HERE AND argon2 FOR PASSWORDS. It looks inconsistent and is not.
argon2 is deliberately slow because a password carries little entropy and must
survive brute force. These tokens carry 256 bits from `secrets`: there is
nothing to guess, and a slow hash would run on every authenticated request
purely to slow the site down.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 32 bytes = 256 bits. Even at a trillion guesses per second the space outlives
# the system that stores it.
ENTROPY_BYTES = 32


def generate() -> tuple[str, str]:
    """
    A fresh token: `(cleartext, hash)`.

    The caller sends `cleartext` to the user and stores `hash`. There is no
    function that turns a hash back into cleartext, by design: if one were
    needed, the cleartext would have been kept somewhere.
    """
    clear = secrets.token_urlsafe(ENTROPY_BYTES)
    return clear, digest(clear)


def digest(token: str) -> str:
    """Hex SHA-256, 64 characters — the width of the `token_hash` column."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def matches(token: str, stored_hash: str) -> bool:
    """
    Constant-time comparison.

    `==` on strings returns at the first differing byte, so response time
    depends on how many leading characters are right. Hard to exploit over a
    noisy network, but the fix costs one line.
    """
    return hmac.compare_digest(digest(token), stored_hash)
