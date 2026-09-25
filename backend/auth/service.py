"""
Authentication logic, kept away from HTTP.

WHY SEPARATE. Nothing here imports FastAPI and nothing here talks about
requests or responses: a test can sign up, verify, sign in and reset without
starting a server. The routes become translation — read the body, call a
function, pick a status code.

THE RULE THAT RUNS THROUGH THE WHOLE FILE: **never reveal whether an address
exists.** Signup and reset-request answer identically either way. A different
message, a different status code or a different response time turns this
module into an address-validation service for anyone who asks.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import mail as mail_mod
from . import password as pwd
from . import tokens
from .mail import NotSent, Sender
from .models import AuthEvent, EmailToken, Permission, Role, User, UserSession, now
from .settings import Settings, settings

log = logging.getLogger("auth.service")


class AccessDenied(Exception):
    """Wrong credentials, inactive account, or locked out."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        # `reason` is for logs and audit. `message` is what may be shown to the
        # user, and stays deliberately vague.
        self.reason = reason


class InvalidToken(Exception):
    """Expired, already spent, or nonexistent."""


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def record_event(db: Session, kind: str, outcome: str, *,
                 user_id: uuid.UUID | None = None, ip: str | None = None,
                 user_agent: str | None = None, details: dict | None = None) -> None:
    """
    One row in the audit log.

    Never credentials in `details`: context, not secrets. Same rule as the
    predictions log — you write, you do not rewrite.
    """
    db.add(AuthEvent(
        user_id=user_id, kind=kind, outcome=outcome, ip=ip,
        user_agent=(user_agent or "")[:500] or None, details=details,
    ))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def by_email(db: Session, email: str) -> User | None:
    """CITEXT makes the comparison case-insensitive."""
    return db.scalar(select(User).where(User.email == email.strip()))


def permissions_of(user: User) -> set[str]:
    return {p.name for r in user.roles for p in r.permissions}


def has_permission(user: User, permission: str) -> bool:
    return permission in permissions_of(user)


# ---------------------------------------------------------------------------
# Signup and verification
# ---------------------------------------------------------------------------

def sign_up(db: Session, email: str, clear: str, *,
            cfg: Settings | None = None, sender: Sender | None = None,
            ip: str | None = None, user_agent: str | None = None) -> None:
    """
    Create a `pending` account and send the verification email.

    RETURNS NOTHING, DELIBERATELY. If the address is already registered it
    neither raises nor says so: it emails the person who actually owns that
    address instead. Whoever probes with someone else's email sees the same
    response either way.
    """
    cfg = cfg or settings()
    sender = sender or mail_mod.sender(cfg)
    email = email.strip()

    existing = by_email(db, email)
    if existing is not None:
        record_event(db, "signup", "ko", user_id=existing.id, ip=ip,
                     user_agent=user_agent, details={"cause": "already_registered"})
        # No second verification link to an already-active account: that would
        # be a way to flood someone else's mailbox starting from nothing.
        if existing.status == "pending":
            _send_verification(db, existing, cfg, sender)
        return

    pwd.check_strength(clear)
    user = User(email=email, password_hash=pwd.hash_password(clear), status="pending")
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # Two simultaneous signups with the same address: the unique index
        # decides, and the loser behaves like the "already registered" case.
        db.rollback()
        return

    grant_role(db, user, "customer")
    record_event(db, "signup", "ok", user_id=user.id, ip=ip, user_agent=user_agent)
    _send_verification(db, user, cfg, sender)


def _send_verification(db: Session, user: User, cfg: Settings, sender: Sender) -> None:
    clear, hashed = tokens.generate()
    db.add(EmailToken(
        user_id=user.id, kind="verify", token_hash=hashed,
        expires_at=now() + dt.timedelta(hours=cfg.verify_valid_hours),
    ))
    try:
        sender.send(mail_mod.verification_message(cfg, user.email, clear))
    except NotSent as exc:
        # The account already exists: unwinding would leave the user with
        # neither an account nor an email. Record it and move on; asking again
        # is a fresh signup.
        log.error("verification email not sent to %s: %s", user.email, exc)
        record_event(db, "verification_mail", "ko", user_id=user.id,
                     details={"cause": "send_failed"})


def verify_email(db: Session, token: str) -> User:
    row = _spendable_token(db, token, "verify")
    user = db.get(User, row.user_id)
    if user is None:
        raise InvalidToken("invalid or expired token")

    row.used_at = now()
    if user.status == "pending":
        user.status = "active"
    user.email_verified_at = now()
    record_event(db, "verify_email", "ok", user_id=user.id)
    return user


# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------

def sign_in(db: Session, email: str, clear: str, *, cfg: Settings | None = None,
            ip: str | None = None, user_agent: str | None = None) -> tuple[str, UserSession]:
    """
    Check credentials and open a session. Returns `(token, row)`.

    The cleartext token is returned ONLY here, to go into the cookie; the
    database keeps its hash.
    """
    cfg = cfg or settings()
    at = now()
    user = by_email(db, email)

    if user is None:
        # Same cost as a real verification: without it, response time reveals
        # which addresses are registered.
        pwd.dummy_verify()
        record_event(db, "sign_in", "ko", ip=ip, user_agent=user_agent,
                     details={"cause": "unknown_email"})
        raise AccessDenied("invalid credentials", "unknown_email")

    if user.locked_until is not None and at < user.locked_until:
        record_event(db, "sign_in", "ko", user_id=user.id, ip=ip,
                     user_agent=user_agent, details={"cause": "locked"})
        raise AccessDenied("too many failed attempts: try again in a few minutes", "locked")

    if not pwd.verify(user.password_hash, clear):
        _count_failure(db, user, cfg, ip, user_agent)
        raise AccessDenied("invalid credentials", "wrong_password")

    if user.status != "active":
        record_event(db, "sign_in", "ko", user_id=user.id, ip=ip,
                     user_agent=user_agent, details={"cause": f"status_{user.status}"})
        message = ("confirm your address before signing in"
                   if user.status == "pending" else "account is not active")
        raise AccessDenied(message, f"status_{user.status}")

    # argon2 parameters rise over time; sign-in is the only moment the
    # cleartext password is available to redo the hash.
    if pwd.needs_rehash(user.password_hash):
        user.password_hash = pwd.hash_password(clear)

    user.failed_attempts = 0
    user.locked_until = None
    record_event(db, "sign_in", "ok", user_id=user.id, ip=ip, user_agent=user_agent)
    return open_session(db, user, cfg, ip=ip, user_agent=user_agent)


def _count_failure(db: Session, user: User, cfg: Settings,
                   ip: str | None, user_agent: str | None) -> None:
    user.failed_attempts += 1
    details = {"cause": "wrong_password", "attempts": user.failed_attempts}
    if user.failed_attempts >= cfg.max_attempts:
        # The lockout is per ACCOUNT, not per IP: a distributed attack rotates
        # addresses on every attempt and a per-IP limit would never see it.
        user.locked_until = now() + dt.timedelta(minutes=cfg.lockout_minutes)
        details["locked_for_minutes"] = cfg.lockout_minutes
    record_event(db, "sign_in", "ko", user_id=user.id, ip=ip,
                 user_agent=user_agent, details=details)


def open_session(db: Session, user: User, cfg: Settings, *, ip: str | None = None,
                 user_agent: str | None = None) -> tuple[str, UserSession]:
    clear, hashed = tokens.generate()
    row = UserSession(
        user_id=user.id, token_hash=hashed,
        expires_at=now() + dt.timedelta(hours=cfg.session_max_hours),
        ip=ip, user_agent=(user_agent or "")[:500] or None,
    )
    db.add(row)
    db.flush()
    return clear, row


def session_from_token(db: Session, token: str, cfg: Settings | None = None) -> UserSession | None:
    """
    The valid session for that token, refreshing last-seen.

    `None` covers every way in: nonexistent, revoked, expired, idle too long —
    because the caller needs the same answer for all of them: 401.
    """
    cfg = cfg or settings()
    row = db.scalar(select(UserSession).where(UserSession.token_hash == tokens.digest(token)))
    if row is None:
        return None

    at = now()
    if not row.is_valid(at, dt.timedelta(minutes=cfg.session_idle_minutes)):
        return None
    if row.user.status != "active":
        # An account suspended mid-session must drop immediately, not when the
        # cookie happens to expire.
        return None

    row.last_seen_at = at
    return row


