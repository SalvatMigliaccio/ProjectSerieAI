"""
Sign in with Google (ADR 0008, step 2), end to end, without Google.

Google is replaced at the two points where the network would be touched: the
code exchange returns an ID token signed here with a throwaway RSA key, and
the key lookup returns that key's public half. Everything else — state, nonce,
PKCE, cookies, the account rules, the redirects — runs as in production.

WHAT IS PROTECTED, and why each one would fail silently:
- a callback that does not match the browser's flow (login CSRF) still signs
  someone in, just the wrong person;
- an ID token for another site, from another issuer, or replayed with an old
  nonce is a perfectly valid Google token;
- auto-linking to a `pending` account hands it to whoever registered it;
- `next` pointing off-site turns the sign-in into a phishing trampoline;
- unlinking the last way in leaves an account nobody can enter.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from backend.api import app
from backend.auth import oauth, service
from backend.auth.db import request_session
from backend.auth.deps import config, mail_sender
from backend.auth.mail import InMemorySender
from backend.auth.models import AuthEvent, User, UserIdentity, now
from backend.auth.settings import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.requires_db

GOOD = "a sufficiently long password"
CLIENT_ID = "test-client.apps.googleusercontent.com"
PUBLIC = "http://127.0.0.1:5173"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, google_client_id=CLIENT_ID,
                    google_client_secret="test-secret", public_url=PUBLIC,
                    secret_key="x" * 64)


@pytest.fixture
def google(monkeypatch):
    """
    Stand-in for Google. Set `google.claims` before the callback; the token
    exchange then returns them signed, with the nonce of the flow in progress.
    """
    from types import SimpleNamespace
    # `override`: fields to force, e.g. a wrong `aud` or `nonce`.
    fake = SimpleNamespace(claims={}, override={}, exchanged=[])

    def exchange(code, flow, cfg, client=None):
        fake.exchanged.append((code, flow.verifier))
        at = dt.datetime.now(dt.UTC)
        body = {"iss": "https://accounts.google.com", "aud": cfg.google_client_id,
                "iat": at, "exp": at + dt.timedelta(minutes=5), "nonce": flow.nonce,
                **fake.claims, **fake.override}
        return jwt.encode(body, KEY, algorithm="RS256")

    monkeypatch.setattr(oauth, "exchange_code", exchange)
    monkeypatch.setattr(oauth, "signing_key", lambda token: KEY.public_key())
    return fake


@pytest.fixture
def client(db, cfg):
    """Rollback session as in test_auth_http, plus Google-enabled settings."""
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
    app.dependency_overrides[mail_sender] = lambda: InMemorySender()
    app.dependency_overrides[config] = lambda: cfg
    # `deps.current_*` read settings() directly in places; the override covers
    # the routes, and the key is the same object either way for these tests.
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def _start(client, intent: str = "sign_in", next_path: str | None = None) -> str:
    """Begin a flow; returns the `state` Google would echo back."""
    params = {"intent": intent}
    if next_path:
        params["next"] = next_path
    r = client.get("/api/auth/oauth/google/start", params=params)
    assert r.status_code == 303, r.text
    target = urlparse(r.headers["location"])
    if target.netloc != "accounts.google.com":
        pytest.fail(f"start did not go to Google: {r.headers['location']}")
    return parse_qs(target.query)["state"][0]


def _back(client, state: str, **extra) -> httpx.Response:
    return client.get("/api/auth/oauth/google/callback",
                      params={"code": "one-time-code", "state": state, **extra})


def _landing(r) -> tuple[str, dict]:
    assert r.status_code == 303, r.text
    u = urlparse(r.headers["location"])
    assert f"{u.scheme}://{u.netloc}" == PUBLIC, "every outcome must land on the frontend"
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


def _sign_in(client, google, sub="g-123", email="nuovo@gmail.com", verified=True, **kw):
    google.claims = {"sub": sub, "email": email, "email_verified": verified}
    return _back(client, _start(client, **kw))


def _password_account(client, db, email: str) -> User:
    from tests.test_auth_http import _token
    assert client.post("/api/auth/signup", json={"email": email, "password": GOOD}).status_code == 202
    assert client.post("/api/auth/verify-email",
                       json={"token": _token(db, email, "verify")}).status_code == 200
    client.post("/api/auth/sign-out")
    return service.by_email(db, email)


# --- configuration --------------------------------------------------------------

def test_an_unconfigured_provider_is_off_not_broken(db) -> None:
    app.dependency_overrides[config] = lambda: Settings(_env_file=None)
    try:
        c = TestClient(app, follow_redirects=False)
        assert c.get("/api/auth/providers").json() == {"google": False, "apple": False}
        assert c.get("/api/auth/oauth/google/start").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_half_a_configuration_is_a_production_problem() -> None:
    cfg = Settings(_env_file=None, google_client_id="only-the-id")
    assert any("half configured" in p for p in cfg.production_problems())


def test_start_sends_pkce_nonce_and_the_registered_redirect(client, cfg) -> None:
    r = client.get("/api/auth/oauth/google/start")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [f"{PUBLIC}/api/auth/oauth/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert q["nonce"][0] and q["state"][0]
    assert "openid" in q["scope"][0].split()
    cookie = r.headers["set-cookie"]
    assert cfg.oauth_cookie_name in cookie and "HttpOnly" in cookie
    assert "Path=/api/auth/oauth" in cookie


# --- the flow's own defences ----------------------------------------------------

def test_a_callback_without_the_flow_cookie_signs_nobody_in(client, google, db) -> None:
    """Login CSRF: a callback URL crafted elsewhere, opened in the victim's browser."""
    state = _start(client)
    client.cookies.clear()
    google.claims = {"sub": "attacker", "email": "a@gmail.com", "email_verified": True}
    path, q = _landing(_back(client, state))
    assert (path, q.get("oauth_error")) == ("/sign-in", "state")
    assert service.by_email(db, "a@gmail.com") is None


