"""
Who an external sign-in opens, and when an account may be tied to one (ADR 0008).

`oauth.py` proves who the person is at Google. This module decides what that
proof is worth here, and it is where the dangerous mistakes would live:
signing someone into another person's account because two emails matched.

THE RULES, IN THE ORDER THEY ARE APPLIED:

1. A known `(provider, subject)` opens its account — provided the account is
   active. The email is not consulted: it may have changed at Google since.
2. An unknown subject with an UNVERIFIED Google email opens nothing. Google
   accounts can carry an address the person never proved they own.
3. An unknown subject whose verified email matches no account creates one:
   active, verified (Google just proved the mailbox), no password, customer.
4. If the email matches an existing account, the two are linked only when that
   account has ITSELF verified the address and is active. A `pending` account
   may have been opened by someone else with this address and a password of
   their choosing: linking would activate it, and their password with it.
   The way out for the real owner is "forgot password", which activates the
   account with a password of THEIR choosing; then they link Google.

Every outcome, refused or not, is an audit row with its cause.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import service
from .models import User, UserIdentity, UserSession, now
from .oauth import Claims
from .settings import Settings, settings

# What the user is told, per refusal. Short, and saying what to do next.
# The `reason` (second argument of AccessDenied) is what the frontend maps to
# Italian; these English messages are for logs and API clients.
REASONS = {
    "email_unverified": "the Google account has no verified email",
    "link_pending": "an account with this email exists but is not confirmed yet",
    "link_required": "an account with this email exists: sign in with your password "
                     "and link Google from your profile",
    "inactive": "account is not active",
    "other_google": "this account is already linked to a different Google account",
    "linked_elsewhere": "this Google account is already linked to another account",
    "mismatch": "this is not the Google account linked to your profile",
    "last_method": "this is your only way to sign in: set a password first",
}


def _refuse(db: Session, kind: str, reason: str, *, user_id: uuid.UUID | None,
            provider: str, ip: str | None, user_agent: str | None) -> service.AccessDenied:
    service.record_event(db, kind, "ko", user_id=user_id, ip=ip, user_agent=user_agent,
                         details={"via": provider, "cause": reason})
    service.keep_on_refusal(db)
    return service.AccessDenied(REASONS[reason], reason)


def find(db: Session, provider: str, subject: str) -> UserIdentity | None:
    return db.scalar(select(UserIdentity).where(
        UserIdentity.provider == provider, UserIdentity.subject == subject))


def of(user: User, provider: str) -> UserIdentity | None:
    return next((i for i in user.identities if i.provider == provider), None)


def _attach(db: Session, user: User, claims: Claims) -> UserIdentity | None:
    """
    Insert the link. `None` if the unique index says someone got there first.

    Inside a savepoint: a failed INSERT otherwise poisons the whole request
    transaction, audit rows included.
    """
    row = UserIdentity(user_id=user.id, provider=claims.provider, subject=claims.subject,
                       email=claims.email, last_used_at=now())
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    db.refresh(user, ["identities"])
    return row


def sign_in(db: Session, claims: Claims, *, cfg: Settings | None = None,
            ip: str | None = None, user_agent: str | None = None
            ) -> tuple[str, UserSession]:
    """Open a session from verified provider claims, or raise `AccessDenied`."""
    cfg = cfg or settings()
    p = claims.provider
    via = {"via": p}

    known = find(db, p, claims.subject)
    if known is not None:
        user = db.get(User, known.user_id)
        if user is None or not user.is_active():
            raise _refuse(db, "sign_in", "inactive", user_id=known.user_id, provider=p,
                          ip=ip, user_agent=user_agent)
        known.last_used_at = now()
    else:
        if not claims.email or not claims.email_verified:
            raise _refuse(db, "sign_in", "email_unverified", user_id=None, provider=p,
                          ip=ip, user_agent=user_agent)
        user = service.by_email(db, claims.email)
        if user is None:
            user = _create(db, claims, ip=ip, user_agent=user_agent)
        else:
            if user.status == "pending" or user.email_verified_at is None:
                raise _refuse(db, "sign_in", "link_pending", user_id=user.id, provider=p,
                              ip=ip, user_agent=user_agent)
            if not user.is_active():
                raise _refuse(db, "sign_in", "inactive", user_id=user.id, provider=p,
                              ip=ip, user_agent=user_agent)
            if of(user, p) is not None:
                # Same address, different Google subject: the account is already
                # tied to another Google identity. Two would be two doors.
                raise _refuse(db, "sign_in", "other_google", user_id=user.id, provider=p,
                              ip=ip, user_agent=user_agent)
            if _attach(db, user, claims) is None:
                raise _refuse(db, "sign_in", "linked_elsewhere", user_id=user.id,
                              provider=p, ip=ip, user_agent=user_agent)
            service.record_event(db, "identity_linked", "ok", user_id=user.id, ip=ip,
                                 user_agent=user_agent, details={**via, "auto": True})

    service.record_event(db, "sign_in", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details=via)
    # Same as the password path: before the session, so the first access token
    # already carries the role.
    service.apply_bootstrap(db, user, cfg)
    return service.open_session(db, user, cfg, ip=ip, user_agent=user_agent)


def _create(db: Session, claims: Claims, *, ip: str | None,
            user_agent: str | None) -> User:
    """A new account from a verified provider email: active at once, no password."""
    user = User(email=claims.email, password_hash=None, status="active",
                email_verified_at=now())
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:
        # Two first sign-ins racing on the same address: the other one created
        # it. Refuse this one; a second click finds the account and the link.
        raise _refuse(db, "sign_in", "link_required", user_id=None,
                      provider=claims.provider, ip=ip, user_agent=user_agent) from None
    service.grant_role(db, user, "customer")
    service.record_event(db, "signup", "ok", user_id=user.id, ip=ip, user_agent=user_agent,
                         details={"via": claims.provider})
    if _attach(db, user, claims) is None:
        raise _refuse(db, "sign_in", "linked_elsewhere", user_id=user.id,
                      provider=claims.provider, ip=ip, user_agent=user_agent)
    return user


def link(db: Session, user: User, claims: Claims, *, ip: str | None = None,
         user_agent: str | None = None) -> UserIdentity:
    """
    Tie a provider to a signed-in account, from the profile page.

    The Google email does not have to match the account's: linking is what the
    owner, already signed in, asked for. Its verification state does not
    matter either, for the same reason — it is not used to find anyone.
    """
    p = claims.provider
    known = find(db, p, claims.subject)
    if known is not None:
        if known.user_id == user.id:
            return known      # already done: idempotent, no second audit row
        raise _refuse(db, "identity_linked", "linked_elsewhere", user_id=user.id,
                      provider=p, ip=ip, user_agent=user_agent)
    if of(user, p) is not None:
        raise _refuse(db, "identity_linked", "other_google", user_id=user.id,
                      provider=p, ip=ip, user_agent=user_agent)
    row = _attach(db, user, claims)
    if row is None:
        raise _refuse(db, "identity_linked", "linked_elsewhere", user_id=user.id,
                      provider=p, ip=ip, user_agent=user_agent)
    service.record_event(db, "identity_linked", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"via": p})
    return row


def unlink(db: Session, user: User, provider: str, *, ip: str | None = None,
           user_agent: str | None = None) -> bool:
    """
    Remove a provider. `False` if it was not linked.

    REFUSED WHEN IT IS THE LAST WAY IN. An account with no password and one
    provider would be left with no way to sign in at all, and the only exit
    would be an administrator writing SQL.
    """
    row = of(user, provider)
    if row is None:
        return False
    others = [i for i in user.identities if i.provider != provider]
    if not user.has_password() and not others:
        raise _refuse(db, "identity_unlinked", "last_method", user_id=user.id,
                      provider=provider, ip=ip, user_agent=user_agent)
    db.delete(row)
    db.flush()
    db.refresh(user, ["identities"])
    service.record_event(db, "identity_unlinked", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"via": provider})
    return True


def confirms(db: Session, user: User, claims: Claims, *, ip: str | None = None,
             user_agent: str | None = None) -> None:
    """
    Re-confirmation: the claims must be the provider identity ALREADY linked.

    Any Google account proves that someone holds a Google account; only the
    linked one proves it is the owner of this account.
    """
    row = of(user, claims.provider)
    if row is None or row.subject != claims.subject:
        raise _refuse(db, "reauth", "mismatch", user_id=user.id, provider=claims.provider,
                      ip=ip, user_agent=user_agent)
    row.last_used_at = now()
    service.record_event(db, "reauth", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"via": claims.provider})
