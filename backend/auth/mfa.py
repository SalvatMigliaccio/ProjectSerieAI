"""
The second factor: a six-digit code sent by email (ADR 0008, step 3).

ONE MECHANISM, THREE USES. Finishing a sign-in (`sign_in`), and confirming from
the account page that 2FA goes on (`enable`) or off (`disable`). All three are
a row of `mfa_challenges` plus a short-lived cookie holding its random token,
and all three are spent the same way.

WHAT EACH PIECE IS FOR:
- the cookie token binds the code to the browser that asked for it: a code
  read off someone's screen is useless from another device;
- the code is stored as an HMAC keyed with the server secret AND salted with
  the challenge, so a database dump cannot be brute-forced over a million
  values, and the same code in two challenges hashes differently;
- `attempts` caps guesses per code; a new code needs the first factor again,
  or a resend, which the route limits per account;
- issuing a new challenge for the same user and purpose spends the old ones:
  there is never more than one live code to guess.

The limit of an emailed second factor is in the ADR and not repeated here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import mail as mail_mod
from . import service, tokens, totp
from .mail import NotSent, Sender
from .models import MfaChallenge, User, now
from .settings import Settings, settings

log = logging.getLogger("auth.mfa")

PURPOSES = ("sign_in", "enable", "disable")


def uses_app(user: User, purpose: str) -> bool:
    """
    True when this challenge is answered from the authenticator app, not by email.

    Signing in and turning 2FA off ask for the factor the account HAS. Turning
    the emailed factor on always goes by email: it is proving the mailbox.
    """
    return user.mfa_method == "totp" and purpose in ("sign_in", "disable")

# A resend keeps the flow alive only this long after the FIRST code: past that,
# start again from the password. Otherwise resending would be a way to keep a
# half-signed-in state open forever.
RESEND_WINDOW = dt.timedelta(minutes=30)


class CodeRefused(Exception):
    """Wrong, expired, spent or foreign. `reason` for the audit, message for the user."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _key(cfg: Settings) -> bytes:
    return hmac.new(cfg.secret_key.get_secret_value().encode(),
                    b"ainaples/mfa-code/v1", hashlib.sha256).digest()


def _code_hash(cfg: Settings, challenge_hash: str, code: str) -> str:
    return hmac.new(_key(cfg), f"{challenge_hash}:{code}".encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    """Six digits from the OS CSPRNG, leading zeros kept: '004217' is a valid code."""
    return f"{secrets.randbelow(10**6):06d}"


def normalise(code: str) -> str:
    """What people type: '123 456', '123-456'. Digits only, or nothing that can match."""
    return "".join(c for c in code if c.isdigit())


def mask(email: str) -> str:
    """
    `m•••o@gmail.com`: enough to recognise your own mailbox, not to read it.

    Shown on the code screen. The address is already known to whoever passed
    the first factor, but a screen over a shoulder should not spell it out.
    """
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}•••@{domain}"
    return f"{local[0]}•••{local[-1]}@{domain}"


