"""
The access token (ADR 0003): what a JWT must refuse, and what refresh must do.

Two files' worth of promises are checked here. `access.verify` must refuse
every token that is not exactly one it would have issued — the list of JWT
failure modes in its docstring is the list of tests below. And the HTTP layer
must keep the property the two-token design rests on: the data routes need no
database, the account routes still stop a revoked session at once.

The verification tests need no Postgres; the HTTP ones reuse the fixtures of
`test_auth_http`.
"""

from __future__ import annotations

import datetime as dt
import uuid

import jwt
import pytest
from backend.auth import access
from backend.auth.settings import Settings
from pydantic import SecretStr

from tests.test_auth_http import GOOD, _register_and_verify, client, post  # noqa: F401

CFG = Settings(secret_key=SecretStr("k" * 64), access_token_minutes=15)


def _claims(**over) -> dict:
    now = dt.datetime.now(dt.UTC)
    base = {
        "iss": access.ISSUER, "aud": access.AUDIENCE, "typ": access.TYPE,
        "sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "perms": ["picks:read"],
        "iat": int(now.timestamp()), "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _sign(claims: dict, key: bytes | None = None, alg: str = "HS256") -> str:
    return jwt.encode(claims, key or access._key(CFG), algorithm=alg)


# --- what verify accepts ------------------------------------------------------

def test_a_token_it_issued_comes_back_as_the_same_caller() -> None:
    user, session = uuid.uuid4(), uuid.uuid4()
    token, expires = access.issue(user, session, {"picks:read", "data:read"}, CFG)
    p = access.verify(token, CFG)
    assert p is not None
    assert (p.user_id, p.session_id) == (user, session)
    assert p.permissions == {"picks:read", "data:read"}
    assert abs((p.expires_at - expires).total_seconds()) < 1


def test_it_lives_as_long_as_configured() -> None:
    at = dt.datetime.now(dt.UTC)
    _, expires = access.issue(uuid.uuid4(), uuid.uuid4(), set(), CFG, at=at)
    assert expires - at == dt.timedelta(minutes=15)


# --- what verify refuses: one test per known way JWT goes wrong ---------------

def test_an_expired_token_is_refused() -> None:
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    token, _ = access.issue(uuid.uuid4(), uuid.uuid4(), set(), CFG, at=past)
    assert access.verify(token, CFG) is None


def test_alg_none_is_refused() -> None:
    """The classic: an unsigned token naming `none` as its algorithm."""
    token = jwt.encode(_claims(), key=None, algorithm="none")
    assert access.verify(token, CFG) is None


def test_another_hmac_algorithm_is_refused() -> None:
    """Only HS256 is on the list; a token naming HS512 is not ours."""
    token = jwt.encode(_claims(), access._key(CFG), algorithm="HS512")
    assert access.verify(token, CFG) is None


def test_a_token_signed_with_another_key_is_refused() -> None:
    assert access.verify(_sign(_claims(), key=b"x" * 32), CFG) is None


def test_the_raw_secret_is_not_the_signing_key() -> None:
    """The key is derived: the secret that signs emailed links does not sign this."""
    raw = CFG.secret_key.get_secret_value().encode()
    assert access.verify(_sign(_claims(), key=raw), CFG) is None


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "sid", "aud", "iss"])
def test_a_missing_required_claim_is_refused(missing: str) -> None:
    """Without `exp` in the list, PyJWT would accept a token that never expires."""
    assert access.verify(_sign(_claims(**{missing: None})), CFG) is None


@pytest.mark.parametrize("claim,value", [
    ("aud", "someone-else"), ("iss", "someone-else"), ("typ", "reset"), ("typ", None),
])
def test_a_token_for_another_purpose_is_refused(claim: str, value) -> None:
    assert access.verify(_sign(_claims(**{claim: value})), CFG) is None


@pytest.mark.parametrize("perms", ["picks:read", [1, 2], {"a": 1}])
def test_malformed_permissions_are_refused(perms) -> None:
    """A string would iterate into one-letter permissions; refuse, do not coerce."""
    assert access.verify(_sign(_claims(perms=perms)), CFG) is None


def test_a_tampered_payload_is_refused() -> None:
    token, _ = access.issue(uuid.uuid4(), uuid.uuid4(), set(), CFG)
    head, body, sig = token.split(".")
    forged = _sign(_claims(perms=["users:manage"])).split(".")[1]
    assert access.verify(f"{head}.{forged[:-2]}xx.{sig}", CFG) is None
    assert access.verify("not.a.jwt", CFG) is None
    assert access.verify("", CFG) is None


# --- over HTTP -----------------------------------------------------------------

