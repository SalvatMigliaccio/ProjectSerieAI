"""
The second factor over HTTP (ADR 0008, step 3).

Two groups under `/api/auth/mfa`, the prefix the `ainaples_mfa` cookie is
scoped to:

- finishing a sign-in, with no session yet: `GET /pending` (what the code
  screen shows), `POST /verify`, `POST /resend`;
- the account page, with a session: `POST /enable/start` and `/enable/confirm`,
  `POST /disable/start` and `/disable/confirm`.

TURNING IT OFF NEEDS A CODE TOO. Otherwise a stolen session — exactly what a
second factor exists to make less useful — could switch it off in one click
and keep the account. Turning it on needs a code because it proves the mailbox
works before the account starts depending on it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import mfa, oauth, service, totp
from . import password as pwd
from .deps import (
    Cfg,
    CurrentSession,
    Db,
    Mailer,
    clear_mfa_cookie,
    client_ip,
    set_mfa_cookie,
    set_session_cookie,
)
from .models import User, now
from .ratelimit import mfa_limiter, mfa_resend_limiter, password_change_limiter
from .routes import Identity, MfaPending, _identity, _issue_access, _limit, pending_for

log = logging.getLogger("auth.mfa_routes")

router = APIRouter(prefix="/api/auth/mfa", tags=["auth"])


class CodePayload(BaseModel):
    # Generous on length: people paste "123 456" or "123-456". `mfa.normalise`
    # keeps the digits.
    code: str = Field(min_length=6, max_length=16)


def _refused(exc: mfa.CodeRefused) -> HTTPException:
    """400 with the service's message: what is wrong and what to do next."""
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


def _resend_allowed(key: str) -> None:
    if not mfa_resend_limiter.allow(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many codes requested: wait before asking again",
            headers={"Retry-After": str(mfa_resend_limiter.retry_after(key))},
        )


# --- finishing a sign-in --------------------------------------------------------

@router.get("/pending")
def pending(request: Request, db: Db, cfg: Cfg) -> MfaPending:
    """
    Which mailbox the code went to, for the code screen. 404 if nothing pending.

    The sign-in with a password gets this in its 202; the Google sign-in
    arrives by redirect and has to ask.
    """
    row = mfa.peek(db, request.cookies.get(cfg.mfa_cookie_name))
    # A spent code still counts as pending while "send again" may revive it.
    if (row is None or row.purpose != "sign_in"
            or now() - row.created_at > mfa.RESEND_WINDOW):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no sign-in waiting for a code")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no sign-in waiting for a code")
    return pending_for(user)


@router.post("/verify")
def verify(payload: CodePayload, request: Request, response: Response,
           db: Db, cfg: Cfg) -> Identity:
    """The code turns the pending sign-in into a session: same cookies as a plain sign-in."""
    _limit(mfa_limiter, request, "mfa-verify")
    ip, ua = client_ip(request), request.headers.get("user-agent")
    token = request.cookies.get(cfg.mfa_cookie_name)
    row = mfa.peek(db, token)
    via = row.via if row else "password"
    try:
        user = mfa.verify(db, token, payload.code, "sign_in", cfg=cfg, ip=ip, user_agent=ua)
    except mfa.CodeRefused as exc:
        raise _refused(exc) from exc

    refresh_token, session = service.complete_sign_in(db, user, cfg, via=via, ip=ip,
                                                      user_agent=ua)
    set_session_cookie(response, refresh_token, cfg)
    _issue_access(response, session, cfg)
    clear_mfa_cookie(response, cfg)
    return _identity(user)


@router.post("/resend")
def resend(request: Request, db: Db, cfg: Cfg, sender: Mailer) -> MfaPending:
    _limit(mfa_limiter, request, "mfa-resend")
    token = request.cookies.get(cfg.mfa_cookie_name)
    row = mfa.peek(db, token)
    if row is not None:
        _resend_allowed(str(row.user_id))
    try:
        new_token, user = mfa.resend(db, token, "sign_in", cfg=cfg, sender=sender,
                                     ip=client_ip(request),
                                     user_agent=request.headers.get("user-agent"))
    except mfa.CodeRefused as exc:
        raise _refused(exc) from exc
    out = JSONResponse(pending_for(user).model_dump())
    set_mfa_cookie(out, new_token, cfg)
    return out


