"""
The HTTP side of "Sign in with Google" (ADR 0008).

Under `/api/auth`, the prefix that may write (ADR 0004): the callback opens
sessions and links identities in Postgres. The track record is out of reach
here as everywhere else.

THE CALLBACK NEVER ANSWERS WITH AN ERROR PAGE. It is a browser navigation
coming back from Google, not a fetch: a JSON 400 there is a blank screen with
`{"detail": ...}` on it. Every outcome is a redirect to the frontend, with
either the session cookies set or `?oauth_error=<code>` for the page to turn
into a sentence. The code is one of a fixed list; the details go to the log
and the audit table, never into the URL.

THE FLOW COOKIE IS CLEARED ON EVERY CALLBACK, success or not. A flow is single
use: leaving it would let the same `state` be presented twice.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import access, identities, oauth, service
from .deps import (
    Cfg,
    CurrentSession,
    Db,
    clear_oauth_cookie,
    client_ip,
    set_access_cookie,
    set_oauth_cookie,
    set_reauth_cookie,
    set_session_cookie,
)
from .models import UserSession
from .ratelimit import oauth_limiter, session_admin_limiter
from .settings import Settings

log = logging.getLogger("auth.oauth_routes")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Where each intent lands when it fails, unless the flow names a better page.
SIGN_IN_PAGE = "/sign-in"
SECURITY_PAGE = "/account/sicurezza"


class Providers(BaseModel):
    """Which buttons the sign-in page should draw. Nothing secret: no client id."""

    google: bool
    apple: bool


class LinkedIdentity(BaseModel):
    """
    One provider tied to this account, as its owner sees it.

    No `subject`: it identifies the person at Google, and nothing on the page
    needs it. The email is the one Google had at link time, shown so the owner
    recognises WHICH Google account it is.
    """

    provider: str
    email: str | None = None
    linked_at: str
    last_used_at: str | None = None


def _enabled(provider: str, cfg: Settings) -> None:
    """404 for an unknown or unconfigured provider: off is off, not broken."""
    if provider != "google" or not cfg.google_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sign-in provider not available")


def _limit(request: Request) -> bool:
    key = client_ip(request) or "unknown"
    if oauth_limiter.allow(key):
        return True
    log.warning("rate limit hit on oauth from %s", key)
    return False


def _to(cfg: Settings, path: str, **params: str) -> RedirectResponse:
    """
    A redirect to the frontend. 303: the browser follows it with a GET, always.

    THE PAGE GOES AFTER `#`. The frontend uses a HashRouter (App.tsx), the same
    shape as the emailed links (`mail.py`): `/#/dashboard`, not `/dashboard`.
    A real path still serves the app, but the router reads an empty hash and
    the `?oauth_error=` the page needed is lost.
    """
    target = f"{cfg.public_url.rstrip('/')}/#{oauth.safe_next(path)}"
    if params:
        target += ("&" if "?" in target else "?") + urlencode(params)
    out = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    # The session cookies and the flow cookie travel on this response, and
    # Referrer-Policy keeps the `code` in the callback URL out of any request
    # the landing page makes.
    out.headers["Referrer-Policy"] = "no-referrer"
    out.headers["Cache-Control"] = "no-store"
    return out


def _session(request: Request, db, cfg: Settings) -> UserSession | None:
    """
    The signed-in session, from the REFRESH cookie.

    Not the access token: a link starts with a full-page navigation that may
    come after the 15-minute access token has lapsed, and the refresh cookie
    is what the server checks against the database anyway. Its path is
    `/api/auth`, so it reaches both ends of the flow.
    """
    token = request.cookies.get(cfg.cookie_name)
    return service.session_from_token(db, token, cfg) if token else None


@router.get("/providers")
def providers(cfg: Cfg) -> Providers:
    return Providers(google=cfg.google_enabled, apple=False)


@router.get("/oauth/{provider}/start")
def start(provider: str, request: Request, db: Db, cfg: Cfg,
          intent: str = "sign_in", next: str | None = None) -> RedirectResponse:
    """
    Send the browser to Google, carrying a fresh signed flow in a cookie.

    `link` and `reauth` need a signed-in session, recorded in the flow: the
    callback will insist on finding the same one.
    """
    _enabled(provider, cfg)
    fallback = SIGN_IN_PAGE if intent == "sign_in" else SECURITY_PAGE
    if intent not in oauth.INTENTS:
        return _to(cfg, fallback, oauth_error="state")
    if not _limit(request):
        return _to(cfg, fallback, oauth_error="rate_limited")

    session_id = None
    if intent != "sign_in":
        row = _session(request, db, cfg)
        if row is None:
            return _to(cfg, SIGN_IN_PAGE, oauth_error="session")
        session_id = row.id

    landing = "/dashboard" if intent == "sign_in" else SECURITY_PAGE
    flow = oauth.new_flow(provider, intent, next or landing, session_id)
    out = RedirectResponse(oauth.authorization_url(flow, cfg),
                           status_code=status.HTTP_303_SEE_OTHER)
    out.headers["Cache-Control"] = "no-store"
    set_oauth_cookie(out, oauth.encode_flow(flow, cfg), cfg)
    return out


@router.get("/oauth/{provider}/callback")
def callback(provider: str, request: Request, db: Db, cfg: Cfg,
             code: str | None = None, state: str | None = None,
             error: str | None = None) -> RedirectResponse:
    """Google sends the browser back here. Every branch ends in a redirect."""
    _enabled(provider, cfg)
    ip, ua = client_ip(request), request.headers.get("user-agent")

    try:
        flow = oauth.decode_flow(request.cookies.get(cfg.oauth_cookie_name), cfg)
    except oauth.OAuthError as exc:
        log.info("oauth callback without a usable flow: %s", exc)
        return _finish(_to(cfg, SIGN_IN_PAGE, oauth_error="state"), cfg)

    fail_page = SIGN_IN_PAGE if flow.intent == "sign_in" else flow.next_path

    def fail(code_: str) -> RedirectResponse:
        return _finish(_to(cfg, fail_page, oauth_error=code_), cfg)

    if not _limit(request):
        return fail("rate_limited")
    if error:
        # `access_denied` is the person pressing "Cancel" at Google: not a fault.
        log.info("oauth returned an error: %s", error)
        return fail("cancelled" if error == "access_denied" else "provider")

    try:
        oauth.check_state(state, flow)
        if flow.provider != provider:
            raise oauth.OAuthError("state", "flow started for another provider")
        if not code:
            raise oauth.OAuthError("provider", "callback without a code")
        claims = oauth.verify_id_token(oauth.exchange_code(code, flow, cfg), flow, cfg)
    except oauth.OAuthError as exc:
        log.warning("oauth %s failed: %s", flow.intent, exc)
        service.record_event(db, "sign_in" if flow.intent == "sign_in" else flow.intent,
                             "ko", ip=ip, user_agent=ua,
                             details={"via": provider, "cause": f"oauth_{exc.code}"})
        return fail(exc.code)

    if flow.intent == "sign_in":
        try:
            token, row = identities.sign_in(db, claims, cfg=cfg, ip=ip, user_agent=ua)
        except service.AccessDenied as exc:
            return fail(exc.reason)
        out = _to(cfg, flow.next_path)
        set_session_cookie(out, token, cfg)
        jwt_token, _ = access.issue(row.user_id, row.id, service.permissions_of(row.user), cfg)
        set_access_cookie(out, jwt_token, cfg)
        return _finish(out, cfg)

    # `link` and `reauth`: the session that started the flow must still be the
    # one in this browser. Otherwise a link would land on whoever signed in
    # between the two ends.
    row = _session(request, db, cfg)
    if row is None or row.id != flow.session_id:
        return fail("session")
    try:
        if flow.intent == "link":
            identities.link(db, row.user, claims, ip=ip, user_agent=ua)
            return _finish(_to(cfg, flow.next_path, linked=provider), cfg)
        identities.confirms(db, row.user, claims, ip=ip, user_agent=ua)
    except service.AccessDenied as exc:
        return fail(exc.reason)
    out = _to(cfg, flow.next_path, reauth=provider)
    set_reauth_cookie(out, oauth.issue_reauth(row.user_id, row.id, cfg), cfg)
    return _finish(out, cfg)


def _finish(response: RedirectResponse, cfg: Settings) -> RedirectResponse:
    clear_oauth_cookie(response, cfg)
    return response


@router.get("/identities")
def linked(row: CurrentSession) -> list[LinkedIdentity]:
    return [LinkedIdentity(
        provider=i.provider,
        email=i.email,
        linked_at=i.created_at.isoformat(),
        last_used_at=i.last_used_at.isoformat() if i.last_used_at else None,
    ) for i in sorted(row.user.identities, key=lambda i: i.provider)]


@router.post("/identities/{provider}/unlink")
def unlink(provider: str, row: CurrentSession, request: Request, db: Db) -> dict[str, int]:
    """409 when it is the account's last way in; `unlinked: 0` when it was not linked."""
    key = str(row.user_id)
    if not session_admin_limiter.allow(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many requests: wait a few minutes before trying again",
            headers={"Retry-After": str(session_admin_limiter.retry_after(key))},
        )
    try:
        done = identities.unlink(db, row.user, provider, ip=client_ip(request),
                                 user_agent=request.headers.get("user-agent"))
    except service.AccessDenied as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"unlinked": int(done)}