def sign_out(db: Session, token: str) -> None:
    """Server-side revocation. Clearing the cookie is not enough: a copy still works."""
    row = db.scalar(select(UserSession).where(UserSession.token_hash == tokens.digest(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = now()
        record_event(db, "sign_out", "ok", user_id=row.user_id)


def revoke_all(db: Session, user: User, *, keep: uuid.UUID | None = None) -> int:
    at = now()
    rows = db.scalars(select(UserSession).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None))).all()
    count = 0
    for r in rows:
        if keep is not None and r.id == keep:
            continue
        r.revoked_at = at
        count += 1
    return count


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def request_reset(db: Session, email: str, *, cfg: Settings | None = None,
                  sender: Sender | None = None, ip: str | None = None,
                  user_agent: str | None = None) -> None:
    """
    Email the reset link if the address exists. Never says whether it does.

    The caller always answers the same way. A 404 here would turn the endpoint
    into an address checker usable by anyone.
    """
    cfg = cfg or settings()
    sender = sender or mail_mod.sender(cfg)
    user = by_email(db, email)

    if user is None or user.status in ("suspended", "deleted"):
        record_event(db, "reset_requested", "ko", ip=ip, user_agent=user_agent,
                     details={"cause": "recipient_not_eligible"})
        return

    # Burn earlier reset tokens: if only the latest stays valid, an old link
    # dug out of a mailbox opens nothing.
    for old in db.scalars(select(EmailToken).where(
            EmailToken.user_id == user.id, EmailToken.kind == "reset",
            EmailToken.used_at.is_(None))).all():
        old.used_at = now()

    clear, hashed = tokens.generate()
    db.add(EmailToken(
        user_id=user.id, kind="reset", token_hash=hashed,
        expires_at=now() + dt.timedelta(minutes=cfg.reset_valid_minutes),
    ))
    record_event(db, "reset_requested", "ok", user_id=user.id, ip=ip, user_agent=user_agent)
    try:
        sender.send(mail_mod.reset_message(cfg, user.email, clear))
    except NotSent as exc:
        log.error("reset email not sent to %s: %s", user.email, exc)


def reset_password(db: Session, token: str, new: str, *, ip: str | None = None,
                   user_agent: str | None = None) -> User:
    """
    Change the password and **drop every session**.

    A reset is what someone does when they suspect an intruder. Leaving open
    sessions valid would make the operation useless in exactly the case it
    exists for.
    """
    row = _spendable_token(db, token, "reset")
    user = db.get(User, row.user_id)
    if user is None:
        raise InvalidToken("invalid or expired token")

    pwd.check_strength(new)
    row.used_at = now()
    user.password_hash = pwd.hash_password(new)
    user.failed_attempts = 0
    user.locked_until = None
    # Getting here proves they read mail at that address, which is the same
    # proof the verification step asks for.
    if user.status == "pending":
        user.status = "active"
        user.email_verified_at = now()

    count = revoke_all(db, user)
    record_event(db, "reset_done", "ok", user_id=user.id, ip=ip,
                 user_agent=user_agent, details={"sessions_revoked": count})
    return user


def change_password(db: Session, user: User, current: str, new: str, *,
                    current_session: uuid.UUID | None = None) -> None:
    """Change from inside the account area: the current password is required."""
    if not pwd.verify(user.password_hash, current):
        record_event(db, "change_password", "ko", user_id=user.id,
                     details={"cause": "wrong_current"})
        raise AccessDenied("the current password is not correct", "wrong_current")

    pwd.check_strength(new)
    user.password_hash = pwd.hash_password(new)
    # Other sessions drop, the current one survives: changing your password
    # must not sign you out of the page you are doing it from.
    count = revoke_all(db, user, keep=current_session)
    record_event(db, "change_password", "ok", user_id=user.id,
                 details={"sessions_revoked": count})


def _spendable_token(db: Session, token: str, kind: str) -> EmailToken:
    row = db.scalar(select(EmailToken).where(
        EmailToken.token_hash == tokens.digest(token), EmailToken.kind == kind))
    # One message for nonexistent, expired and already-spent alike:
    # distinguishing them would tell someone guessing tokens when they hit a
    # real one.
    if row is None or not row.is_spendable(now()):
        raise InvalidToken("invalid or expired token")
    return row


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def grant_role(db: Session, user: User, name: str, *, by: uuid.UUID | None = None) -> None:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        raise LookupError(f"role '{name}' does not exist: run the base-roles migration")
    if role not in user.roles:
        user.roles.append(role)
        record_event(db, "role_granted", "ok", user_id=user.id,
                     details={"role": name, "by": str(by) if by else None})


def revoke_role(db: Session, user: User, name: str, *, by: uuid.UUID | None = None) -> None:
    for r in list(user.roles):
        if r.name == name:
            user.roles.remove(r)
            record_event(db, "role_revoked", "ok", user_id=user.id,
                         details={"role": name, "by": str(by) if by else None})
            return


def permission_exists(db: Session, name: str) -> bool:
    return db.scalar(select(Permission).where(Permission.name == name)) is not None
