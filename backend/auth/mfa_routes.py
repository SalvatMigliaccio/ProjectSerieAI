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

from . import mfa, service
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
from .ratelimit import mfa_limiter, mfa_resend_limiter
from .routes import Identity, MfaPending, _identity, _issue_access, _limit

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
    return MfaPending(sent_to=mfa.mask(user.email))


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
    out = JSONResponse(MfaPending(sent_to=mfa.mask(user.email)).model_dump())
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
    out = JSONResponse(MfaPending(sent_to=mfa.mask(user.email)).model_dump())
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