def test_sign_in_sets_both_cookies(client, db) -> None:  # noqa: F811
    _register_and_verify(client, db, "due@example.com")
    r = client.post("/api/auth/sign-in", json={"email": "due@example.com", "password": GOOD})
    assert r.status_code == 200
    cookies = r.headers.get_list("set-cookie")
    refresh = [c for c in cookies if c.startswith("ainaples_session=") and "Max-Age=0" not in c]
    token = [c for c in cookies if c.startswith("ainaples_access=")]
    assert refresh and token
    assert "Path=/api/auth" in refresh[0], "the refresh cookie travels to /api/auth only"
    assert "HttpOnly" in refresh[0] and "HttpOnly" in token[0]
    assert "Max-Age=900" in token[0]


def test_refresh_mints_a_new_access_token(client, db) -> None:  # noqa: F811
    _register_and_verify(client, db, "rinnova@example.com")
    client.cookies.delete("ainaples_access")
    assert client.get("/api/auth/me").status_code == 401, "no access token, no entry"

    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["access_expires_at"] < body["session_expires_at"]
    assert client.get("/api/auth/me").status_code == 200


def test_the_session_lasts_a_day_not_two_weeks(client, db) -> None:  # noqa: F811
    _register_and_verify(client, db, "giorno@example.com")
    body = client.post("/api/auth/refresh").json()
    lasts = (dt.datetime.fromisoformat(body["session_expires_at"])
             - dt.datetime.now(dt.UTC))
    assert dt.timedelta(hours=23) < lasts <= dt.timedelta(hours=24)


def test_refresh_without_a_session_is_401_and_clears_the_cookies(client) -> None:  # noqa: F811
    client.cookies.set("ainaples_session", "made-up", path="/api/auth")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    cleared = [c for c in r.headers.get_list("set-cookie") if "Max-Age=0" in c]
    assert any(c.startswith("ainaples_session=") for c in cleared)
    assert any(c.startswith("ainaples_access=") for c in cleared)


def test_a_revoked_session_cannot_refresh(client, db) -> None:  # noqa: F811
    """Where the 15-minute window closes: the next token is not issued."""
    email = "revocata@example.com"
    _register_and_verify(client, db, email)
    first = client.cookies.get("ainaples_session", path="/api/auth")
    client.post("/api/auth/sign-in", json={"email": email, "password": GOOD})
    client.post("/api/auth/sessions/revoke-others")  # kills the first session

    client.cookies.clear()
    client.cookies.set("ainaples_session", first, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401


def test_account_routes_stop_a_revoked_session_at_once(client, db) -> None:  # noqa: F811
    """
    The access token of a revoked session is still well signed and unexpired.
    The data routes would accept it for its few minutes; the account routes
    check the row as well and must not.
    """
    email = "subito@example.com"
    _register_and_verify(client, db, email)
    victim_token = client.cookies.get("ainaples_access")
    client.post("/api/auth/sign-in", json={"email": email, "password": GOOD})
    client.post("/api/auth/sessions/revoke-others")

    client.cookies.clear()
    client.cookies.set("ainaples_access", victim_token)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/sessions").status_code == 401


def test_a_public_route_says_401_for_a_dead_token_not_free_tier(client) -> None:  # noqa: F811
    """A lapsed subscriber must be told to refresh, not silently downgraded."""
    from backend.auth.settings import settings

    past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    stale, _ = access.issue(uuid.uuid4(), uuid.uuid4(), {"picks:read"}, settings(), at=past)
    assert client.get("/api/health").status_code == 200

    client.cookies.set("ainaples_access", stale)
    assert client.get("/api/picks/2026-27").status_code == 401
    client.cookies.clear()
    assert client.get("/api/picks/2026-27").status_code == 200, "no cookie is anonymous"


def test_sign_out_clears_both_cookies(client, db) -> None:  # noqa: F811
    _register_and_verify(client, db, "esce@example.com")
    r = client.post("/api/auth/sign-out")
    cleared = [c for c in r.headers.get_list("set-cookie") if "Max-Age=0" in c]
    assert any(c.startswith("ainaples_access=") for c in cleared)
    assert any(c.startswith("ainaples_session=") and "Path=/api/auth" in c for c in cleared)
    assert client.post("/api/auth/refresh").status_code == 401


def test_a_session_opened_under_the_old_ceiling_obeys_the_new_one(client, db) -> None:  # noqa: F811
    """A 14-day session opened before ADR 0003 must not outlive the 24 hours."""
    from backend.auth.models import UserSession

    _register_and_verify(client, db, "vecchia@example.com")
    sid = next(r["id"] for r in client.get("/api/auth/sessions").json() if r["current"])
    row = db.get(UserSession, uuid.UUID(sid))
    row.created_at = row.created_at - dt.timedelta(days=3)
    row.expires_at = row.created_at + dt.timedelta(days=14)
    db.flush()

    assert client.post("/api/auth/refresh").status_code == 401