# --- turning it on and off from the account page --------------------------------

def _start(purpose: str, row, request: Request, db, cfg, sender) -> JSONResponse:
    user = row.user
    if purpose == "enable":
        if user.mfa_enabled():
            raise HTTPException(status.HTTP_409_CONFLICT, "two-step verification is already on")
        if user.email_verified_at is None:
            # The code goes to that mailbox: it has to be one we know works.
            raise HTTPException(status.HTTP_409_CONFLICT, "verify your email address first")
    elif not user.mfa_enabled():
        raise HTTPException(status.HTTP_409_CONFLICT, "two-step verification is already off")

    _resend_allowed(str(user.id))
    token = mfa.issue(db, user, purpose, "session", cfg=cfg, sender=sender,
                      ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    out = JSONResponse(pending_for(user, purpose).model_dump())
    set_mfa_cookie(out, token, cfg)
    return out


def _confirm(purpose: str, payload: CodePayload, row, request: Request,
             response: Response, db, cfg) -> Identity:
    _limit(mfa_limiter, request, f"mfa-{purpose}")
    ip, ua = client_ip(request), request.headers.get("user-agent")
    try:
        user = mfa.verify(db, request.cookies.get(cfg.mfa_cookie_name), payload.code, purpose,
                          cfg=cfg, user_id=row.user_id, ip=ip, user_agent=ua)
    except mfa.CodeRefused as exc:
        raise _refused(exc) from exc
    (mfa.enable if purpose == "enable" else mfa.disable)(db, user, ip=ip, user_agent=ua)
    clear_mfa_cookie(response, cfg)
    return _identity(user)


@router.post("/enable/start", responses={409: {"description": "already on, or email unverified"}})
def enable_start(row: CurrentSession, request: Request, db: Db, cfg: Cfg,
                 sender: Mailer) -> MfaPending:
    return _start("enable", row, request, db, cfg, sender)


@router.post("/enable/confirm")
def enable_confirm(payload: CodePayload, row: CurrentSession, request: Request,
                   response: Response, db: Db, cfg: Cfg) -> Identity:
    return _confirm("enable", payload, row, request, response, db, cfg)


@router.post("/disable/start", responses={409: {"description": "already off"}})
def disable_start(row: CurrentSession, request: Request, db: Db, cfg: Cfg,
                  sender: Mailer) -> MfaPending:
    return _start("disable", row, request, db, cfg, sender)


@router.post("/disable/confirm")
def disable_confirm(payload: CodePayload, row: CurrentSession, request: Request,
                    response: Response, db: Db, cfg: Cfg) -> Identity:
    return _confirm("disable", payload, row, request, response, db, cfg)


# --- the authenticator app (TOTP) -------------------------------------------------

class TotpSetup(BaseModel):
    """
    What the person needs to add the account to their app.

    The secret is shown AND carried by the QR code: the camera does not always
    cooperate, and typing it by hand is the fallback every app offers. It is
    sent once, over the session, and stored only encrypted.
    """

    secret: str
    otpauth_uri: str
    qr: str


class TotpConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=16)
    # Required for an account that has one; an account without a password
    # confirms with a fresh Google round trip instead (the reauth cookie).
    password: str | None = Field(default=None, max_length=1024)


class TotpEnabled(BaseModel):
    identity: Identity
    # Shown ONCE. The server keeps only their hashes.
    recovery_codes: list[str]


class MfaStatus(BaseModel):
    method: str | None
    enabled_at: str | None = None
    recovery_codes_left: int | None = None


@router.get("/status")
def mfa_status(row: CurrentSession, db: Db) -> MfaStatus:
    user = row.user
    return MfaStatus(
        method=user.mfa_method,
        enabled_at=user.mfa_enabled_at.isoformat() if user.mfa_enabled_at else None,
        recovery_codes_left=totp.recovery_codes_left(db, user) if user.mfa_method == "totp" else None,
    )


