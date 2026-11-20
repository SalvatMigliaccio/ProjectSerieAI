"""
The authenticator-app second factor (TOTP), over HTTP.

The "phone" is pyotp with the secret the setup returned, and the clock is moved
by hand: TOTP depends on time, and a test that waited 30 seconds would be a
test nobody runs.

WHAT IS PROTECTED, and why each one would fail silently:
- a stolen session that can turn the app on owns the account (the owner can no
  longer sign in without a phone they do not have);
- a code accepted twice is a code that works for whoever read it;
- a secret stored in the clear lets a database dump generate codes;
- a fallback to the emailed code would undo the reason the app exists;
- a recovery code that works twice, or that can mint new ones, is a second
  password nobody rotates.
"""

# The fixtures `client`, `post`, `cfg`, `google` are imported from the other auth
# test modules and then requested by name: ruff reads that as a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from backend.auth import totp
from backend.auth.models import MfaRecoveryCode, User
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_auth_mfa import GOOD, _account, _app, cfg, client, post  # noqa: F401
from tests.test_auth_oauth import google  # noqa: F401

pytestmark = pytest.mark.requires_db

T0 = 1_800_000_000.0   # a fixed instant, aligned to nothing in particular


@pytest.fixture
def clock(monkeypatch):
    """The TOTP clock, moved by hand. `clock.tick()` = the next 30-second step."""
    class Clock:
        t = T0

        def tick(self, steps: int = 1) -> None:
            self.t += 30 * steps

    c = Clock()
    monkeypatch.setattr(totp, "_clock", lambda: c.t)
    return c


def _phone(secret: str, clock) -> str:
    return pyotp.TOTP(secret.replace(" ", "")).at(clock.t)


def _setup(client) -> str:
    r = client.post("/api/auth/mfa/totp/setup")
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def _turn_on(client, clock, password: str = GOOD) -> tuple[str, list[str]]:
    secret = _setup(client)
    r = client.post("/api/auth/mfa/totp/confirm",
                    json={"code": _phone(secret, clock), "password": password})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _with_app(client, db, post, clock, email: str) -> tuple[str, list[str]]:
    _account(client, db, post, email)
    secret, codes = _turn_on(client, clock)
    client.post("/api/auth/sign-out")
    clock.tick()
    return secret, codes


# --- setting it up ---------------------------------------------------------------

def test_setup_gives_a_qr_code_and_the_secret_to_type(client, db, post, clock) -> None:
    _account(client, db, post, "setup@example.com")
    r = client.post("/api/auth/mfa/totp/setup").json()
    assert re.fullmatch(r"([A-Z2-7]{4} ){7}[A-Z2-7]{4}", r["secret"])
    uri = urlparse(r["otpauth_uri"])
    assert uri.scheme == "otpauth" and uri.netloc == "totp"
    assert parse_qs(uri.query)["issuer"] == ["MatchPoint"]
    assert r["qr"].startswith("data:image/svg+xml;base64,")
    assert client.get("/api/auth/me").json()["mfa_method"] is None, "setup alone turns nothing on"


def test_the_secret_is_stored_encrypted(client, db, post, clock) -> None:
    _account(client, db, post, "cifrato@example.com")
    secret = _setup(client).replace(" ", "")
    stored = db.scalar(select(User.totp_secret).where(User.email == "cifrato@example.com"))
    assert stored.startswith("v1:") and secret not in stored


def test_a_stolen_session_cannot_turn_it_on(client, db, post, clock) -> None:
    """Without the password, a session could install its own app and lock the owner out."""
    _account(client, db, post, "rubata@example.com")
    secret = _setup(client)
    for password in (None, "not the password"):
        r = client.post("/api/auth/mfa/totp/confirm",
                        json={"code": _phone(secret, clock), "password": password})
        assert r.status_code == 403
    assert client.get("/api/auth/me").json()["mfa_method"] is None


