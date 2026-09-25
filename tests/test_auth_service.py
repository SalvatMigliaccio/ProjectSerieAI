"""
The authentication flows, against the real database.

Everything here runs inside a transaction that is rolled back, so the tests
share one migrated database without seeing each other's rows.

WHAT IS BEING PROTECTED. Most of these properties are invisible when broken:
an endpoint that reveals which addresses are registered still signs people in,
a reset that leaves old sessions alive still changes the password, a lockout
that never triggers still rejects wrong passwords. They need naming.
"""

from __future__ import annotations

import datetime as dt

import pytest
from backend.auth import service
from backend.auth.mail import InMemorySender
from backend.auth.models import EmailToken, User, now
from backend.auth.settings import Settings

pytestmark = pytest.mark.requires_db

GOOD = "a sufficiently long password"


@pytest.fixture
def cfg() -> Settings:
    """Deliberately fast lockout, so the test does not need eight rounds."""
    return Settings(_env_file=None, max_attempts=3, lockout_minutes=15)


@pytest.fixture
def post() -> InMemorySender:
    return InMemorySender()


def _token(db, user: User, kind: str) -> str:
    """
    The cleartext token never leaves `service`, so a test cannot read it.

    Rather than weaken the production code to make it observable, the test
    rewrites the stored hash to one it knows. That keeps the property under
    test — cleartext is never persisted — intact.
    """
    from backend.auth import tokens
    # The most recent unused one: a repeat signup on a pending account issues
    # a second verification link on purpose, so there can legitimately be more
    # than one outstanding.
    row = (db.query(EmailToken)
             .filter_by(user_id=user.id, kind=kind, used_at=None)
             .order_by(EmailToken.created_at.desc(), EmailToken.id.desc())
             .first())
    assert row is not None, f"no outstanding {kind} token for {user.email}"
    clear, hashed = tokens.generate()
    row.token_hash = hashed
    db.flush()
    return clear


# --- signup -----------------------------------------------------------------

def test_signup_creates_a_pending_customer(db, cfg, post) -> None:
    service.sign_up(db, "nuovo@example.com", GOOD, cfg=cfg, sender=post)
    user = service.by_email(db, "nuovo@example.com")

    assert user is not None
    assert user.status == "pending", "a fresh account must not be active"
    assert {r.name for r in user.roles} == {"customer"}
    assert post.last_to("nuovo@example.com") is not None, "no verification email"


def test_signup_is_case_insensitive_on_the_address(db, cfg, post) -> None:
    """Without CITEXT these are two accounts and the user is locked out."""
    service.sign_up(db, "Mario@Example.com", GOOD, cfg=cfg, sender=post)
    assert service.by_email(db, "mario@example.com") is not None


def test_signing_up_twice_does_not_say_the_address_is_taken(db, cfg, post) -> None:
    """
    The endpoint must not become an address checker.

    It neither raises nor creates a second row; the owner of the address gets
    an email, the prober gets the same silence either way.
    """
    service.sign_up(db, "due@example.com", GOOD, cfg=cfg, sender=post)
    service.sign_up(db, "due@example.com", "another long password", cfg=cfg, sender=post)

    assert db.query(User).filter_by(email="due@example.com").count() == 1
    # The original password still works, so the second signup did not overwrite it.
    user = service.by_email(db, "due@example.com")
    service.verify_email(db, _token(db, user, "verify"))
    service.sign_in(db, "due@example.com", GOOD, cfg=cfg)


def test_a_weak_password_is_refused(db, cfg, post) -> None:
    from backend.auth.password import WeakPassword
    with pytest.raises(WeakPassword):
        service.sign_up(db, "debole@example.com", "short", cfg=cfg, sender=post)
    assert service.by_email(db, "debole@example.com") is None


# --- verification -----------------------------------------------------------

def test_verification_activates_the_account(db, cfg, post) -> None:
    service.sign_up(db, "verifica@example.com", GOOD, cfg=cfg, sender=post)
    user = service.by_email(db, "verifica@example.com")

    service.verify_email(db, _token(db, user, "verify"))

    assert user.status == "active"
    assert user.email_verified_at is not None


def test_a_verification_token_works_once(db, cfg, post) -> None:
    service.sign_up(db, "unavolta@example.com", GOOD, cfg=cfg, sender=post)
    user = service.by_email(db, "unavolta@example.com")
    token = _token(db, user, "verify")

    service.verify_email(db, token)
    with pytest.raises(service.InvalidToken):
        service.verify_email(db, token)


