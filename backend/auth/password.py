"""
Password hashing with argon2id.

WHY argon2id AND NOT bcrypt. bcrypt silently truncates at 72 bytes: two long
passwords sharing their first 72 characters are the same password, and nothing
says so. argon2id is also memory-hard — an attacker with GPUs or ASICs does
not gain the orders of magnitude they gain against bcrypt, because the
bottleneck is RAM per attempt rather than cycles.

WHY NOT SALTED SHA-256. A general-purpose hash is fast by design, which is
exactly the wrong property here: what is needed is calibrated slowness.

REHASHING IS NOT A DETAIL. Parameters get raised over time. Without
`needs_rehash`, whoever signed up two years ago keeps the parameters of two
years ago forever, and raising them protects only new accounts.
"""

from __future__ import annotations

import contextlib
import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

log = logging.getLogger("auth.password")

# OWASP Password Storage Cheat Sheet, argon2id profile: 19 MiB, 2 iterations,
# parallelism 1. That is ~50-100 ms per verification on ordinary hardware:
# enough to make brute force expensive, little enough that sign-in does not
# become a way to bring the server down with a handful of requests.
_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# A minimum length, not a rulebook about uppercase and digits: composition
# rules push people towards "Password1!" and lower real entropy instead of
# raising it. NIST SP 800-63B advises against them explicitly.
MIN_LENGTH = 12

# argon2 does not truncate, but without a ceiling a 10 MB password is a way to
# burn server CPU and RAM with a single request.
MAX_LENGTH = 1024

# The hash of a password nobody will ever use. Feeds `dummy_verify`.
_DUMMY = _HASHER.hash("nonexistent password used for the empty comparison")


class WeakPassword(ValueError):
    """The password does not meet the minimum requirements."""


def check_strength(password: str) -> None:
    """Raise `WeakPassword` with a message that says what to do."""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"the password must be at least {MIN_LENGTH} characters long")
    if len(password) > MAX_LENGTH:
        raise WeakPassword(f"the password cannot exceed {MAX_LENGTH} characters")


def hash_password(password: str) -> str:
    check_strength(password)
    return _HASHER.hash(password)


def verify(stored_hash: str, password: str) -> bool:
    """
    `True` if the password matches. Does not raise on wrong passwords.

    A corrupt or unknown-format hash counts as a wrong password, but is logged:
    that is a data defect rather than a failed attempt, and conflating the two
    hides database corruption.
    """
    try:
        return _HASHER.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        log.warning("unreadable password hash: treated as invalid")
        return False


def dummy_verify() -> None:
    """
    Burn the same time as a real verification, against a fake hash.

    WHY IT IS NEEDED. If sign-in answers instantly when the address does not
    exist and after 80 ms when it does, response time tells a stranger which
    addresses are registered. That is account enumeration, and it needs no
    explicit error message — only a stopwatch.
    """
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _HASHER.verify(_DUMMY, "anything at all")


def needs_rehash(stored_hash: str) -> bool:
    """`True` if the hash uses outdated parameters and should be redone at next sign-in."""
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