def test_a_wrong_state_is_refused(client, google, db) -> None:
    _start(client)
    google.claims = {"sub": "s", "email": "s@gmail.com", "email_verified": True}
    _, q = _landing(_back(client, "not-the-state"))
    assert q["oauth_error"] == "state"
    assert service.by_email(db, "s@gmail.com") is None


@pytest.mark.parametrize(("field", "value", "code"), [
    ("aud", "someone-elses-client", "provider"),
    ("iss", "https://evil.example", "provider"),
    ("nonce", "a-replayed-nonce", "state"),
    ("exp", dt.datetime(2020, 1, 1, tzinfo=dt.UTC), "provider"),
])
def test_an_id_token_that_is_not_for_us_is_refused(client, google, db, field, value, code) -> None:
    google.override = {field: value}
    _, q = _landing(_sign_in(client, google, email="forged@gmail.com"))
    assert q["oauth_error"] == code
    assert service.by_email(db, "forged@gmail.com") is None


def test_cancelling_at_google_is_not_reported_as_a_fault(client) -> None:
    state = _start(client)
    _, q = _landing(client.get("/api/auth/oauth/google/callback",
                               params={"state": state, "error": "access_denied"}))
    assert q["oauth_error"] == "cancelled"


def test_the_flow_cookie_is_spent_by_the_callback(client, google) -> None:
    r = _sign_in(client, google)
    assert any(h.startswith("ainaples_oauth=") and "Max-Age=0" in h
               for h in r.headers.get_list("set-cookie")), "flow cookie not cleared"


@pytest.mark.parametrize("evil", ["//evil.example/x", "https://evil.example", "/\\evil.example"])
def test_next_cannot_leave_the_site(client, google, evil) -> None:
    path, _ = _landing(_sign_in(client, google, next_path=evil))
    assert path == "/dashboard"


def test_next_inside_the_site_is_honoured(client, google) -> None:
    path, _ = _landing(_sign_in(client, google, next_path="/dashboard/giornata"))
    assert path == "/dashboard/giornata"


# --- the account rules ----------------------------------------------------------

def test_a_first_google_sign_in_creates_an_active_passwordless_account(client, google, db) -> None:
    path, q = _landing(_sign_in(client, google, sub="g-new", email="Primo@Gmail.com"))
    assert path == "/dashboard" and "oauth_error" not in q
    user = service.by_email(db, "primo@gmail.com")
    assert user.status == "active" and user.email_verified_at is not None
    assert user.password_hash is None
    assert {r.name for r in user.roles} == {"customer"}
    assert [(i.provider, i.subject) for i in user.identities] == [("google", "g-new")]

    me = client.get("/api/auth/me").json()
    assert me["has_password"] is False and me["providers"] == ["google"]


