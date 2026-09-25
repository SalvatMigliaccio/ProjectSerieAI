"""
The authentication endpoints over HTTP, end to end.

The service tests cover the logic; these cover what a browser actually sees:
status codes, cookies, and the fact that the data routes refuse anonymous
callers. Both matter, and neither substitutes for the other — the logic can be
right while the route leaks it, and the route can be right while the logic is
wrong.
"""

from __future__ import annotations

import pytest
from backend.api import app
from backend.auth import service
from backend.auth.db import request_session
from backend.auth.deps import mail_sender
from backend.auth.mail import InMemorySender
from backend.auth.models import EmailToken
from backend.auth.ratelimit import reset_limiter, sign_in_limiter, signup_limiter
from fastapi.testclient import TestClient

pytestmark = pytest.mark.requires_db

GOOD = "a sufficiently long password"


@pytest.fixture
def post() -> InMemorySender:
    return InMemorySender()


@pytest.fixture
def client(db, post):
    """
    A client bound to the rollback session and an in-memory mailbox.

    Overriding `request_session` is what keeps these tests from leaving rows
    behind: the route gets the same transaction the test holds, and the
    fixture rolls it back.
    """
    # Every limiter is per-process and shared, so a previous test's attempts
    # would otherwise count against this one and produce a 429 that looks like
    # a broken endpoint.
    for limiter in (sign_in_limiter, signup_limiter, reset_limiter):
        limiter.reset()

    app.dependency_overrides[request_session] = lambda: db
    app.dependency_overrides[mail_sender] = lambda: post
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _token(db, email: str, kind: str) -> str:
    """Rewrite the stored hash to one the test knows; see test_auth_service."""
    from backend.auth import tokens
    user = service.by_email(db, email)
    row = (db.query(EmailToken)
             .filter_by(user_id=user.id, kind=kind, used_at=None)
             .order_by(EmailToken.created_at.desc(), EmailToken.id.desc())
             .first())
    assert row is not None, f"no outstanding {kind} token for {email}"
    clear, hashed = tokens.generate()
    row.token_hash = hashed
    db.flush()
    return clear


def _register_and_verify(client, db, email: str) -> None:
    assert client.post("/api/auth/signup",
                       json={"email": email, "password": GOOD}).status_code == 202
    token = _token(db, email, "verify")
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200


# --- the gate ---------------------------------------------------------------

def test_health_stays_public(client) -> None:
    """A monitor that must authenticate reports the wrong thing when auth breaks."""
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("path", [
    "/api/track-record/2026-27",
    "/api/standings/2026-27",
    "/api/picks/2026-27",
    "/api/selections/2026-27",
    "/api/rounds/2026-27",
    "/api/status",
])
def test_data_routes_refuse_anonymous_callers(client, path: str) -> None:
    assert client.get(path).status_code == 401


def test_me_requires_a_session(client) -> None:
    assert client.get("/api/auth/me").status_code == 401


# --- signup and verification ------------------------------------------------

def test_signup_returns_202_and_sends_one_email(client, db, post) -> None:
    r = client.post("/api/auth/signup", json={"email": "http@example.com", "password": GOOD})
    assert r.status_code == 202
    assert post.last_to("http@example.com") is not None


