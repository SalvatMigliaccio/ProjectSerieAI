"""
FastAPI dependencies: who is calling, and may they.

TWO TOKENS SINCE ADR 0003. A JWT valid for minutes (`access.py`), which the
data routes check without touching the database, and the session row in
Postgres as the refresh token, valid for a day and revocable at once.

WHY COOKIES AND NOT AN `Authorization` HEADER. Both tokens are credentials;
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

from . import access, service
from . import mail as mail_mod
from .access import Principal
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

    `X-Forwarded-For` IS NOT READ HERE, AND STILL SHOULD NOT BE. Whether that
    header can be believed depends on what is in front of the process, which is
    a deployment fact and not something this function can know: read
    unconditionally it is a way to poison the audit log and dodge every
    per-address limit, since any client can send it.

    The trust lives one layer down, where it belongs. uvicorn's proxy-headers
    middleware rewrites the socket address from the header, but only for the
    addresses in `FORWARDED_ALLOW_IPS` — set in `compose.prod.yaml` to the
    Caddy container and nothing else. So `request.client.host` here is the real
    caller behind a configured proxy, and the raw socket peer everywhere else,
    with no branch in this file.
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


def current_principal(request: Request, cfg: Cfg) -> Principal:
    """
    The caller according to the access token: signature and expiry, no query.

    401 for every way the token can fail, including its absence. The frontend
    reads a 401 as "call /api/auth/refresh once, then retry" — so an expired
    access token costs one round trip, not a sign-in screen.
    """
    token = request.cookies.get(cfg.access_cookie_name)
    principal = access.verify(token, cfg) if token else None
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def current_session(principal: CurrentPrincipal, db: Db, cfg: Cfg) -> UserSession:
    """
    The session row behind the access token, still alive. For account routes.

    THE ACCOUNT ROUTES DO NOT TRUST THE TOKEN ALONE. Listing sessions, changing
    the password, revoking a device: these are where a revoked session must stop
    AT ONCE, not when its access token lapses. So here the row is loaded and
    checked again — revoked, expired, idle, account suspended, all as at
    refresh — and the 15-minute window of ADR 0003 closes for them.
    """
    row = service.live_session(db, principal.session_id, principal.user_id, cfg)
    if row is None:
        # One answer for expired, revoked, idle-too-long and never-existed.
        # Telling them apart would say whether a stolen token was ever real.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session not valid")
    return row


CurrentSession = Annotated[UserSession, Depends(current_session)]


def current_user(row: CurrentSession) -> User:
    return row.user


CurrentUser = Annotated[User, Depends(current_user)]


def viewer(request: Request, cfg: Cfg) -> Principal | None:
    """
    The signed-in caller, or `None` — never a 401.

    THE FREE TIER NEEDS A THIRD ANSWER. `current_principal` knows two: signed
    in, or refused. A route that is public but shows MORE to a subscriber needs
    to ask who is there without turning "nobody" into an error, which is
    exactly the shape of the tiers in `docs/MODELLO_DI_BUSINESS.md`: played
    matches are everyone's, the round that has not kicked off yet is the
    product.

    No database: the permissions travel in the token.

    NO COOKIE IS ANONYMOUS; A DEAD COOKIE IS 401. A subscriber whose access
    token lapsed between two refreshes would otherwise get the free tier, with
    a 200 and nothing to say a part is missing — a paywall that quietly closes
    on a paying customer. A 401 is what makes the frontend refresh and retry;
    if the refresh fails it clears both cookies, and the retry is then
    genuinely anonymous.
    """
    if not request.cookies.get(cfg.access_cookie_name):
        return None
    return current_principal(request, cfg)


Viewer = Annotated["Principal | None", Depends(viewer)]


def may_see_upcoming(principal: Principal | None) -> bool:
    """Whether this viewer gets matches that have not been played yet."""
    return principal is not None and principal.can("picks:read")


def require_permission(name: str) -> Callable[..., Principal]:
    """
    A dependency that lets the request through only with that permission.

    403 and not 404: the caller is authenticated and we already told them so
    at sign-in, therefore hiding the existence of the resource buys nothing
    and costs a confusing error.
    """

    def guard(principal: CurrentPrincipal) -> Principal:
        if not principal.can(name):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"missing permission: {name}")
        return principal

    return guard


# The refresh cookie travels only to /api/auth: the data routes never read it,
# so there is no reason to send it with every poll of the dashboard. Before ADR
# 0003 it lived at "/", and those cookies are still in browsers: every place
# that sets or clears it also clears the old path, or the two would coexist
# under one name and the server would read whichever the browser sent first.
REFRESH_PATH = "/api/auth"
LEGACY_PATH = "/"


def _cookie(response: Response, cfg: Settings, key: str, value: str, path: str,
            max_age: int | None = None) -> None:
    response.set_cookie(
        key=key, value=value, max_age=max_age, path=path,
        httponly=True, secure=cfg.cookie_secure, samesite="lax", domain=cfg.cookie_domain,
    )


def _drop(response: Response, cfg: Settings, key: str, path: str) -> None:
    response.delete_cookie(
        key=key, path=path,
        httponly=True, secure=cfg.cookie_secure, samesite="lax", domain=cfg.cookie_domain,
    )


def set_session_cookie(response: Response, token: str, cfg: Settings) -> None:
    """
    The refresh token: HttpOnly, SameSite=Lax, Secure when configured.

    No `max_age`: it is a session cookie, so closing the browser drops it. The
    real deadlines live server-side, where the client cannot move them — a
    cookie lifetime is a hint to the browser, never an expiry you can rely on.
    """
    _drop(response, cfg, cfg.cookie_name, LEGACY_PATH)
    _cookie(response, cfg, cfg.cookie_name, token, REFRESH_PATH)


def set_access_cookie(response: Response, token: str, cfg: Settings) -> None:
    """
    The access token, at "/" because every data route reads it.

    `max_age` equal to the token's life, unlike the refresh cookie: a browser
    that keeps an expired JWT only sends a dead credential and earns a 401 it
    could have skipped. The expiry that counts is still `exp`, inside the
    signed token.
    """
    _cookie(response, cfg, cfg.access_cookie_name, token, "/",
            max_age=cfg.access_token_minutes * 60)


def clear_session_cookie(response: Response, cfg: Settings) -> None:
    """Both tokens, and the refresh cookie at its old path as well."""
    _drop(response, cfg, cfg.cookie_name, REFRESH_PATH)
    _drop(response, cfg, cfg.cookie_name, LEGACY_PATH)
    _drop(response, cfg, cfg.access_cookie_name, "/")
