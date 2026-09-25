"""
FastAPI dependencies: who is calling, and may they.

WHY THE COOKIE AND NOT AN `Authorization` HEADER. The token is the session;
anything JavaScript can read, an XSS can exfiltrate. An `HttpOnly` cookie is
unreadable from script, so a cross-site scripting bug stops short of handing
over the account. The price is CSRF, which is paid for by `SameSite` plus the
fact that every state-changing route lives under `/api/auth/`.

WHY PERMISSIONS AND NOT ROLES AT THE CALL SITE. A route that asks for
`require_permission("picks:read")` keeps working when the role layout changes;
one that asks for `role == "customer"` has to be found and edited every time a
tier is added. Roles are how permissions are granted, not what is checked.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from . import mail as mail_mod
from . import service
from .db import request_session
from .models import User, UserSession
from .settings import Settings, settings


def config(request: Request) -> Settings:
    return settings()


def client_ip(request: Request) -> str | None:
    """
    The caller's address, or `None` when it is not one.

    THE VALIDATION IS NOT DEFENSIVE PROGRAMMING, IT IS A BUG THAT HAPPENED.
    `auth_events.ip` is an `INET` column, so a value Postgres cannot parse
    makes the INSERT fail — and because the audit row is written inside the
    sign-in transaction, an unparseable address does not lose a log line, it
    takes **authentication** down. Starlette's TestClient reports the host as
    the literal string `testclient`, which is how this surfaced; a reverse
    proxy passing a hostname, or a unix socket, would do the same in
    production.

    Best-effort context must never be load-bearing: if it is not an address,
    it is `None` and the event is still recorded.

    `X-Forwarded-For` is deliberately NOT read here. It is trivially forged by
    the client, so trusting it without a proxy in front is a way to poison the
    audit log and dodge any per-address limit. Reading it is a deployment
    decision, and phase 3 will make it an explicit setting rather than
    something this function guesses.
    """
    if request.client is None:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


# Declared as Annotated aliases rather than `= Depends(...)` defaults: it is
# FastAPI's current recommended form, it keeps the signatures readable, and a
# call in a default argument is a real footgun everywhere except here.
Db = Annotated[Session, Depends(request_session)]
Cfg = Annotated["Settings", Depends(config)]


def mail_sender(cfg: Cfg) -> mail_mod.Sender:
    """
    How email leaves the process.

    A dependency rather than a module-level default so tests can swap in
    `InMemorySender` and read what was sent. Without the seam, testing the
    signup route means either talking to a real relay or asserting nothing
    about the message.
    """
    return mail_mod.sender(cfg)


Mailer = Annotated[mail_mod.Sender, Depends(mail_sender)]


def current_session(request: Request, db: Db, cfg: Cfg) -> UserSession:
    token = request.cookies.get(cfg.cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")

    row = service.session_from_token(db, token, cfg)
    if row is None:
        # One answer for expired, revoked, idle-too-long and never-existed.
        # Telling them apart would say whether a stolen token was ever real.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session not valid")
    return row


CurrentSession = Annotated[UserSession, Depends(current_session)]


def current_user(row: CurrentSession) -> User:
    return row.user


CurrentUser = Annotated[User, Depends(current_user)]


def require_permission(name: str) -> Callable[..., User]:
    """
    A dependency that lets the request through only with that permission.

    403 and not 404: the caller is authenticated and we already told them so
    at sign-in, therefore hiding the existence of the resource buys nothing
    and costs a confusing error.
    """

    def guard(user: CurrentUser) -> User:
        if not service.has_permission(user, name):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"missing permission: {name}")
        return user

    return guard


def set_session_cookie(response: Response, token: str, cfg: Settings) -> None:
    """
    HttpOnly, SameSite=Lax, Secure when configured.

    No `max_age`: it is a session cookie, so closing the browser drops it. The
    real deadlines live server-side, where the client cannot move them — a
    cookie lifetime is a hint to the browser, never an expiry you can rely on.
    """
    response.set_cookie(
        key=cfg.cookie_name,
        value=token,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        domain=cfg.cookie_domain,
        path="/",
    )


def clear_session_cookie(response: Response, cfg: Settings) -> None:
    response.delete_cookie(
        key=cfg.cookie_name,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        domain=cfg.cookie_domain,
        path="/",
    )
