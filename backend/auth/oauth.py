"""
Sign in with Google: the protocol, and nothing else (ADR 0008).

OpenID Connect, authorization code flow with PKCE, run entirely server-side.
The browser only ever carries Google's one-time `code` through a redirect; the
token exchange happens here, with the client secret, and the ID token never
reaches JavaScript. What this module does NOT do is decide who gets in: it
returns verified claims, and `identities.py` decides what they open.

THE FLOW STATE LIVES IN A SIGNED COOKIE, NOT A TABLE. `state`, `nonce` and the
PKCE verifier have to survive one round trip to Google and back, from the same
browser. A short-lived HttpOnly cookie signed with a key derived for this one
purpose does exactly that, and binds the flow to the browser that started it:
a callback replayed from another browser has no cookie to match `state`
against. That is the login-CSRF defence, and the reason the cookie is
SameSite=Lax — Strict would not be sent on the top-level redirect back from
Google, and every sign-in would fail as a forgery.

WHAT IS CHECKED ON THE ID TOKEN, all of it, because each check closes a known
hole: the signature against Google's published keys (RS256 only: no `none`, no
HMAC confusion); `iss` is Google; `aud` is OUR client id — a token minted for
another site is a valid Google token too; `exp`/`iat`; and `nonce` equal to the
one we put in the flow, which is what stops an ID token being replayed.

The endpoints are constants rather than read from Google's discovery document:
they have not changed in years, and one fewer network call at sign-in is one
fewer thing that can fail there.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt

from .settings import Settings

log = logging.getLogger("auth.oauth")

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a public endpoint, not a secret
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

PROVIDERS = ("google",)
# What the flow is for. `link` and `reauth` need a signed-in session at both
# ends of the round trip; `sign_in` must not have one mattering.
INTENTS = ("sign_in", "link", "reauth")

FLOW_TYPE = "oauth-flow"
REAUTH_TYPE = "reauth"
ISSUER = "ainaples"
LEEWAY = dt.timedelta(seconds=30)

# A token response is a few kilobytes. A body far larger than that is not one,
# and reading it whole would let the other end decide how much memory we use
# (security rule 3).
MAX_TOKEN_RESPONSE = 64 * 1024
HTTP_TIMEOUT = 10.0


class OAuthError(Exception):
    """
    The flow failed. `code` is what the frontend shows a sentence for.

    The message is for the log; the code is for the user. Google's own error
    text never reaches the page: it can be anything, and it is not ours to
    display.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Flow:
    """What has to survive the trip to Google and back."""

    provider: str
    intent: str
    state: str
    nonce: str
    verifier: str
    next_path: str
    # The session that started a `link` or `reauth`. The callback must find
    # the same session, or a link could land on whoever is signed in by then.
    session_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Claims:
    """The verified facts about the person, as the provider states them."""

    provider: str
    subject: str
    email: str | None
    email_verified: bool


# --- keys and signed cookies ---------------------------------------------------

def _key(cfg: Settings, purpose: bytes) -> bytes:
    """One key per use, derived from the secret: a flow cookie is never an access token."""
    return hmac.new(cfg.secret_key.get_secret_value().encode(), purpose, hashlib.sha256).digest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def redirect_uri(cfg: Settings, provider: str) -> str:
    """THE one place the callback address is built. Must match the console exactly."""
    return f"{cfg.public_url.rstrip('/')}/api/auth/oauth/{provider}/callback"


def safe_next(path: str | None, default: str = "/dashboard") -> str:
    """
    A path inside this site, or the default.

    AN OPEN REDIRECT OTHERWISE. `next` comes from the query string, so anyone
    can write a link that signs a victim in through Google and then lands them
    on a lookalike page. Only a single leading slash is accepted: `//evil.com`
    is a protocol-relative URL, and a backslash is read as a slash by browsers.
    """
    if not path or not path.startswith("/") or path.startswith("//"):
        return default
    if "\\" in path or any(ord(c) < 0x20 for c in path):
        return default
    return path


def new_flow(provider: str, intent: str, next_path: str | None,
             session_id: uuid.UUID | None = None) -> Flow:
    if provider not in PROVIDERS:
        raise OAuthError("not_configured", f"unknown provider {provider}")
    if intent not in INTENTS:
        raise OAuthError("state", f"unknown intent {intent}")
    return Flow(provider=provider, intent=intent,
                state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                verifier=secrets.token_urlsafe(64), next_path=safe_next(next_path),
                session_id=session_id)


def encode_flow(flow: Flow, cfg: Settings, *, at: dt.datetime | None = None) -> str:
    at = at or dt.datetime.now(dt.UTC)
    claims = {
        "iss": ISSUER, "typ": FLOW_TYPE, "iat": at,
        "exp": at + dt.timedelta(minutes=cfg.oauth_flow_minutes),
        "prv": flow.provider, "int": flow.intent, "st": flow.state, "nn": flow.nonce,
        "pv": flow.verifier, "nx": flow.next_path,
        "sid": str(flow.session_id) if flow.session_id else None,
    }
    return jwt.encode(claims, _key(cfg, b"ainaples/oauth-flow/v1"), algorithm="HS256")