def issue(db: Session, user: User, purpose: str, via: str, *,
          cfg: Settings | None = None, sender: Sender | None = None,
          ip: str | None = None, user_agent: str | None = None,
          first_issued: dt.datetime | None = None) -> str:
    """
    Create a challenge, email its code, return the cookie token (cleartext).

    With the authenticator app (`uses_app`) nothing is emailed: the challenge
    still exists, because it is what binds the attempt to this browser and
    counts the attempts, but its code comes from the phone. The stored code
    hash is then random and never matches anything.

    `first_issued` carries the start of a resend chain, so the window counts
    from the first code and not from the latest one.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose}")
    cfg = cfg or settings()
    sender = sender or mail_mod.sender(cfg)
    at = now()

    # One live code per user and purpose.
    db.execute(update(MfaChallenge)
               .where(MfaChallenge.user_id == user.id, MfaChallenge.purpose == purpose,
                      MfaChallenge.used_at.is_(None))
               .values(used_at=at))

    clear, hashed = tokens.generate()
    code = new_code()
    app = uses_app(user, purpose)
    db.add(MfaChallenge(
        user_id=user.id, purpose=purpose, via=via, challenge_hash=hashed,
        code_hash=secrets.token_hex(32) if app else _code_hash(cfg, hashed, code),
        expires_at=at + dt.timedelta(minutes=cfg.mfa_code_minutes),
        # `created_at` is the start of the chain, see `resend`.
        created_at=first_issued or at,
        ip=ip, user_agent=(user_agent or "")[:500] or None,
    ))
    if app:
        service.record_event(db, "mfa_challenge", "ok", user_id=user.id, ip=ip,
                             user_agent=user_agent,
                             details={"purpose": purpose, "via": via, "method": "totp"})
        db.flush()
        return clear
    try:
        sender.send(mail_mod.mfa_code_message(cfg, user.email, code, purpose))
        service.record_event(db, "mfa_code_sent", "ok", user_id=user.id, ip=ip,
                             user_agent=user_agent, details={"purpose": purpose, "via": via})
    except NotSent as exc:
        # Not fatal here: the screen offers "send again". But it is recorded,
        # because "I never got the code" is otherwise impossible to diagnose.
        log.error("2FA code not sent to %s: %s", user.email, exc)
        service.record_event(db, "mfa_code_sent", "ko", user_id=user.id, ip=ip,
                             user_agent=user_agent,
                             details={"purpose": purpose, "cause": "send_failed"})
    db.flush()
    return clear


def _row(db: Session, token: str | None) -> MfaChallenge | None:
    if not token:
        return None
    return db.scalar(select(MfaChallenge).where(
        MfaChallenge.challenge_hash == tokens.digest(token)))


def verify(db: Session, token: str | None, code: str, purpose: str, *,
           cfg: Settings | None = None, user_id: uuid.UUID | None = None,
           ip: str | None = None, user_agent: str | None = None) -> User:
    """
    Spend the challenge if the code is right; return its user.

    `user_id`, when given, must own the challenge: on the account page the
    code must belong to the account that is signed in, not merely to someone.

    A wrong code costs an attempt and is committed before raising, like a
    wrong password (`service.keep_on_refusal`): otherwise the rollback of the
    failed request would hand the attempt back, and the cap would not exist.
    """
    cfg = cfg or settings()
    at = now()
    row = _row(db, token)

    def refuse(reason: str, message: str, uid: uuid.UUID | None) -> CodeRefused:
        service.record_event(db, "mfa_verify", "ko", user_id=uid, ip=ip, user_agent=user_agent,
                             details={"purpose": purpose, "cause": reason})
        service.keep_on_refusal(db)
        return CodeRefused(message, reason)

    if (row is None or row.purpose != purpose
            or (user_id is not None and row.user_id != user_id)):
        raise refuse("no_challenge", "the code has expired: ask for a new one",
                     row.user_id if row else None)
    if not row.is_spendable(at, cfg.mfa_max_attempts):
        raise refuse("expired", "the code has expired or was tried too many times: "
                                "ask for a new one", row.user_id)

    user = db.get(User, row.user_id)
    if user is None or not user.is_active():
        raise refuse("inactive", "account is not active", row.user_id)

    method = "totp" if uses_app(user, purpose) else "email"
    recovery = False
    if method == "totp":
        try:
            if totp.looks_like_recovery(code):
                ok = recovery = totp.use_recovery_code(db, user, code, cfg)
            else:
                ok = totp.check(user, normalise(code), cfg)
        except totp.SecretUnreadable:
            # The key changed under a stored secret: not the user's fault, and
            # no number of attempts will fix it. Recovery codes do not help
            # either (same key). Logged loudly; the message says to ask.
            log.error("TOTP secret of %s cannot be decrypted: check AI_NAPLES_MFA_KEY", user.id)
            raise refuse("secret_unreadable", "the authenticator app cannot be checked "
                         "right now: contact the administrator", user.id) from None
    else:
        ok = hmac.compare_digest(row.code_hash,
                                 _code_hash(cfg, row.challenge_hash, normalise(code)))
    if not ok:
        row.attempts += 1
        left = cfg.mfa_max_attempts - row.attempts
        if left <= 0:
            row.used_at = at
        raise refuse("wrong_code",
                     f"wrong code: {left} attempts left" if left > 0
                     else "wrong code: ask for a new one", row.user_id)

    row.used_at = at
    service.record_event(db, "mfa_verify", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent,
                         details={"purpose": purpose, "via": row.via, "method": method,
                                  **({"recovery_code": True} if recovery else {})})
    return user


def resend(db: Session, token: str | None, purpose: str, *,
           cfg: Settings | None = None, sender: Sender | None = None,
           user_id: uuid.UUID | None = None,
           ip: str | None = None, user_agent: str | None = None) -> tuple[str, User]:
    """
    A fresh code for a challenge still in its chain. Returns `(new_token, user)`.

    Allowed even when the old code is spent by wrong guesses — that is the
    point of "send again" — but not past `RESEND_WINDOW` from the first code.
    """
    cfg = cfg or settings()
    row = _row(db, token)
    if (row is None or row.purpose != purpose
            or (user_id is not None and row.user_id != user_id)
            or now() - row.created_at > RESEND_WINDOW):
        raise CodeRefused("this request has expired: start again", "no_challenge")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active():
        raise CodeRefused("account is not active", "inactive")
    if uses_app(user, purpose):
        # Nothing to resend: the code is on the phone. And no falling back to
        # email, which would undo what the app is for.
        raise CodeRefused("the code comes from your authenticator app; if you lost "
                          "the phone, use a recovery code", "app_method")
    clear = issue(db, user, purpose, row.via, cfg=cfg, sender=sender, ip=ip,
                  user_agent=user_agent, first_issued=row.created_at)
    return clear, user


def peek(db: Session, token: str | None) -> MfaChallenge | None:
    """
    The challenge behind a cookie, without spending anything.

    For what the screens need before a code is typed: whose mailbox to name,
    which account to rate-limit a resend on, how the first factor was passed.
    """
    return _row(db, token)


def enable(db: Session, user: User, *, ip: str | None = None,
           user_agent: str | None = None) -> None:
    user.mfa_method = "email"
    user.mfa_enabled_at = now()
    service.record_event(db, "mfa_enabled", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"method": "email"})


def enable_app(db: Session, user: User, encrypted_secret: str, *, ip: str | None = None,
               user_agent: str | None = None) -> None:
    """Switch the account to the authenticator app. The caller has checked identity and code."""
    previous = user.mfa_method
    user.totp_secret = encrypted_secret
    user.mfa_method = "totp"
    user.mfa_enabled_at = now()
    service.record_event(db, "mfa_enabled", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"method": "totp", "previous": previous})


def disable(db: Session, user: User, *, ip: str | None = None,
            user_agent: str | None = None) -> None:
    method = user.mfa_method
    user.mfa_method = None
    user.mfa_enabled_at = None
    # Nothing of the app is kept once it is off: a secret left behind would be
    # a second factor the user believes gone.
    totp.clear(db, user)
    service.record_event(db, "mfa_disabled", "ok", user_id=user.id, ip=ip,
                         user_agent=user_agent, details={"method": method})