def test_the_subject_finds_the_account_even_if_the_email_changed(client, google, db) -> None:
    _sign_in(client, google, sub="g-stable", email="vecchia@gmail.com")
    client.post("/api/auth/sign-out")
    _, q = _landing(_sign_in(client, google, sub="g-stable", email="nuova@gmail.com"))
    assert "oauth_error" not in q
    assert service.by_email(db, "nuova@gmail.com") is None, "a second account was created"
    assert client.get("/api/auth/me").json()["email"] == "vecchia@gmail.com"


def test_an_unverified_google_email_opens_nothing(client, google, db) -> None:
    _, q = _landing(_sign_in(client, google, email="nonverificata@gmail.com", verified=False))
    assert q["oauth_error"] == "email_unverified"
    assert service.by_email(db, "nonverificata@gmail.com") is None


def test_a_verified_account_is_linked_automatically(client, google, db) -> None:
    user = _password_account(client, db, "esistente@gmail.com")
    _, q = _landing(_sign_in(client, google, sub="g-link", email="esistente@gmail.com"))
    assert "oauth_error" not in q
    db.refresh(user)
    assert [i.subject for i in user.identities] == ["g-link"]
    assert user.has_password(), "linking must not touch the password"
    events = db.scalars(select(AuthEvent).where(
        AuthEvent.user_id == user.id, AuthEvent.kind == "identity_linked")).all()
    assert events and events[0].details["auto"] is True


def test_a_pending_account_is_never_linked(client, google, db) -> None:
    """Somebody else may have opened it with this address and their own password."""
    assert client.post("/api/auth/signup",
                       json={"email": "squatter@gmail.com", "password": GOOD}).status_code == 202
    _, q = _landing(_sign_in(client, google, sub="g-owner", email="squatter@gmail.com"))
    assert q["oauth_error"] == "link_pending"
    user = service.by_email(db, "squatter@gmail.com")
    assert user.status == "pending" and user.identities == []


def test_a_suspended_account_stays_out_through_google(client, google, db) -> None:
    _sign_in(client, google, sub="g-susp", email="sospeso@gmail.com")
    client.post("/api/auth/sign-out")
    user = service.by_email(db, "sospeso@gmail.com")
    user.status = "suspended"
    db.flush()
    _, q = _landing(_sign_in(client, google, sub="g-susp", email="sospeso@gmail.com"))
    assert q["oauth_error"] == "inactive"


def test_the_bootstrap_superadmin_applies_to_google_sign_in(client, google, db, cfg) -> None:
    cfg.superadmin_emails = "capo@gmail.com"
    _sign_in(client, google, sub="g-boss", email="capo@gmail.com")
    assert "superadmin" in client.get("/api/auth/me").json()["roles"]


# --- linking from the profile ----------------------------------------------------

def _signed_in_with_password(client, db, email: str) -> User:
    user = _password_account(client, db, email)
    assert client.post("/api/auth/sign-in", json={"email": email, "password": GOOD}).status_code == 200
    return user


def test_link_from_the_profile_ties_any_google_account(client, google, db) -> None:
    user = _signed_in_with_password(client, db, "profilo@example.com")
    google.claims = {"sub": "g-prof", "email": "altro.indirizzo@gmail.com", "email_verified": True}
    path, q = _landing(_back(client, _start(client, intent="link", next_path="/account/sicurezza")))
    assert (path, q) == ("/account/sicurezza", {"linked": "google"})
    rows = client.get("/api/auth/identities").json()
    assert [(r["provider"], r["email"]) for r in rows] == [("google", "altro.indirizzo@gmail.com")]
    assert "subject" not in rows[0]
    db.refresh(user)
    assert len(user.identities) == 1


def test_link_needs_a_session(client, google) -> None:
    path, q = _landing(client.get("/api/auth/oauth/google/start", params={"intent": "link"}))
    assert (path, q["oauth_error"]) == ("/sign-in", "session")


def test_link_refuses_a_google_account_owned_by_someone_else(client, google, db) -> None:
    _sign_in(client, google, sub="g-taken", email="proprietario@gmail.com")
    client.post("/api/auth/sign-out")
    _signed_in_with_password(client, db, "secondo@example.com")
    google.claims = {"sub": "g-taken", "email": "proprietario@gmail.com", "email_verified": True}
    _, q = _landing(_back(client, _start(client, intent="link")))
    assert q["oauth_error"] == "linked_elsewhere"


