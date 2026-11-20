"""
The emailed second factor (ADR 0008, step 3), over HTTP.

The mailbox is in memory: the code is read from the message, as a person would.

WHAT IS PROTECTED, and how each would fail without a sound:
- a sign-in with 2FA on that hands out a session before the code is just a
  sign-in without 2FA, with an extra email;
- a code accepted from another browser, for another purpose, after its
  attempts ran out, or after a newer one was sent, is a code an attacker can
  guess or reuse;
- Google sign-in that skips the code makes the button a way around 2FA;
- turning 2FA off with a session alone gives a stolen cookie the account.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlparse

import pytest
from backend.api import app
from backend.auth import mfa, service
from backend.auth.db import request_session
from backend.auth.deps import config, mail_sender
from backend.auth.mail import InMemorySender
from backend.auth.models import AuthEvent, MfaChallenge
from backend.auth.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_auth_oauth import CLIENT_ID, PUBLIC, google  # noqa: F401  (fixture)

pytestmark = pytest.mark.requires_db

GOOD = "a sufficiently long password"


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, google_client_id=CLIENT_ID,
                    google_client_secret="test-secret", public_url=PUBLIC,
                    secret_key="y" * 64)


@pytest.fixture
def post() -> InMemorySender:
    return InMemorySender()


def _app(db, cfg, post):
    from backend.auth.ratelimit import ALL_LIMITERS
    for limiter in ALL_LIMITERS:
        limiter.reset()

    def scoped():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[request_session] = scoped
    app.dependency_overrides[mail_sender] = lambda: post
    app.dependency_overrides[config] = lambda: cfg


@pytest.fixture
def client(db, cfg, post):
    _app(db, cfg, post)
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def _code(post: InMemorySender, email: str) -> str:
    message = post.last_to(email)
    assert message is not None, f"no email to {email}"
    found = re.search(r"\b(\d{6})\b", message.text)
    assert found, "no six-digit code in the email"
    return found.group(1)


def _account(client, db, post, email: str) -> None:
    """A verified password account, signed in."""
    from tests.test_auth_http import _token
    assert client.post("/api/auth/signup", json={"email": email, "password": GOOD}).status_code == 202
    assert client.post("/api/auth/verify-email",
                       json={"token": _token(db, email, "verify")}).status_code == 200


def _with_2fa(client, db, post, email: str) -> None:
    """Account with 2FA turned on through the real routes, then signed out."""
    _account(client, db, post, email)
    assert client.post("/api/auth/mfa/enable/start").status_code == 200
    r = client.post("/api/auth/mfa/enable/confirm", json={"code": _code(post, email)})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is True
    client.post("/api/auth/sign-out")


def _cookies(r) -> set[str]:
    return {h.split("=", 1)[0] for h in r.headers.get_list("set-cookie")
            if "Max-Age=0" not in h and 'expires=Thu, 01 Jan 1970' not in h}


# --- turning it on ---------------------------------------------------------------

def test_turning_it_on_needs_the_emailed_code(client, db, post) -> None:
    _account(client, db, post, "attiva@example.com")
    r = client.post("/api/auth/mfa/enable/start")
    assert r.status_code == 200 and r.json()["sent_to"] == "a•••a@example.com"
    right = _code(post, "attiva@example.com")
    wrong = "000000" if right != "000000" else "999999"
    assert client.post("/api/auth/mfa/enable/confirm", json={"code": wrong}).status_code == 400
    assert client.get("/api/auth/me").json()["mfa_enabled"] is False

    r = client.post("/api/auth/mfa/enable/confirm", json={"code": right})
    assert r.json()["mfa_enabled"] is True
    assert db.scalars(select(AuthEvent).where(AuthEvent.kind == "mfa_enabled")).all()


def test_the_code_is_not_in_the_subject(client, db, post) -> None:
    """A subject is readable on a locked phone's notifications."""
    _account(client, db, post, "oggetto@example.com")
    client.post("/api/auth/mfa/enable/start")
    message = post.last_to("oggetto@example.com")
    assert _code(post, "oggetto@example.com") not in message.subject


