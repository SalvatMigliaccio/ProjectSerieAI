"""
Identifiers, configuration, passwords and tokens.

Every property checked here is one that, when broken, produces no error at
all: an id that is predictable, a hash that always verifies, a token you can
guess, a comparison that leaks timing. The system works fine in all four
cases. That is why they need explicit tests.
"""

from __future__ import annotations

import time

import pytest
from backend.auth import ids, password, tokens
from backend.auth.settings import EXAMPLE_KEY, Settings
from pydantic import SecretStr

# --- identifiers ------------------------------------------------------------


@pytest.fixture(params=[True, False], ids=["native", "fallback"])
def uuid7(request, monkeypatch):
    """Both implementations must honour the same contract."""
    if request.param and not ids._NATIVE:
        pytest.skip("this Python has no native uuid.uuid7")
    monkeypatch.setattr(ids, "_NATIVE", request.param)
    return ids.uuid7


def test_uuid7_matches_the_rfc(uuid7) -> None:
    u = uuid7()
    assert u.version == 7, f"version {u.version}, not 7"
    assert (u.int >> 62) & 0b11 == 0b10, "variant bits do not match RFC 9562"


def test_uuid7_does_not_collide(uuid7) -> None:
    assert len({uuid7() for _ in range(5000)}) == 5000


def test_uuid7_grows_over_time(uuid7) -> None:
    """
    The only reason to pick v7 over v4.

    If this stopped holding, inserts would scatter across the index again and
    the table would fragment — with no visible symptom until it is large.
    """
    first = uuid7()
    time.sleep(0.01)
    assert str(uuid7()) > str(first), "an id made later does not sort later"


# --- configuration ----------------------------------------------------------

def test_development_defaults_declare_themselves_insecure() -> None:
    """
    The dangerous case is that it simply works.

    A signing key left at the example value breaks nothing: the system starts,
    people sign in, and the tokens are forgeable by anyone who read the repo.
    """
    problems = Settings(_env_file=None).production_problems()
    assert any("SECRET_KEY" in p for p in problems)
    assert any("COOKIE_SECURE" in p for p in problems)


def test_a_sound_configuration_raises_no_false_alarm() -> None:
    good = Settings(
        _env_file=None,
        secret_key=SecretStr("x" * 64),
        cookie_secure=True,
        smtp_host="smtp.provider.example",
        smtp_security="starttls",
        public_url="https://ainaples.example",
    )
    assert good.production_problems() == []
    assert good.sender == "AI Naples <no-reply@ainaples.local>"


def test_plaintext_smtp_to_a_remote_host_is_a_problem() -> None:
    """Fine towards Mailpit on loopback; not fine towards a real provider."""
    local = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_security="none")
    assert not any("clear" in p for p in local.production_problems())

    remote = Settings(_env_file=None, smtp_host="smtp.example.com", smtp_security="none")
    assert any("clear" in p for p in remote.production_problems())


def test_smtp_security_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="smtp_security"):
        Settings(_env_file=None, smtp_security="maybe")


def test_the_example_key_is_still_the_one_in_the_file() -> None:
    """
    If `.env.example` changes and this constant does not, the check stops
    recognising the example key and protects nothing.
    """
    from pathlib import Path
    example = Path(__file__).resolve().parent.parent / ".env.example"
    assert EXAMPLE_KEY in example.read_text(encoding="utf-8"), (
        "EXAMPLE_KEY no longer appears in .env.example: the check would stop "
        "recognising the default value"
    )


# --- passwords --------------------------------------------------------------

def test_hash_and_verify() -> None:
    h = password.hash_password("a sufficiently long password")
    assert password.verify(h, "a sufficiently long password")
    assert not password.verify(h, "a sufficiently long passworD")


def test_two_hashes_of_the_same_password_differ() -> None:
    """The salt is per hash. Without it, users sharing a password can tell."""
    a = password.hash_password("the very same password here")
    b = password.hash_password("the very same password here")
    assert a != b
    assert password.verify(a, "the very same password here")
    assert password.verify(b, "the very same password here")


def test_the_hash_does_not_contain_the_password() -> None:
    assert "topsecret" not in password.hash_password("topsecret-do-not-write-this")


def test_it_is_argon2id() -> None:
    """Not bcrypt: bcrypt truncates at 72 bytes in silence."""
    assert password.hash_password("a sufficiently long password").startswith("$argon2id$")


def test_long_passwords_are_not_truncated() -> None:
    """
    bcrypt's specific defect, tested here because it never shows up by itself.

    Under bcrypt these two are the same password: they share their first 72
    bytes, so whoever knows the first can sign in with the second.
    """
    base = "x" * 72
    h = password.hash_password(base + "FIRST")
    assert not password.verify(h, base + "SECOND")


def test_too_short_is_rejected() -> None:
    with pytest.raises(password.WeakPassword, match="at least"):
        password.hash_password("short")


def test_absurdly_long_is_rejected() -> None:
    """Without a ceiling, a megabyte password is a way to burn the server."""
    with pytest.raises(password.WeakPassword, match="exceed"):
        password.hash_password("x" * (password.MAX_LENGTH + 1))


def test_a_corrupt_hash_does_not_raise() -> None:
    """It counts as a wrong password: one bad row must not take sign-in down."""
    assert not password.verify("not-a-hash", "anything")


def test_the_dummy_verify_costs_about_as_much_as_a_real_one() -> None:
    """
    Without it, response time reveals which addresses are registered.

    The margin is wide because timings wobble on a shared machine; what must
    hold is the same order of magnitude, not equality.
    """
    h = password.hash_password("a sufficiently long password")

    def elapsed(f) -> float:
        start = time.perf_counter()
        for _ in range(3):
            f()
        return time.perf_counter() - start

    real = elapsed(lambda: password.verify(h, "wrong but long enough"))
    dummy = elapsed(password.dummy_verify)
    assert 0.25 < dummy / real < 4.0, f"ratio {dummy / real:.2f}: timings too far apart"


# --- tokens -----------------------------------------------------------------

def test_tokens_are_unique() -> None:
    assert len({tokens.generate()[0] for _ in range(2000)}) == 2000


def test_tokens_carry_enough_entropy() -> None:
    clear, _ = tokens.generate()
    # base64url: roughly 4 characters per 3 bytes.
    assert len(clear) >= tokens.ENTROPY_BYTES * 4 // 3


def test_the_hash_fits_the_column() -> None:
    """`token_hash` is String(64). A longer hash would be truncated by the db."""
    _, h = tokens.generate()
    assert len(h) == 64


def test_matches_only_the_right_token() -> None:
    clear, h = tokens.generate()
    assert tokens.matches(clear, h)
    other, _ = tokens.generate()
    assert not tokens.matches(other, h)


def test_the_cleartext_is_not_recoverable_from_the_hash() -> None:
    """Trivial, but it is the invariant the whole scheme rests on."""
    clear, h = tokens.generate()
    assert clear not in h