def test_link_is_bound_to_the_session_that_started_it(client, google, db) -> None:
    """Sign out and back in as someone else mid-flow: the link must not land on them."""
    _signed_in_with_password(client, db, "primo-utente@example.com")
    state = _start(client, intent="link")
    client.post("/api/auth/sign-out")
    # The flow cookie survives: nothing but the callback clears it.
    _signed_in_with_password(client, db, "secondo-utente@example.com")
    google.claims = {"sub": "g-mid", "email": "x@gmail.com", "email_verified": True}
    _, q = _landing(_back(client, state))
    assert q["oauth_error"] == "session"
    assert service.by_email(db, "secondo-utente@example.com").identities == []


def test_unlink_works_when_a_password_remains(client, google, db) -> None:
    _signed_in_with_password(client, db, "scollega@example.com")
    google.claims = {"sub": "g-un", "email": "s@gmail.com", "email_verified": True}
    _back(client, _start(client, intent="link"))
    assert client.post("/api/auth/identities/google/unlink").json() == {"unlinked": 1}
    assert client.get("/api/auth/identities").json() == []


def test_the_last_way_in_cannot_be_unlinked(client, google, db) -> None:
    _sign_in(client, google, sub="g-only", email="solo@gmail.com")
    r = client.post("/api/auth/identities/google/unlink")
    assert r.status_code == 409
    assert db.scalar(select(UserIdentity).where(UserIdentity.subject == "g-only")) is not None


# --- deleting an account that has no password -----------------------------------

def test_a_passwordless_account_deletes_itself_after_reconfirming(client, google, db) -> None:
    _sign_in(client, google, sub="g-del", email="addio@gmail.com")
    assert client.post("/api/me/account/delete", json={}).status_code == 403, \
        "a session alone must not delete"

    google.claims = {"sub": "g-del", "email": "addio@gmail.com", "email_verified": True}
    path, q = _landing(_back(client, _start(client, intent="reauth",
                                            next_path="/account/sicurezza")))
    assert (path, q) == ("/account/sicurezza", {"reauth": "google"})
    assert client.post("/api/me/account/delete", json={}).status_code == 204
    assert service.by_email(db, "addio@gmail.com") is None


def test_reconfirming_with_another_google_account_proves_nothing(client, google, db) -> None:
    _sign_in(client, google, sub="g-mine", email="mio@gmail.com")
    google.claims = {"sub": "g-not-mine", "email": "altro@gmail.com", "email_verified": True}
    _, q = _landing(_back(client, _start(client, intent="reauth")))
    assert q["oauth_error"] == "mismatch"
    assert client.post("/api/me/account/delete", json={}).status_code == 403


def test_the_reauth_mark_is_bound_to_its_session(cfg) -> None:
    import uuid
    u, s = uuid.uuid4(), uuid.uuid4()
    token = oauth.issue_reauth(u, s, cfg)
    assert oauth.reauth_valid(token, u, s, cfg)
    assert not oauth.reauth_valid(token, u, uuid.uuid4(), cfg)
    assert not oauth.reauth_valid(token, uuid.uuid4(), s, cfg)
    late = oauth.issue_reauth(u, s, cfg, at=now() - dt.timedelta(minutes=cfg.reauth_minutes + 1))
    assert not oauth.reauth_valid(late, u, s, cfg)


# --- the token exchange itself --------------------------------------------------

def _flow() -> oauth.Flow:
    return oauth.new_flow("google", "sign_in", None)


def test_exchange_rejects_an_html_error_page(cfg) -> None:
    """Security rule 3: a proxy's error page is not a token response."""
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200, text="<html>502</html>", headers={"content-type": "text/html"}))
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.exchange_code("c", _flow(), cfg, client=httpx.Client(transport=transport))
    assert exc.value.code == "provider"


def test_exchange_sends_the_pkce_verifier_and_reads_the_id_token(cfg) -> None:
    flow = _flow()
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(req.content.decode()))
        return httpx.Response(200, json={"id_token": "tok", "access_token": "a"})

    out = oauth.exchange_code("c", flow, cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert out == "tok"
    assert seen["code_verifier"] == [flow.verifier]
    assert seen["redirect_uri"] == [oauth.redirect_uri(cfg, "google")]