def test_an_expired_token_is_refused(db, cfg, post) -> None:
    service.sign_up(db, "scaduto@example.com", GOOD, cfg=cfg, sender=post)
    user = service.by_email(db, "scaduto@example.com")
    token = _token(db, user, "verify")
    db.query(EmailToken).filter_by(user_id=user.id).update(
        {"expires_at": now() - dt.timedelta(seconds=1)})

    with pytest.raises(service.InvalidToken):
        service.verify_email(db, token)


def test_an_invented_token_is_refused(db) -> None:
    with pytest.raises(service.InvalidToken):
        service.verify_email(db, "completely-made-up")


# --- sign in ----------------------------------------------------------------

def _active_user(db, cfg, post, email: str) -> User:
    service.sign_up(db, email, GOOD, cfg=cfg, sender=post)
    user = service.by_email(db, email)
    service.verify_email(db, _token(db, user, "verify"))
    return user


def test_sign_in_returns_a_session(db, cfg, post) -> None:
    _active_user(db, cfg, post, "entra@example.com")
    token, row = service.sign_in(db, "entra@example.com", GOOD, cfg=cfg)

    assert service.session_from_token(db, token, cfg) is not None
    assert row.token_hash != token, "the cleartext token must not be stored"


def test_an_unverified_account_cannot_sign_in(db, cfg, post) -> None:
    service.sign_up(db, "nonverificato@example.com", GOOD, cfg=cfg, sender=post)
    with pytest.raises(service.AccessDenied, match="confirm"):
        service.sign_in(db, "nonverificato@example.com", GOOD, cfg=cfg)


def test_an_unknown_address_and_a_wrong_password_look_the_same(db, cfg, post) -> None:
    """Different messages would make the endpoint an address checker."""
    _active_user(db, cfg, post, "esiste@example.com")

    with pytest.raises(service.AccessDenied) as unknown:
        service.sign_in(db, "non-esiste@example.com", GOOD, cfg=cfg)
    with pytest.raises(service.AccessDenied) as wrong:
        service.sign_in(db, "esiste@example.com", "wrong but long enough", cfg=cfg)

    assert str(unknown.value) == str(wrong.value) == "invalid credentials"


def test_too_many_failures_lock_the_account(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "blocco@example.com")

    for _ in range(cfg.max_attempts):
        with pytest.raises(service.AccessDenied):
            service.sign_in(db, "blocco@example.com", "wrong but long enough", cfg=cfg)

    assert user.locked_until is not None
    # Now even the right password is refused, which is the point.
    with pytest.raises(service.AccessDenied, match="too many"):
        service.sign_in(db, "blocco@example.com", GOOD, cfg=cfg)


def test_a_successful_sign_in_clears_the_counter(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "azzera@example.com")
    with pytest.raises(service.AccessDenied):
        service.sign_in(db, "azzera@example.com", "wrong but long enough", cfg=cfg)
    assert user.failed_attempts == 1

    service.sign_in(db, "azzera@example.com", GOOD, cfg=cfg)
    assert user.failed_attempts == 0


# --- sessions ---------------------------------------------------------------

def test_signing_out_revokes_server_side(db, cfg, post) -> None:
    """Clearing the cookie is not enough: whoever copied it would still be in."""
    _active_user(db, cfg, post, "esci@example.com")
    token, _ = service.sign_in(db, "esci@example.com", GOOD, cfg=cfg)

    service.sign_out(db, token)
    assert service.session_from_token(db, token, cfg) is None


def test_an_idle_session_expires(db, cfg, post) -> None:
    _active_user(db, cfg, post, "inattivo@example.com")
    token, row = service.sign_in(db, "inattivo@example.com", GOOD, cfg=cfg)

    row.last_seen_at = now() - dt.timedelta(minutes=cfg.session_idle_minutes + 1)
    db.flush()
    assert service.session_from_token(db, token, cfg) is None


def test_a_session_dies_at_its_absolute_deadline(db, cfg, post) -> None:
    """Even while in active use: it caps the window of a stolen cookie."""
    _active_user(db, cfg, post, "assoluto@example.com")
    token, row = service.sign_in(db, "assoluto@example.com", GOOD, cfg=cfg)

    row.expires_at = now() - dt.timedelta(seconds=1)
    row.last_seen_at = now()
    db.flush()
    assert service.session_from_token(db, token, cfg) is None


def test_suspending_a_user_drops_their_sessions_at_once(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "sospeso@example.com")
    token, _ = service.sign_in(db, "sospeso@example.com", GOOD, cfg=cfg)

    user.status = "suspended"
    db.flush()
    assert service.session_from_token(db, token, cfg) is None


# --- password reset ---------------------------------------------------------