def test_the_code_is_never_stored_in_the_clear(client, db, post) -> None:
    _account(client, db, post, "hash@example.com")
    client.post("/api/auth/mfa/enable/start")
    code = _code(post, "hash@example.com")
    row = db.scalars(select(MfaChallenge).order_by(MfaChallenge.created_at.desc())).first()
    assert code not in row.code_hash
    assert row.code_hash != hashlib.sha256(code.encode()).hexdigest(), \
        "an unkeyed hash of six digits is reversed by trying a million values"


# --- signing in with it on -------------------------------------------------------

def test_the_password_alone_opens_no_session(client, db, post) -> None:
    _with_2fa(client, db, post, "due@example.com")
    r = client.post("/api/auth/sign-in", json={"email": "due@example.com", "password": GOOD})
    assert r.status_code == 202
    assert r.json() == {"mfa_required": True, "sent_to": "d•••e@example.com"}
    assert _cookies(r) == {"ainaples_mfa"}, "a session cookie before the code defeats 2FA"
    assert client.get("/api/auth/me").status_code == 401


def test_the_code_completes_the_sign_in(client, db, post) -> None:
    _with_2fa(client, db, post, "completa@example.com")
    client.post("/api/auth/sign-in", json={"email": "completa@example.com", "password": GOOD})
    assert client.get("/api/auth/mfa/pending").json()["sent_to"] == "c•••a@example.com"

    r = client.post("/api/auth/mfa/verify", json={"code": _code(post, "completa@example.com")})
    assert r.status_code == 200 and r.json()["email"] == "completa@example.com"
    assert {"ainaples_session", "ainaples_access"} <= _cookies(r)
    assert client.get("/api/auth/me").status_code == 200
    ok = db.scalars(select(AuthEvent).where(AuthEvent.kind == "sign_in",
                                            AuthEvent.outcome == "ok")).all()
    assert any((e.details or {}).get("mfa") == "email" for e in ok)


def test_a_code_typed_with_spaces_is_accepted(client, db, post) -> None:
    _with_2fa(client, db, post, "spazi@example.com")
    client.post("/api/auth/sign-in", json={"email": "spazi@example.com", "password": GOOD})
    code = _code(post, "spazi@example.com")
    assert client.post("/api/auth/mfa/verify",
                       json={"code": f"{code[:3]} {code[3:]}"}).status_code == 200


def test_the_attempts_run_out(client, db, post, cfg) -> None:
    _with_2fa(client, db, post, "tentativi@example.com")
    client.post("/api/auth/sign-in", json={"email": "tentativi@example.com", "password": GOOD})
    right = _code(post, "tentativi@example.com")
    wrong = "111111" if right != "111111" else "222222"
    for _ in range(cfg.mfa_max_attempts):
        assert client.post("/api/auth/mfa/verify", json={"code": wrong}).status_code == 400
    r = client.post("/api/auth/mfa/verify", json={"code": right})
    assert r.status_code == 400, "the right code after the cap must not open a session"
    assert client.get("/api/auth/me").status_code == 401


def test_the_code_only_works_in_the_browser_that_asked(client, db, post, cfg) -> None:
    _with_2fa(client, db, post, "browser@example.com")
    client.post("/api/auth/sign-in", json={"email": "browser@example.com", "password": GOOD})
    code = _code(post, "browser@example.com")
    other = TestClient(app, follow_redirects=False)
    assert other.post("/api/auth/mfa/verify", json={"code": code}).status_code == 400


def test_resend_kills_the_previous_code(client, db, post) -> None:
    _with_2fa(client, db, post, "rinvia@example.com")
    client.post("/api/auth/sign-in", json={"email": "rinvia@example.com", "password": GOOD})
    first = _code(post, "rinvia@example.com")
    assert client.post("/api/auth/mfa/resend").status_code == 200
    second = _code(post, "rinvia@example.com")
    if first != second:
        assert client.post("/api/auth/mfa/verify", json={"code": first}).status_code == 400
    assert client.post("/api/auth/mfa/verify", json={"code": second}).status_code == 200


