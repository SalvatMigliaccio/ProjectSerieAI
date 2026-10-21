"""
The authentication endpoints.

These are the only routes in the whole API allowed to declare a write method,
and `backend.api._assert_read_only_routes` enforces that at startup. They write
to Postgres; the track record stays a set of files that only `predict_round`
and `close_round` open for writing, from the local machine.

THE SHAPE OF THE ANSWERS IS PART OF THE SECURITY. Signup and forgot-password
return 202 unconditionally — same body, same status, whether or not the address
is registered. Returning 404 for an unknown address would turn either endpoint
into an address-validation service for anyone with curl.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from . import access, service
from .deps import (
    Cfg,
    CurrentSession,
    CurrentUser,
    Db,
    Mailer,
    clear_session_cookie,
    client_ip,
    set_access_cookie,
    set_session_cookie,
)
from .models import User
from .password import MIN_LENGTH, WeakPassword
from .ratelimit import (
    password_change_limiter,
    refresh_limiter,
    reset_limiter,
    session_admin_limiter,
    sign_in_limiter,
    signup_limiter,
    token_limiter,
)

log = logging.getLogger("auth.routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCEPTED = (
    "If the address is valid you will receive an email. "
    "Check the spam folder too."
)


# --- payloads ---------------------------------------------------------------

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class EmailOnly(BaseModel):
    email: EmailStr


class TokenOnly(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ResetPayload(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=MIN_LENGTH, max_length=1024)


class ChangePayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=MIN_LENGTH, max_length=1024)


class SessionRow(BaseModel):
    """
    One active session, as its owner sees it.

    NO TOKEN AND NO HASH. The point of storing only the digest is that reading
    the table does not let anyone impersonate anyone; putting either on the
    wire would give that back. `current` is what the page needs instead —
    enough to say "this is the one you are using now" without naming it.
    """

    id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    ip: str | None = None
    user_agent: str | None = None
    current: bool


class Identity(BaseModel):
    """What the frontend needs to draw the interface. Never anything secret."""

    id: str
    email: EmailStr
    status: str
    roles: list[str]
    permissions: list[str]
    # Two dates for the account page. Optional so a client that predates them
    # keeps working, and because an unverified account has no verification.
    created_at: str | None = None
    email_verified_at: str | None = None


def _identity(user: User) -> Identity:
    return Identity(
        id=str(user.id),
        email=user.email,
        status=user.status,
        roles=sorted(r.name for r in user.roles),
        permissions=sorted(service.permissions_of(user)),
        created_at=user.created_at.isoformat() if user.created_at else None,
        email_verified_at=user.email_verified_at.isoformat() if user.email_verified_at else None,
    )


class ActivityRow(BaseModel):
    """
    One entry of the account's own audit trail, as its owner sees it.

    NO `details`. That column holds context for whoever investigates — the id
    of the admin who granted a role, the reason of a refusal — and none of it
    is the user's to read. What is left is what a person can act on: what
    happened, whether it worked, when, from where.
    """

    kind: str
    outcome: str
    at: str
    ip: str | None = None
    user_agent: str | None = None


def _issue_access(response: Response, row, cfg) -> dt.datetime:
    """Mint an access token for a live session row and set its cookie."""
    token, expires = access.issue(row.user_id, row.id, service.permissions_of(row.user), cfg)
    set_access_cookie(response, token, cfg)
    return expires


def _hand_over(response: Response, refresh_token: str, row, cfg) -> None:
    """A session just opened: the refresh cookie and the first access token."""
    set_session_cookie(response, refresh_token, cfg)
    _issue_access(response, row, cfg)


def _refused(cfg) -> Response:
    """401 that still clears both cookies (see `refresh`)."""
    out = JSONResponse({"detail": "session not valid"}, status_code=status.HTTP_401_UNAUTHORIZED)
    clear_session_cookie(out, cfg)
    return out


def _limit(limiter, request: Request, what: str, key: str | None = None) -> None:
    """
    429 with `Retry-After` when `limiter` is full for this caller.

    `key` defaults to the caller's address; the account routes pass the user id
    instead, because what they protect is the account, and an attacker holding
    a stolen token can change address at will.
    """
    key = key or client_ip(request) or "unknown"
    if not limiter.allow(key):
        log.warning("rate limit hit on %s from %s", what, key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many requests: wait a few minutes before trying again",
            headers={"Retry-After": str(limiter.retry_after(key))},
        )


# --- signup and verification ------------------------------------------------

@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
def signup(payload: Credentials, request: Request, db: Db, cfg: Cfg,
           sender: Mailer) -> dict:
    """
    Always 202, whether or not the address is already registered.

    The account is created inactive; only the emailed link activates it. That
    is what stops someone signing up with an address they do not own and then
    using it.
    """
    _limit(signup_limiter, request, "signup")
    try:
        service.sign_up(db, payload.email, payload.password, cfg=cfg, sender=sender,
                        ip=client_ip(request),
                        user_agent=request.headers.get("user-agent"))
    except WeakPassword as exc:
        # The only detail worth returning: the caller must be able to fix it.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"detail": ACCEPTED}


@router.post("/verify-email")
def verify_email(payload: TokenOnly, response: Response, request: Request,
                 db: Db,
                 cfg: Cfg) -> Identity:
    """Confirms the address and signs the user in straight away."""
    _limit(token_limiter, request, "verify-email")
    try:
        user = service.verify_email(db, payload.token)
    except service.InvalidToken as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    token, row = service.open_session(db, user, cfg, ip=client_ip(request),
                                      user_agent=request.headers.get("user-agent"))
    _hand_over(response, token, row, cfg)
    return _identity(user)


# --- sign in and out --------------------------------------------------------

@router.post("/sign-in")
def sign_in(payload: Credentials, response: Response, request: Request,
            db: Db,
            cfg: Cfg) -> Identity:
    _limit(sign_in_limiter, request, "sign-in")
    try:
        token, row = service.sign_in(db, payload.email, payload.password, cfg=cfg,
                                   ip=client_ip(request),
                                   user_agent=request.headers.get("user-agent"))
    except service.AccessDenied as exc:
        # 401 for every reason, with the message the service chose. The service
        # is what decides how vague to be; the route does not second-guess it.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    _hand_over(response, token, row, cfg)
    return _identity(row.user)


class Refreshed(BaseModel):
    """When the new access token lapses, and when the session behind it does."""

    access_expires_at: str
    session_expires_at: str


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Db, cfg: Cfg) -> Refreshed:
    """
    A new access token from the refresh cookie, or 401.

    Every check of a session happens HERE, on the row: revoked, past its 24
    hours, idle too long, account no longer active. That is why an access token
    can be trusted for its few minutes without the database — the moment it
    lapses, the next one is only issued if the session still stands.

    THE REFRESH TOKEN IS NOT ROTATED, and it is a choice. Rotation (a new
    refresh token at every refresh, the old one dead) detects a stolen copy
    only if the thief and the owner both use it — and it logs the owner out
    whenever two tabs refresh at the same moment, since one of them presents a
    token the other has just burnt. With a 24-hour ceiling and a sessions page
    that revokes a device in one click, the race costs more than it buys.
    """
    _limit(refresh_limiter, request, "refresh")
    token = request.cookies.get(cfg.cookie_name)
    row = service.session_from_token(db, token, cfg) if token else None
    if row is None:
        clear_session_cookie(response, cfg)
        # Raising would discard the headers set on `response`, so the dead
        # cookies would stay in the browser and be sent again on every retry.
        return _refused(cfg)
    expires = _issue_access(response, row, cfg)
    return Refreshed(access_expires_at=expires.isoformat(),
                     session_expires_at=row.expires_at.isoformat())


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(request: Request, response: Response,
             db: Db,
             cfg: Cfg) -> Response:
    """
    Always 204, even without a valid session.

    Signing out is not a place to report errors: if the cookie is already
    unusable, the caller wanted exactly this state and gets it.
    """
    token = request.cookies.get(cfg.cookie_name)
    if token:
        service.sign_out(db, token)
    # The cookies go on the response that is RETURNED. They used to be set on
    # the injected `response` and a fresh 204 returned instead, which drops
    # its headers: the session was revoked server-side, but the browser kept
    # both cookies. Harmless while the server checked every request; with a
    # JWT the data routes would keep accepting that cookie until it expired.
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(out, cfg)
    return out


@router.get("/me")
def me(user: CurrentUser) -> Identity:
    """Who the cookie belongs to. The frontend calls this on load."""
    return _identity(user)


# --- password ---------------------------------------------------------------

@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: EmailOnly, request: Request, db: Db, cfg: Cfg,
                    sender: Mailer) -> dict:
    """Always 202. A 404 here would make this an address checker."""
    _limit(reset_limiter, request, "forgot-password")
    service.request_reset(db, payload.email, cfg=cfg, sender=sender, ip=client_ip(request),
                          user_agent=request.headers.get("user-agent"))
    return {"detail": ACCEPTED}


@router.post("/reset-password")
def reset_password(payload: ResetPayload, request: Request,
                   db: Db) -> dict:
    """
    Sets the new password and drops every session, including this one.

    No cookie is issued: after a reset the user signs in again. If the reset
    was triggered by someone else, that second step is where they stop.
    """
    _limit(token_limiter, request, "reset-password")
    try:
        service.reset_password(db, payload.token, payload.password,
                               ip=client_ip(request),
                               user_agent=request.headers.get("user-agent"))
    except service.InvalidToken as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"detail": "password changed: sign in again"}


# --- sessions ---------------------------------------------------------------

@router.get("/sessions")
def sessions(row: CurrentSession, db: Db) -> list[SessionRow]:
    """
    Where this account is signed in.

    IT IS THE ONLY WAY A USER CAN SEE A STOLEN COOKIE. The session is HttpOnly
    and revocable server-side, which protects it from a script on the page but
    says nothing to the person whose laptop was borrowed. A list with a time, a
    place and a device is the thing they can actually read an intrusion out of.
    """
    rows = service.active_sessions(db, row.user)
    return [SessionRow(
        id=str(r.id),
        created_at=r.created_at.isoformat(),
        last_seen_at=r.last_seen_at.isoformat(),
        expires_at=r.expires_at.isoformat(),
        ip=str(r.ip) if r.ip else None,
        user_agent=r.user_agent,
        current=r.id == row.id,
    ) for r in rows]


@router.get("/activity")
def activity(user: CurrentUser, db: Db) -> list[ActivityRow]:
    """
    What happened to this account lately: sign-ins, failures, password changes.

    The sessions list shows who is signed in NOW. It cannot show eight wrong
    passwords typed last night, and that is exactly the thing worth seeing
    before it becomes a ninth that works.
    """
    return [ActivityRow(
        kind=e.kind,
        outcome=e.outcome,
        at=e.at.isoformat(),
        ip=str(e.ip) if e.ip else None,
        user_agent=e.user_agent,
    ) for e in service.recent_activity(db, user)]


@router.post("/sessions/revoke-others")
def revoke_other_sessions(row: CurrentSession, request: Request, db: Db) -> dict[str, int]:
    """
    Sign out everywhere else, keeping the session asking.

    KEEPING THE CURRENT ONE IS THE POINT. Signing the user out of the page they
    are using to secure their account is how a security action becomes one
    nobody completes: they land on a login form and assume it failed.
    """
    _limit(session_admin_limiter, request, "revoke-others", key=str(row.user_id))
    count = service.revoke_all(db, row.user, keep=row.id)
    service.record_event(db, "sessions_revoked", "ok", user_id=row.user.id,
                         ip=client_ip(request), user_agent=request.headers.get("user-agent"),
                         details={"revoked": count})
    return {"revoked": count}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: uuid.UUID, row: CurrentSession, request: Request,
                   db: Db) -> dict[str, int]:
    """
    Sign out one other device, the one the user does not recognise.

    NOT THE CURRENT ONE: ending the session asking is sign-out, and it already
    has its own route that also clears the cookie. Doing it here would leave a
    dead cookie in the browser and a page that fails on its next request.

    An id that belongs to another account answers exactly like one that does
    not exist: 404, same body. Otherwise the route would confirm which session
    ids are live, for anyone signed in.
    """
    _limit(session_admin_limiter, request, "revoke-session", key=str(row.user_id))
    if session_id == row.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "this is the current session: use sign-out")
    if not service.revoke_one(db, row.user, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    service.record_event(db, "session_revoked", "ok", user_id=row.user.id,
                         ip=client_ip(request), user_agent=request.headers.get("user-agent"),
                         details={"session": str(session_id)})
    return {"revoked": 1}


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePayload,
                    row: CurrentSession,
                    request: Request,
                    db: Db) -> Response:
    """Every other session drops; the one doing the change survives."""
    _limit(password_change_limiter, request, "change-password", key=str(row.user_id))
    try:
        service.change_password(db, row.user, payload.current_password,
                                payload.new_password, current_session=row.id)
    except service.AccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