@router.post("/totp/setup", responses={409: {"description": "the app is already on"}})
def totp_setup(row: CurrentSession, request: Request, db: Db, cfg: Cfg) -> TotpSetup:
    """
    A new secret for the app, not active yet: `/totp/confirm` activates it.

    Calling it again replaces the pending secret, so a QR code shown and
    abandoned never becomes valid.
    """
    _limit(mfa_limiter, request, "totp-setup")
    user = row.user
    if user.mfa_method == "totp":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the authenticator app is already on: turn it off first")
    secret = totp.new_secret()
    user.totp_secret = totp.encrypt(secret, cfg)
    user.totp_last_step = None
    uri = totp.provisioning_uri(secret, user.email, cfg)
    return TotpSetup(secret=totp.readable(secret), otpauth_uri=uri, qr=totp.qr_data_uri(uri))


@router.post("/totp/confirm", responses={403: {"description": "identity not confirmed"}})
def totp_confirm(payload: TotpConfirm, row: CurrentSession, request: Request,
                 db: Db, cfg: Cfg) -> TotpEnabled:
    """
    Turn the app on: needs the code from the app AND proof that this is the owner.

    WITHOUT THE SECOND CONDITION a stolen session could install its own app and
    lock the real owner out of the account. The password is the proof; for an
    account without one, a fresh Google confirmation (as for deletion).
    """
    _limit(mfa_limiter, request, "totp-confirm")
    user = row.user
    ip, ua = client_ip(request), request.headers.get("user-agent")
    if user.mfa_method == "totp" or not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the setup again")

    key = str(user.id)
    if not password_change_limiter.allow(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "too many requests: wait a few minutes before trying again",
                            headers={"Retry-After": str(password_change_limiter.retry_after(key))})
    if user.has_password():
        proved = bool(payload.password) and pwd.verify(user.password_hash, payload.password)
    else:
        proved = oauth.reauth_valid(request.cookies.get(cfg.reauth_cookie_name),
                                    row.user_id, row.id, cfg)
    if not proved:
        service.record_event(db, "mfa_enabled", "ko", user_id=user.id, ip=ip, user_agent=ua,
                             details={"method": "totp", "cause": "identity_not_confirmed"})
        service.keep_on_refusal(db)
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "the password is not correct" if user.has_password()
                            else "confirm with your Google account first")

    try:
        pending_secret = totp.decrypt(user.totp_secret, cfg)
    except totp.SecretUnreadable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the setup again") from exc
    if not totp.check(user, mfa.normalise(payload.code), cfg, secret=pending_secret):
        service.record_event(db, "mfa_enabled", "ko", user_id=user.id, ip=ip, user_agent=ua,
                             details={"method": "totp", "cause": "wrong_code"})
        service.keep_on_refusal(db)
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "the code from the app is not right: check the phone's clock and retry")

    mfa.enable_app(db, user, user.totp_secret, ip=ip, user_agent=ua)
    codes = totp.new_recovery_codes(db, user, cfg)
    return TotpEnabled(identity=_identity(user), recovery_codes=codes)


@router.post("/totp/recovery-codes")
def totp_new_recovery_codes(payload: CodePayload, row: CurrentSession, request: Request,
                            db: Db, cfg: Cfg) -> TotpEnabled:
    """
    A fresh set of recovery codes; the old ones stop working.

    Needs a code from the APP, not a recovery code: whoever holds one leftover
    recovery code must not be able to mint ten more.
    """
    _limit(mfa_limiter, request, "totp-recovery")
    user = row.user
    if user.mfa_method != "totp":
        raise HTTPException(status.HTTP_409_CONFLICT, "the authenticator app is not on")
    if not totp.check(user, mfa.normalise(payload.code), cfg):
        service.record_event(db, "mfa_recovery_codes", "ko", user_id=user.id,
                             ip=client_ip(request), details={"cause": "wrong_code"})
        service.keep_on_refusal(db)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the code from the app is not right")
    codes = totp.new_recovery_codes(db, user, cfg)
    service.record_event(db, "mfa_recovery_codes", "ok", user_id=user.id, ip=client_ip(request))
    return TotpEnabled(identity=_identity(user), recovery_codes=codes)