def test_reset_changes_the_password_and_drops_every_session(db, cfg, post) -> None:
    """
    The whole point of a reset: it is what you do when you suspect an intruder.

    Leaving the old sessions valid would make it useless in exactly the case
    it exists for.
    """
    user = _active_user(db, cfg, post, "reset@example.com")
    old_session, _ = service.sign_in(db, "reset@example.com", GOOD, cfg=cfg)

    service.request_reset(db, "reset@example.com", cfg=cfg, sender=post)
    service.reset_password(db, _token(db, user, "reset"), "a brand new long password")

    assert service.session_from_token(db, old_session, cfg) is None
    with pytest.raises(service.AccessDenied):
        service.sign_in(db, "reset@example.com", GOOD, cfg=cfg)
    service.sign_in(db, "reset@example.com", "a brand new long password", cfg=cfg)


def test_a_reset_token_works_once(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "resetuno@example.com")
    service.request_reset(db, "resetuno@example.com", cfg=cfg, sender=post)
    token = _token(db, user, "reset")

    service.reset_password(db, token, "a brand new long password")
    with pytest.raises(service.InvalidToken):
        service.reset_password(db, token, "yet another long password")


def test_asking_again_invalidates_the_previous_link(db, cfg, post) -> None:
    """An old link dug out of a mailbox must not open anything."""
    user = _active_user(db, cfg, post, "resetdue@example.com")
    service.request_reset(db, "resetdue@example.com", cfg=cfg, sender=post)
    first = _token(db, user, "reset")

    service.request_reset(db, "resetdue@example.com", cfg=cfg, sender=post)

    with pytest.raises(service.InvalidToken):
        service.reset_password(db, first, "a brand new long password")


def test_reset_for_an_unknown_address_is_silent(db, cfg, post) -> None:
    """No raise, no email, no hint that the address is unknown."""
    service.request_reset(db, "mai-visto@example.com", cfg=cfg, sender=post)
    assert post.last_to("mai-visto@example.com") is None


def test_changing_the_password_keeps_the_current_session(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "cambio@example.com")
    keep, keep_row = service.sign_in(db, "cambio@example.com", GOOD, cfg=cfg)
    drop, _ = service.sign_in(db, "cambio@example.com", GOOD, cfg=cfg)

    service.change_password(db, user, GOOD, "a brand new long password",
                            current_session=keep_row.id)

    assert service.session_from_token(db, keep, cfg) is not None, \
        "changing your password must not sign you out of the page you are on"
    assert service.session_from_token(db, drop, cfg) is None


def test_changing_the_password_requires_the_current_one(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "cambiomale@example.com")
    with pytest.raises(service.AccessDenied, match="current password"):
        service.change_password(db, user, "wrong but long enough", "a brand new long password")


# --- roles ------------------------------------------------------------------

def test_a_customer_reads_picks_but_administers_nothing(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "ruoli@example.com")
    assert service.has_permission(user, "picks:read")
    assert service.has_permission(user, "data:read")
    assert not service.has_permission(user, "users:manage")
    assert not service.has_permission(user, "system:manage")


def test_superadmin_holds_everything_a_customer_does_not(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "capo@example.com")
    service.grant_role(db, user, "superadmin")
    db.flush()
    assert service.has_permission(user, "system:manage")
    assert service.has_permission(user, "users:manage")


def test_revoking_a_role_removes_its_permissions(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "tolto@example.com")
    service.grant_role(db, user, "admin")
    db.flush()
    assert service.has_permission(user, "users:manage")

    service.revoke_role(db, user, "admin")
    db.flush()
    assert not service.has_permission(user, "users:manage")


def test_an_unknown_role_raises_instead_of_silently_doing_nothing(db, cfg, post) -> None:
    user = _active_user(db, cfg, post, "inesistente@example.com")
    with pytest.raises(LookupError, match="does not exist"):
        service.grant_role(db, user, "wizard")


# --- audit ------------------------------------------------------------------

def test_failed_sign_ins_are_recorded_even_for_unknown_addresses(db, cfg) -> None:
    """That pattern is the signature of account enumeration."""
    from backend.auth.models import AuthEvent

    before = db.query(AuthEvent).filter_by(kind="sign_in", outcome="ko").count()
    with pytest.raises(service.AccessDenied):
        service.sign_in(db, "fantasma@example.com", GOOD, cfg=cfg)
    db.flush()

    assert db.query(AuthEvent).filter_by(kind="sign_in", outcome="ko").count() == before + 1


def test_the_audit_log_never_holds_credentials(db, cfg, post) -> None:
    from backend.auth.models import AuthEvent

    _active_user(db, cfg, post, "audit@example.com")
    with pytest.raises(service.AccessDenied):
        service.sign_in(db, "audit@example.com", "wrong but long enough", cfg=cfg)
    db.flush()

    for event in db.query(AuthEvent).all():
        blob = str(event.details or {})
        assert GOOD not in blob and "wrong but long enough" not in blob, \
            f"a password leaked into the audit log: {blob}"
