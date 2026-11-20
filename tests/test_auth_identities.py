"""
ADR 0008, step 1: accounts without a password, external identities, and the
second-factor tables.

The flows (Google, emailed codes) arrive in later steps. What is pinned here is
what the schema and the service must already guarantee, because each property
fails silently when broken: a passwordless account that answers differently
from a wrong password is an address checker; a 2FA state that is half on
cannot be read; a second account bound to the same Google `sub` is someone
else's door.
"""

from __future__ import annotations

import datetime as dt

import pytest
from backend.auth import retention, service
from backend.auth.models import (
    AuthEvent,
    MfaChallenge,
    User,
    UserIdentity,
    now,
)
from backend.auth.settings import Settings
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.requires_db


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, max_attempts=3, lockout_minutes=15)


def _passwordless(db, email: str = "solo-google@example.com") -> User:
    """An account as the Google sign-in will create it: active, verified, no password."""
    user = User(email=email, password_hash=None, status="active",
                email_verified_at=now())
    db.add(user)
    db.flush()
    db.add(UserIdentity(user_id=user.id, provider="google",
                        subject=f"sub-{email}", email=email))
    db.flush()
    return user


def _challenge(db, user: User, tag: str, expires: dt.datetime) -> MfaChallenge:
    row = MfaChallenge(user_id=user.id, purpose="sign_in", via="password",
                       challenge_hash=tag.ljust(64, "0"), code_hash="c" * 64,
                       expires_at=expires)
    db.add(row)
    db.flush()
    return row


# --- accounts without a password -------------------------------------------------

def test_a_passwordless_account_answers_like_a_wrong_password(db, cfg) -> None:
    """Same message as any refusal: no hint that the address exists or how to reach it."""
    _passwordless(db)
    with pytest.raises(service.AccessDenied) as exc:
        service.sign_in(db, "solo-google@example.com", "anything at all", cfg=cfg)
    assert str(exc.value) == "invalid credentials"


def test_a_passwordless_account_is_never_locked_by_password_guesses(db, cfg) -> None:
    """Locking it would shut the owner out of the reset that gives them a password."""
    user = _passwordless(db)
    for _ in range(cfg.max_attempts + 2):
        with pytest.raises(service.AccessDenied):
            service.sign_in(db, user.email, "a guess", cfg=cfg)
    assert user.failed_attempts == 0
    assert user.locked_until is None
    causes = {e.details["cause"] for e in db.scalars(select(AuthEvent).where(
        AuthEvent.user_id == user.id, AuthEvent.kind == "sign_in"))}
    assert causes == {"no_password"}, "the refusal must be audited with its real cause"


def test_a_first_password_is_not_set_from_a_session_alone(db) -> None:
    user = _passwordless(db)
    with pytest.raises(service.AccessDenied):
        service.change_password(db, user, "", "a sufficiently long password")
    assert user.password_hash is None


def test_a_reset_gives_a_passwordless_account_its_first_password(db, cfg) -> None:
    from backend.auth.mail import InMemorySender
    from tests.test_auth_service import _token
    user = _passwordless(db, "aggiunge-password@example.com")
    service.request_reset(db, user.email, cfg=cfg, sender=InMemorySender())
    service.reset_password(db, _token(db, user, "reset"), "a sufficiently long password")

    service.sign_in(db, user.email, "a sufficiently long password", cfg=cfg)


def test_a_passwordless_account_is_not_deleted_on_a_session_alone(db) -> None:
    user = _passwordless(db)
    with pytest.raises(service.AccessDenied):
        service.delete_account(db, user, "")
    assert db.get(User, user.id) is not None


# --- second-factor state ---------------------------------------------------------

@pytest.mark.parametrize(("method", "enabled"), [("email", None), (None, "now")])
def test_mfa_is_either_fully_on_or_fully_off(db, method, enabled) -> None:
    user = User(email="mezzo@example.com", password_hash="x", status="active",
                mfa_method=method, mfa_enabled_at=now() if enabled else None)
    db.add(user)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.flush()


def test_totp_cannot_be_on_without_a_secret(db) -> None:
    user = User(email="totp@example.com", password_hash="x", status="active",
                mfa_method="totp", mfa_enabled_at=now())
    db.add(user)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.flush()


def test_email_mfa_is_a_valid_state(db) -> None:
    user = User(email="mfa-ok@example.com", password_hash="x", status="active",
                mfa_method="email", mfa_enabled_at=now())
    db.add(user)
    db.flush()
    assert user.mfa_enabled()


# --- external identities ---------------------------------------------------------

def test_one_google_subject_belongs_to_one_account(db) -> None:
    _passwordless(db, "primo@example.com")
    other = User(email="secondo@example.com", password_hash="x", status="active")
    db.add(other)
    db.flush()
    db.add(UserIdentity(user_id=other.id, provider="google",
                        subject="sub-primo@example.com"))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.flush()


def test_one_account_links_one_google_account(db) -> None:
    user = _passwordless(db)
    db.add(UserIdentity(user_id=user.id, provider="google", subject="another-sub"))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.flush()


# --- challenges ------------------------------------------------------------------

def test_two_users_may_hold_the_same_code_at_once(db) -> None:
    """The reason challenges are not rows of `email_tokens`, where the hash is unique."""
    a = _passwordless(db, "a@example.com")
    b = _passwordless(db, "b@example.com")
    _challenge(db, a, "a", now() + dt.timedelta(minutes=10))
    _challenge(db, b, "b", now() + dt.timedelta(minutes=10))   # same code_hash


def test_a_challenge_dies_after_the_attempts_run_out(db) -> None:
    row = _challenge(db, _passwordless(db), "x", now() + dt.timedelta(minutes=10))
    assert row.is_spendable(now(), max_attempts=5)
    row.attempts = 5
    assert not row.is_spendable(now(), max_attempts=5)


def test_expired_challenges_are_purged_with_the_email_token_period(db) -> None:
    cfg = Settings(_env_file=None)
    at = now()
    day = dt.timedelta(days=1)
    user = _passwordless(db)
    old = _challenge(db, user, "o", at - (cfg.token_retention_days + 1) * day)
    recent = _challenge(db, user, "r", at - 2 * day)

    retention.purge(db, cfg=cfg, at=at)
    db.expire_all()
    assert db.get(MfaChallenge, old.id) is None
    assert db.get(MfaChallenge, recent.id) is not None


def test_deleting_a_user_takes_identities_and_challenges_along(db) -> None:
    from sqlalchemy import delete
    user = _passwordless(db)
    _challenge(db, user, "d", now() + dt.timedelta(minutes=10))
    uid = user.id
    db.execute(delete(User).where(User.id == uid))
    db.expunge_all()
    for model in (UserIdentity, MfaChallenge):
        assert db.scalars(select(model).where(model.user_id == uid)).all() == [], \
            f"{model.__tablename__} kept rows of a deleted account"