def test_signup_answers_identically_for_a_taken_address(client, db, post) -> None:
    """
    Byte-for-byte identical, or the endpoint becomes an address checker.

    Status and body both: a different status alone is enough to enumerate.
    """
    first = client.post("/api/auth/signup", json={"email": "dup@example.com", "password": GOOD})
    second = client.post("/api/auth/signup", json={"email": "dup@example.com", "password": GOOD})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_a_weak_password_is_refused_with_a_usable_message(client) -> None:
    r = client.post("/api/auth/signup", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422
    assert "12" in r.text, "the message should say how long it must be"


def test_a_malformed_address_is_refused(client) -> None:
    r = client.post("/api/auth/signup", json={"email": "not-an-address", "password": GOOD})
    assert r.status_code == 422


def test_verification_signs_the_user_in(client, db) -> None:
    client.post("/api/auth/signup", json={"email": "verify@example.com", "password": GOOD})
    r = client.post("/api/auth/verify-email", json={"token": _token(db, "verify@example.com", "verify")})

    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "verify@example.com"
    assert body["status"] == "active"
    assert "customer" in body["roles"]
    assert "picks:read" in body["permissions"]


def test_an_invalid_verification_token_is_a_400(client) -> None:
    assert client.post("/api/auth/verify-email", json={"token": "made-up"}).status_code == 400


# --- sign in ----------------------------------------------------------------

def test_sign_in_sets_an_httponly_cookie(client, db) -> None:
    """
    HttpOnly is the whole reason the session lives in a cookie.

    If script can read it, an XSS bug hands over the account.
    """
    _register_and_verify(client, db, "cookie@example.com")
    client.post("/api/auth/sign-out")

    r = client.post("/api/auth/sign-in", json={"email": "cookie@example.com", "password": GOOD})
    assert r.status_code == 200

    raw = r.headers["set-cookie"].lower()
    assert "httponly" in raw, "the session cookie must not be readable from script"
    assert "samesite=lax" in raw


def test_a_signed_in_user_reaches_the_data(client, db) -> None:
    _register_and_verify(client, db, "dentro@example.com")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/track-record/2026-27").status_code in (200, 404, 503)
    assert client.get("/api/auth/me").status_code == 200


def test_wrong_credentials_are_a_401(client, db) -> None:
    _register_and_verify(client, db, "sbagliata@example.com")
    client.post("/api/auth/sign-out")
    r = client.post("/api/auth/sign-in",
                    json={"email": "sbagliata@example.com", "password": "wrong but long"})
    assert r.status_code == 401


def test_an_unknown_address_and_a_wrong_password_are_indistinguishable(client, db) -> None:
    _register_and_verify(client, db, "esiste@example.com")
    client.post("/api/auth/sign-out")

    unknown = client.post("/api/auth/sign-in",
                          json={"email": "ghost@example.com", "password": GOOD})
    wrong = client.post("/api/auth/sign-in",
                        json={"email": "esiste@example.com", "password": "wrong but long"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_an_unverified_account_cannot_sign_in(client) -> None:
    client.post("/api/auth/signup", json={"email": "pending@example.com", "password": GOOD})
    r = client.post("/api/auth/sign-in", json={"email": "pending@example.com", "password": GOOD})
    assert r.status_code == 401


# --- sign out ---------------------------------------------------------------

def test_sign_out_ends_access(client, db) -> None:
    _register_and_verify(client, db, "esci@example.com")
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/sign-out").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_sign_out_without_a_session_is_still_204(client) -> None:
    """Signing out is not a place to report errors: the caller wanted this state."""
    assert client.post("/api/auth/sign-out").status_code == 204


# --- password ---------------------------------------------------------------

def test_forgot_password_answers_the_same_for_unknown_addresses(client, db, post) -> None:
    known = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert post.last_to("ghost@example.com") is None, "no email to an address we do not have"


def test_the_full_reset_flow(client, db, post) -> None:
    _register_and_verify(client, db, "flow@example.com")

    assert client.post("/api/auth/forgot-password",
                       json={"email": "flow@example.com"}).status_code == 202
    assert post.last_to("flow@example.com") is not None

    r = client.post("/api/auth/reset-password",
                    json={"token": _token(db, "flow@example.com", "reset"),
                          "password": "a brand new long password"})
    assert r.status_code == 200

    # Every session died, including the one this client was holding.
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/sign-in",
                       json={"email": "flow@example.com", "password": GOOD}).status_code == 401
    assert client.post("/api/auth/sign-in",
                       json={"email": "flow@example.com",
                             "password": "a brand new long password"}).status_code == 200


def test_reset_with_a_weak_password_is_refused(client, db, post) -> None:
    _register_and_verify(client, db, "resetweak@example.com")
    client.post("/api/auth/forgot-password", json={"email": "resetweak@example.com"})
    r = client.post("/api/auth/reset-password",
                    json={"token": _token(db, "resetweak@example.com", "reset"),
                          "password": "short"})
    assert r.status_code == 422


def test_changing_the_password_keeps_you_signed_in(client, db) -> None:
    _register_and_verify(client, db, "change@example.com")
    r = client.post("/api/auth/change-password",
                    json={"current_password": GOOD, "new_password": "a brand new long password"})
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 200, \
        "changing your password must not sign you out of the page you are on"


def test_changing_the_password_needs_the_current_one(client, db) -> None:
    _register_and_verify(client, db, "changebad@example.com")
    r = client.post("/api/auth/change-password",
                    json={"current_password": "wrong but long",
                          "new_password": "a brand new long password"})
    assert r.status_code == 403


# --- rate limiting ----------------------------------------------------------

def test_signup_is_rate_limited(client) -> None:
    """
    Per-account lockout does not cover signup floods: there is no account yet.
    """
    codes = [client.post("/api/auth/signup",
                         json={"email": f"flood{i}@example.com", "password": GOOD}).status_code
             for i in range(signup_limiter.limit + 2)]
    assert 429 in codes, f"no rate limiting on signup: {codes}"


def test_forgot_password_is_rate_limited(client) -> None:
    """Otherwise it is a way to flood someone else's mailbox from nothing."""
    codes = [client.post("/api/auth/forgot-password",
                         json={"email": "target@example.com"}).status_code
             for _ in range(reset_limiter.limit + 2)]
    assert 429 in codes, f"no rate limiting on forgot-password: {codes}"