def decode_flow(token: str | None, cfg: Settings) -> Flow:
    """The flow from its cookie, or `OAuthError("state")` — expired, forged or absent alike."""
    if not token:
        raise OAuthError("state", "no flow cookie: the flow was not started in this browser")
    try:
        c = jwt.decode(token, _key(cfg, b"ainaples/oauth-flow/v1"), algorithms=["HS256"],
                       issuer=ISSUER, leeway=LEEWAY,
                       options={"require": ["exp", "iat", "iss"]})
        if c.get("typ") != FLOW_TYPE:
            raise OAuthError("state", "not a flow cookie")
        return Flow(provider=c["prv"], intent=c["int"], state=c["st"], nonce=c["nn"],
                    verifier=c["pv"], next_path=safe_next(c["nx"]),
                    session_id=uuid.UUID(c["sid"]) if c.get("sid") else None)
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        raise OAuthError("state", f"flow cookie rejected: {exc}") from exc


def issue_reauth(user_id: uuid.UUID, session_id: uuid.UUID, cfg: Settings,
                 *, at: dt.datetime | None = None) -> str:
    """The "proved it again, just now" mark, bound to one user AND one session."""
    at = at or dt.datetime.now(dt.UTC)
    return jwt.encode({
        "iss": ISSUER, "typ": REAUTH_TYPE, "iat": at,
        "exp": at + dt.timedelta(minutes=cfg.reauth_minutes),
        "sub": str(user_id), "sid": str(session_id),
    }, _key(cfg, b"ainaples/reauth/v1"), algorithm="HS256")


def reauth_valid(token: str | None, user_id: uuid.UUID, session_id: uuid.UUID,
                 cfg: Settings) -> bool:
    if not token:
        return False
    try:
        c = jwt.decode(token, _key(cfg, b"ainaples/reauth/v1"), algorithms=["HS256"],
                       issuer=ISSUER, leeway=LEEWAY,
                       options={"require": ["exp", "iat", "iss", "sub", "sid"]})
    except jwt.InvalidTokenError:
        return False
    return (c.get("typ") == REAUTH_TYPE and c.get("sub") == str(user_id)
            and c.get("sid") == str(session_id))


# --- talking to Google ---------------------------------------------------------

def authorization_url(flow: Flow, cfg: Settings) -> str:
    challenge = _b64url(hashlib.sha256(flow.verifier.encode()).digest())
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": redirect_uri(cfg, flow.provider),
        "response_type": "code",
        "scope": "openid email",
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Always show the account chooser: someone signed in to two Google
        # accounts must be able to pick, and a re-confirmation (`reauth`) that
        # passes silently would confirm nothing.
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTHORIZE}?{urlencode(params)}"


def exchange_code(code: str, flow: Flow, cfg: Settings,
                  client: httpx.Client | None = None) -> str:
    """Swap the one-time code for the ID token. Returns the raw ID token."""
    data = {
        "code": code,
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret.get_secret_value(),
        "redirect_uri": redirect_uri(cfg, flow.provider),
        "grant_type": "authorization_code",
        "code_verifier": flow.verifier,
    }
    owned = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT)
    try:
        r = client.post(GOOGLE_TOKEN_URL, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError("provider", f"token endpoint unreachable: {exc}") from exc
    finally:
        if owned:
            client.close()

    if len(r.content) > MAX_TOKEN_RESPONSE:
        raise OAuthError("provider", f"token response too large ({len(r.content)} bytes)")
    if "application/json" not in r.headers.get("content-type", ""):
        raise OAuthError("provider", f"token response is not JSON ({r.headers.get('content-type')})")
    try:
        body = r.json()
    except ValueError as exc:
        raise OAuthError("provider", "token response is not valid JSON") from exc
    if r.status_code != 200 or not isinstance(body, dict) or "id_token" not in body:
        # Google's `error` field only, never the description: it is logged, not shown.
        raise OAuthError("provider", f"token exchange refused: {r.status_code} "
                                     f"{body.get('error') if isinstance(body, dict) else ''}")
    return str(body["id_token"])


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    """One client per process: it caches Google's keys and refetches on rotation."""
    return jwt.PyJWKClient(GOOGLE_JWKS, cache_keys=True, lifespan=3600, timeout=int(HTTP_TIMEOUT))


def signing_key(id_token: str):
    """Google's public key for this token. Separate so tests can supply their own."""
    try:
        return _jwks().get_signing_key_from_jwt(id_token).key
    except jwt.PyJWKClientError as exc:
        raise OAuthError("provider", f"cannot fetch Google's signing keys: {exc}") from exc


def verify_id_token(id_token: str, flow: Flow, cfg: Settings) -> Claims:
    try:
        key = signing_key(id_token)
        c = jwt.decode(id_token, key, algorithms=["RS256"], audience=cfg.google_client_id,
                       leeway=LEEWAY, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.InvalidTokenError as exc:
        raise OAuthError("provider", f"ID token rejected: {exc}") from exc
    if c.get("iss") not in GOOGLE_ISSUERS:
        raise OAuthError("provider", f"ID token from an unexpected issuer: {c.get('iss')}")
    if not c.get("nonce") or not hmac.compare_digest(str(c["nonce"]), flow.nonce):
        raise OAuthError("state", "nonce mismatch: replayed or foreign ID token")
    return Claims(provider="google", subject=str(c["sub"]),
                  email=c.get("email"), email_verified=c.get("email_verified") is True)


def check_state(returned: str | None, flow: Flow) -> None:
    if not returned or not hmac.compare_digest(returned, flow.state):
        raise OAuthError("state", "state mismatch: callback not from the flow this browser started")
