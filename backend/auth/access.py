"""
The short-lived access token: a JWT, checked without the database.

TWO TOKENS, AND WHY (docs/adr/0003-jwt-accesso-breve.md). The session row in
Postgres is the REFRESH token: opaque, stored as a hash, revocable at once, at
most 24 hours old. From it, `/api/auth/refresh` mints this access token, valid
for `access_token_minutes` (15 by default). The data routes trust the token
alone — signature and expiry, no query — and that is the whole gain.

What it costs, said plainly: a session revoked, a password changed, an account
suspended still reaches the DATA routes until the access token in hand
expires. The account routes (`/me`, sessions, password) load the session row
as well, so there the revocation is immediate.

WHAT THE VERIFICATION INSISTS ON. Each item is a known way JWT goes wrong when
the library is left on its defaults:

- `algorithms=["HS256"]`, a list of one. Accepting the algorithm the token
  names is how `alg: none` and HS/RS confusion get in.
- `exp`, `iat`, `sub`, `sid` required. A token without `exp` would never
  expire, and PyJWT only checks `exp` if it is present.
- `aud` and `iss` fixed, and `typ` = "access". The emailed tokens are signed
  with the same secret; a token minted for one purpose must not open another.

THE KEY IS DERIVED, NOT THE SECRET ITSELF. HMAC of the secret with a fixed
label: the same `AI_NAPLES_SECRET_KEY` already signs the verify and reset links
(itsdangerous), and two schemes keyed by the very same bytes is the kind of
coupling that is harmless until it is not. Rotating the secret still kills
every access token at once, which is also the emergency brake.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import uuid
from dataclasses import dataclass

import jwt

from .settings import Settings

ALGORITHM = "HS256"
ISSUER = "ainaples"
AUDIENCE = "ainaples-api"
TYPE = "access"
# Clock skew between the machine that signs and the one that checks. They are
# the same process today; a few seconds is the honest margin if they split.
LEEWAY = dt.timedelta(seconds=10)


@dataclass(frozen=True)
class Principal:
    """
    Who is calling, as the access token says — no database row behind it.

    `permissions` are a snapshot taken when the token was minted. A permission
    granted or revoked shows up at the next refresh, at most
    `access_token_minutes` later.
    """

    user_id: uuid.UUID
    session_id: uuid.UUID
    permissions: frozenset[str]
    expires_at: dt.datetime

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _key(cfg: Settings) -> bytes:
    return hmac.new(cfg.secret_key.get_secret_value().encode(),
                    b"ainaples/access-token/v1", hashlib.sha256).digest()


def issue(user_id: uuid.UUID, session_id: uuid.UUID, permissions: set[str] | frozenset[str],
          cfg: Settings, *, at: dt.datetime | None = None) -> tuple[str, dt.datetime]:
    """A signed access token and its expiry."""
    at = at or dt.datetime.now(dt.UTC)
    expires = at + dt.timedelta(minutes=cfg.access_token_minutes)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "typ": TYPE,
        "sub": str(user_id),
        "sid": str(session_id),
        "perms": sorted(permissions),
        "iat": int(at.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, _key(cfg), algorithm=ALGORITHM), expires


def verify(token: str, cfg: Settings) -> Principal | None:
    """
    The caller, or `None` for every way a token can be bad.

    One answer for expired, forged, malformed and wrong-purpose, for the same
    reason the session check has one: the caller only needs to know "refresh
    or sign in", and the difference would only help someone probing.
    """
    try:
        claims = jwt.decode(
            token,
            _key(cfg),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "sub", "sid", "aud", "iss"]},
        )
        if claims.get("typ") != TYPE:
            return None
        perms = claims.get("perms", [])
        if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
            return None
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]),
            permissions=frozenset(perms),
            expires_at=dt.datetime.fromtimestamp(claims["exp"], dt.UTC),
        )
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        return None