def test_turning_it_on_needs_a_right_code_too(client, db, post, clock) -> None:
    _account(client, db, post, "codice@example.com")
    _setup(client)
    r = client.post("/api/auth/mfa/totp/confirm", json={"code": "000000", "password": GOOD})
    assert r.status_code == 400


def test_turning_it_on_gives_ten_recovery_codes_once(client, db, post, clock) -> None:
    _account(client, db, post, "riserva@example.com")
    _, codes = _turn_on(client, clock)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(re.fullmatch(r"[2-9A-HJKMNP-Z]{5}-[2-9A-HJKMNP-Z]{5}", c) for c in codes)
    stored = db.scalars(select(MfaRecoveryCode.code_hash)).all()
    assert not any(c.replace("-", "") in h for c in codes for h in stored)
    status = client.get("/api/auth/mfa/status").json()
    assert (status["method"], status["recovery_codes_left"]) == ("totp", 10)
    assert status["enabled_at"]


# --- signing in with it -------------------------------------------------------------

def test_sign_in_asks_the_app_and_sends_no_email(client, db, post, clock) -> None:
    secret, _ = _with_app(client, db, post, clock, "app@example.com")
    before = len(post.sent)
    r = client.post("/api/auth/sign-in", json={"email": "app@example.com", "password": GOOD})
    assert r.status_code == 202 and r.json() == {"mfa_required": True, "method": "totp",
                                                 "sent_to": None}
    assert len(post.sent) == before, "no email: the code is on the phone"
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/mfa/verify", json={"code": _phone(secret, clock)}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_there_is_no_email_fallback(client, db, post, clock) -> None:
    _with_app(client, db, post, clock, "fallback@example.com")
    client.post("/api/auth/sign-in", json={"email": "fallback@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/resend").status_code == 400


def test_a_code_is_accepted_once(client, db, post, clock) -> None:
    secret, _ = _with_app(client, db, post, clock, "replay@example.com")
    code = _phone(secret, clock)
    client.post("/api/auth/sign-in", json={"email": "replay@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": code}).status_code == 200
    client.post("/api/auth/sign-out")
    client.post("/api/auth/sign-in", json={"email": "replay@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": code}).status_code == 400, \
        "the same code, still inside its window, must not open a second session"


def test_one_step_of_clock_drift_is_tolerated_two_are_not(client, db, post, clock) -> None:
    secret, _ = _with_app(client, db, post, clock, "deriva@example.com")
    old = _phone(secret, clock)
    clock.tick(1)
    client.post("/api/auth/sign-in", json={"email": "deriva@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": old}).status_code == 200
    client.post("/api/auth/sign-out")

    clock.tick(1)
    stale = _phone(secret, clock)
    clock.tick(2)
    client.post("/api/auth/sign-in", json={"email": "deriva@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": stale}).status_code == 400


def test_a_recovery_code_works_once(client, db, post, clock) -> None:
    _, codes = _with_app(client, db, post, clock, "persotelefono@example.com")
    client.post("/api/auth/sign-in", json={"email": "persotelefono@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": codes[0].lower()}).status_code == 200
    assert client.get("/api/auth/mfa/status").json()["recovery_codes_left"] == 9
    client.post("/api/auth/sign-out")
    client.post("/api/auth/sign-in", json={"email": "persotelefono@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": codes[0]}).status_code == 400


def test_google_sign_in_asks_the_app_too(client, db, post, clock, google) -> None:
    secret, _ = _with_app(client, db, post, clock, "gapp@gmail.com")
    google.claims = {"sub": "g-app", "email": "gapp@gmail.com", "email_verified": True}
    start = client.get("/api/auth/oauth/google/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/api/auth/oauth/google/callback", params={"code": "c", "state": state})
    assert client.get("/api/auth/mfa/pending").json()["method"] == "totp"
    assert client.post("/api/auth/mfa/verify", json={"code": _phone(secret, clock)}).status_code == 200


# --- an account without a password ---------------------------------------------------

def test_without_a_password_turning_it_on_needs_google(client, db, post, clock, google) -> None:
    google.claims = {"sub": "g-nopw", "email": "nopw@gmail.com", "email_verified": True}
    start = client.get("/api/auth/oauth/google/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/api/auth/oauth/google/callback", params={"code": "c", "state": state})

    secret = _setup(client)
    r = client.post("/api/auth/mfa/totp/confirm", json={"code": _phone(secret, clock)})
    assert r.status_code == 403, "a session alone must not turn it on"

    start = client.get("/api/auth/oauth/google/start", params={"intent": "reauth"})
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/api/auth/oauth/google/callback", params={"code": "c", "state": state})
    r = client.post("/api/auth/mfa/totp/confirm", json={"code": _phone(secret, clock)})
    assert r.status_code == 200, r.text


# --- turning it off and the recovery codes -------------------------------------------

def _signed_in_with_app(client, db, post, clock, email):
    secret, codes = _with_app(client, db, post, clock, email)
    client.post("/api/auth/sign-in", json={"email": email, "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": _phone(secret, clock)}).status_code == 200
    clock.tick()
    return secret, codes


def test_turning_it_off_needs_the_app_and_leaves_nothing_behind(client, db, post, clock) -> None:
    secret, _ = _signed_in_with_app(client, db, post, clock, "spegniapp@example.com")
    before = len(post.sent)
    r = client.post("/api/auth/mfa/disable/start")
    assert r.json()["method"] == "totp" and len(post.sent) == before
    assert client.post("/api/auth/mfa/disable/confirm", json={"code": "000000"}).status_code == 400
    client.post("/api/auth/mfa/disable/start")
    r = client.post("/api/auth/mfa/disable/confirm", json={"code": _phone(secret, clock)})
    assert r.status_code == 200 and r.json()["mfa_method"] is None

    user = db.scalar(select(User).where(User.email == "spegniapp@example.com"))
    assert user.totp_secret is None and user.totp_last_step is None
    assert db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)).all() == []