def test_resend_is_limited_per_account(client, db, post) -> None:
    _with_2fa(client, db, post, "limite@example.com")   # enabling spent one
    client.post("/api/auth/sign-in", json={"email": "limite@example.com", "password": GOOD})
    statuses = [client.post("/api/auth/mfa/resend").status_code for _ in range(6)]
    assert 429 in statuses


def test_a_code_for_turning_it_on_does_not_sign_in(client, db, post) -> None:
    """Purposes do not mix: each code opens exactly one door."""
    _account(client, db, post, "scopi@example.com")
    client.post("/api/auth/mfa/enable/start")
    code = _code(post, "scopi@example.com")
    assert client.post("/api/auth/mfa/verify", json={"code": code}).status_code == 400


def test_the_service_cannot_open_a_session_past_2fa(db, post) -> None:
    """A caller that forgets 2FA must fail, not sign someone in."""
    from backend.auth.models import User, now
    from backend.auth.password import hash_password
    user = User(email="servizio@example.com", password_hash=hash_password(GOOD),
                status="active", email_verified_at=now(), mfa_method="email",
                mfa_enabled_at=now())
    db.add(user)
    db.flush()
    with pytest.raises(service.MfaRequired):
        service.sign_in(db, "servizio@example.com", GOOD, cfg=Settings(_env_file=None))


# --- Google is a first factor, not a way around ----------------------------------

def test_google_sign_in_still_asks_for_the_code(client, db, post, google) -> None:  # noqa: F811
    _with_2fa(client, db, post, "g2fa@gmail.com")
    google.claims = {"sub": "g-2fa", "email": "g2fa@gmail.com", "email_verified": True}
    start = client.get("/api/auth/oauth/google/start", params={"next": "/dashboard/giornata"})
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get("/api/auth/oauth/google/callback", params={"code": "c", "state": state})

    page = urlparse(urlparse(r.headers["location"]).fragment)
    assert page.path == "/sign-in/codice"
    assert parse_qs(page.query)["next"] == ["/dashboard/giornata"]
    assert "ainaples_session" not in _cookies(r) and "ainaples_mfa" in _cookies(r)
    assert client.get("/api/auth/me").status_code == 401

    assert client.post("/api/auth/mfa/verify",
                       json={"code": _code(post, "g2fa@gmail.com")}).status_code == 200
    ok = db.scalars(select(AuthEvent).where(AuthEvent.kind == "sign_in",
                                            AuthEvent.outcome == "ok")).all()
    assert any((e.details or {}) == {"via": "google", "mfa": "email"} for e in ok)


# --- turning it off ---------------------------------------------------------------

def _signed_in_with_2fa(client, db, post, email: str) -> None:
    _with_2fa(client, db, post, email)
    client.post("/api/auth/sign-in", json={"email": email, "password": GOOD})
    client.post("/api/auth/mfa/verify", json={"code": _code(post, email)})


def test_a_session_alone_cannot_turn_it_off(client, db, post) -> None:
    _signed_in_with_2fa(client, db, post, "spegni@example.com")
    assert client.post("/api/auth/mfa/disable/confirm", json={"code": "123456"}).status_code == 400
    assert client.get("/api/auth/me").json()["mfa_enabled"] is True


def test_turning_it_off_with_the_code(client, db, post) -> None:
    _signed_in_with_2fa(client, db, post, "off@example.com")
    assert client.post("/api/auth/mfa/disable/start").status_code == 200
    r = client.post("/api/auth/mfa/disable/confirm", json={"code": _code(post, "off@example.com")})
    assert r.json()["mfa_enabled"] is False
    client.post("/api/auth/sign-out")
    assert client.post("/api/auth/sign-in",
                       json={"email": "off@example.com", "password": GOOD}).status_code == 200


# --- small pieces -----------------------------------------------------------------

@pytest.mark.parametrize(("email", "masked"), [
    ("mario.rossi@gmail.com", "m•••i@gmail.com"),
    ("ab@x.it", "a•••@x.it"),
])
def test_the_address_is_masked(email, masked) -> None:
    assert mfa.mask(email) == masked


def test_codes_keep_leading_zeros() -> None:
    assert all(len(mfa.new_code()) == 6 for _ in range(200))
