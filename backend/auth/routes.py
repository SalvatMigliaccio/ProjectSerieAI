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

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from . import service
from .deps import (
    Cfg,
    CurrentSession,
    CurrentUser,
    Db,
    Mailer,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
)
from .models import User
from .password import MIN_LENGTH, WeakPassword
from .ratelimit import reset_limiter, sign_in_limiter, signup_limiter

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


class Identity(BaseModel):
    """What the frontend needs to draw the interface. Never anything secret."""

    id: str
    email: EmailStr
    status: str
    roles: list[str]
    permissions: list[str]


def _identity(user: User) -> Identity:
    return Identity(
        id=str(user.id),
        email=user.email,
        status=user.status,
        roles=sorted(r.name for r in user.roles),
        permissions=sorted(service.permissions_of(user)),
    )


def _limit(limiter, request: Request, what: str) -> None:
    key = client_ip(request) or "unknown"
    if not limiter.allow(key):
        log.warning("rate limit hit on %s from %s", what, key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many requests: wait a few minutes before trying again",
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
    try:
        user = service.verify_email(db, payload.token)
    except service.InvalidToken as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    token, _ = service.open_session(db, user, cfg, ip=client_ip(request),
                                    user_agent=request.headers.get("user-agent"))
    set_session_cookie(response, token, cfg)
    return _identity(user)


# --- sign in and out --------------------------------------------------------

@router.post("/sign-in")
def sign_in(payload: Credentials, response: Response, request: Request,
            db: Db,
            cfg: Cfg) -> Identity:
    _limit(sign_in_limiter, request, "sign-in")
    try:
        token, _ = service.sign_in(db, payload.email, payload.password, cfg=cfg,
                                   ip=client_ip(request),
                                   user_agent=request.headers.get("user-agent"))
    except service.AccessDenied as exc:
        # 401 for every reason, with the message the service chose. The service
        # is what decides how vague to be; the route does not second-guess it.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    user = service.by_email(db, payload.email)
    set_session_cookie(response, token, cfg)
    return _identity(user)


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
    clear_session_cookie(response, cfg)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    try:
        service.reset_password(db, payload.token, payload.password,
                               ip=client_ip(request),
                               user_agent=request.headers.get("user-agent"))
    except service.InvalidToken as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"detail": "password changed: sign in again"}


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePayload,
                    row: CurrentSession,
                    db: Db) -> Response:
    """Every other session drops; the one doing the change survives."""
    try:
        service.change_password(db, row.user, payload.current_password,
                                payload.new_password, current_session=row.id)
    except service.AccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except WeakPassword as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