def test_new_recovery_codes_need_the_app_not_a_recovery_code(client, db, post, clock) -> None:
    secret, codes = _signed_in_with_app(client, db, post, clock, "nuovicodici@example.com")
    assert client.post("/api/auth/mfa/totp/recovery-codes",
                       json={"code": codes[1]}).status_code == 400
    r = client.post("/api/auth/mfa/totp/recovery-codes", json={"code": _phone(secret, clock)})
    fresh = r.json()["recovery_codes"]
    assert set(fresh).isdisjoint(codes)
    client.post("/api/auth/sign-out")
    client.post("/api/auth/sign-in", json={"email": "nuovicodici@example.com", "password": GOOD})
    assert client.post("/api/auth/mfa/verify", json={"code": codes[2]}).status_code == 400, \
        "the old recovery codes must stop working"


def test_a_changed_key_fails_loudly_not_silently(client, db, post, clock, cfg) -> None:
    secret, _ = _with_app(client, db, post, clock, "chiave@example.com")
    cfg.mfa_key = type(cfg.mfa_key)("a-different-key-than-before-" + "z" * 20)
    client.post("/api/auth/sign-in", json={"email": "chiave@example.com", "password": GOOD})
    r = client.post("/api/auth/mfa/verify", json={"code": _phone(secret, clock)})
    assert r.status_code == 400 and "administrator" in r.json()["detail"]


def test_setup_is_refused_while_the_app_is_on(client, db, post, clock) -> None:
    _account(client, db, post, "doppio@example.com")
    _turn_on(client, clock)
    assert client.post("/api/auth/mfa/totp/setup").status_code == 409


def test_an_unrelated_client_cannot_use_the_cookie_free_routes(db, cfg, post) -> None:
    _app(db, cfg, post)
    try:
        c = TestClient(__import__("backend.api", fromlist=["app"]).app)
        assert c.post("/api/auth/mfa/totp/setup").status_code == 401
        assert c.get("/api/auth/mfa/status").status_code == 401
    finally:
        from backend.api import app
        app.dependency_overrides.clear()
