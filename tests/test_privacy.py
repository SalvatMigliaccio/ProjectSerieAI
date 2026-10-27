"""
What the privacy policy and the cookie policy promise, checked against the code.

docs/SPEC_PRIVACY_COOKIE.md, requirement R6: guards, not promises. A policy is
a description of the system, and a description goes stale without raising
anything. Each test here fails the day the code and the published text part
ways, and its message says which of the two to update.

Three groups:
- the published facts (`frontend/src/legal/dati.ts`) against the settings;
- no third-party resource in the page shell;
- over HTTP and on the database: the cookies actually set, account deletion
  (R4), and the retention purge (R5).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
from backend.auth import retention, service
from backend.auth.models import AuthEvent, EmailToken, User, UserPrediction, UserSession, now
from backend.auth.settings import Settings
from sqlalchemy import select
from tests.test_auth_http import GOOD, _register_and_verify, _token, client, post  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
DATI = ROOT / "frontend" / "src" / "legal" / "dati.ts"
INDEX = ROOT / "frontend" / "index.html"


def _defaults() -> Settings:
    """The shipped defaults: what the policy describes, whatever `.env` says."""
    return Settings(_env_file=None)


def _published() -> str:
    return DATI.read_text(encoding="utf-8")


# --- the published facts ------------------------------------------------------

def test_the_cookie_policy_names_exactly_the_cookies_the_backend_sets() -> None:
    cfg = _defaults()
    named = set(re.findall(r'nome:\s*"([^"]+)"', _published()))
    assert named == {cfg.cookie_name, cfg.access_cookie_name}, (
        "the cookie policy and backend/auth/settings.py disagree: update "
        "frontend/src/legal/dati.ts (and AGGIORNATA_IL) together with the code")


def test_the_published_cookie_durations_match_the_settings() -> None:
    cfg = _defaults()
    text = _published()
    assert f'durata: "{cfg.access_token_minutes} minuti"' in text, \
        "the access cookie lifetime on the cookie page is not access_token_minutes"
    assert f"al massimo {cfg.session_max_hours} ore" in text, \
        "the session ceiling on the cookie page is not session_max_hours"


@pytest.mark.parametrize("published, setting", [
    ("registroAccessi", "audit_retention_days"),
    ("sessioni", "session_retention_days"),
    ("tokenEmail", "token_retention_days"),
    ("accountNonConfermati", "pending_account_days"),
])
def test_the_published_retention_periods_match_the_settings(published: str, setting: str) -> None:
    found = re.search(rf"\b{published}:\s*(\d+)", _published())
    assert found, f"{published} is missing from frontend/src/legal/dati.ts"
    assert int(found.group(1)) == getattr(_defaults(), setting), (
        f"the privacy page promises {found.group(1)} days for {published}, the "
        f"purge applies {setting}={getattr(_defaults(), setting)}: one of the two is wrong")


def test_the_page_shell_loads_nothing_from_another_origin() -> None:
    """
    No banner is needed only while every cookie is technical.

    A script, a stylesheet or a font from another origin is how a third-party
    cookie arrives without anyone writing `document.cookie`. If this fails, the
    cookie policy needs a new row and probably a consent banner.
    """
    html = INDEX.read_text(encoding="utf-8")
    external = re.findall(r'(?:src|href)\s*=\s*"((?:https?:)?//[^"]+)"', html)
    assert external == [], f"third-party resources in index.html: {external}"


# --- over HTTP ------------------------------------------------------------------

def _cookie_names(response) -> set[str]:
    return {h.split("=", 1)[0].strip() for h in response.headers.get_list("set-cookie")}


@pytest.mark.requires_db
def test_no_flow_sets_a_cookie_the_policy_does_not_declare(client, db) -> None:  # noqa: F811
    """Signup to deletion, every Set-Cookie on the way."""
    cfg = _defaults()
    declared = {cfg.cookie_name, cfg.access_cookie_name}
    email = "cookie-policy@example.com"
    seen: set[str] = set()

    seen |= _cookie_names(client.post("/api/auth/signup", json={"email": email, "password": GOOD}))
    # Verified by hand, not with _register_and_verify: its headers count too.
    r = client.post("/api/auth/verify-email", json={"token": _token(db, email, "verify")})
    seen |= _cookie_names(r)
    seen |= _cookie_names(client.post("/api/auth/refresh"))
    seen |= _cookie_names(client.post("/api/auth/sign-out"))
    seen |= _cookie_names(client.post("/api/auth/sign-in", json={"email": email, "password": GOOD}))
    seen |= _cookie_names(client.post("/api/me/account/delete", json={"password": GOOD}))

    assert seen, "no Set-Cookie seen at all: the test would pass vacuously"
    assert seen <= declared, (
        f"undeclared cookies {sorted(seen - declared)}: add them to the cookie "
        f"policy (frontend/src/legal/dati.ts) or stop setting them")


@pytest.mark.requires_db
def test_deleting_the_account_needs_the_password(client, db) -> None:  # noqa: F811
    _register_and_verify(client, db, "ripensamento@example.com")
    r = client.post("/api/me/account/delete", json={"password": "not the right one"})
    assert r.status_code == 403
    assert service.by_email(db, "ripensamento@example.com") is not None
    assert client.get("/api/auth/me").status_code == 200, "a refusal must not sign out"
    refused = db.scalars(select(AuthEvent).where(
        AuthEvent.kind == "account_deleted", AuthEvent.outcome == "ko")).all()
    assert refused, "a refused deletion must be audited, it is a password guess"


@pytest.mark.requires_db
def test_deleting_the_account_removes_it_and_what_it_owns(client, db) -> None:  # noqa: F811
    email = "addio@example.com"
    _register_and_verify(client, db, email)
    user = service.by_email(db, email)
    uid = user.id
    db.add(UserPrediction(user_id=uid, season="2627", matchday=9,
                          home_team="Napoli", away_team="Lecce", market="1"))
    db.flush()
    assert db.scalars(select(AuthEvent).where(AuthEvent.user_id == uid)).all(), \
        "signup and verification should have left audit rows to anonymise"

    r = client.post("/api/me/account/delete", json={"password": GOOD})
    assert r.status_code == 204
    assert _cookie_names(r) >= {_defaults().cookie_name, _defaults().access_cookie_name}, \
        "both cookies must be cleared on the way out"

    assert db.get(User, uid) is None
    for model in (UserSession, EmailToken, UserPrediction):
        assert db.scalars(select(model).where(model.user_id == uid)).all() == [], \
            f"{model.__tablename__} kept rows of a deleted account"
    assert db.scalars(select(AuthEvent).where(AuthEvent.user_id == uid)).all() == [], \
        "audit rows must lose the user id, not keep it"
    done = db.scalars(select(AuthEvent).where(
        AuthEvent.kind == "account_deleted", AuthEvent.outcome == "ok")).all()
    assert done and all(e.user_id is None and not e.details for e in done), \
        "the deletion is audited without naming who was deleted"

    assert client.get("/api/auth/me").status_code == 401
    # The address is free again: erasure means the account is really gone.
    assert client.post("/api/auth/signup",
                       json={"email": email, "password": GOOD}).status_code == 202


@pytest.mark.requires_db
def test_deleting_the_account_needs_a_session(client) -> None:  # noqa: F811
    assert client.post("/api/me/account/delete", json={"password": GOOD}).status_code == 401


# --- retention --------------------------------------------------------------------

def _user(db, email: str, status: str, created: dt.datetime) -> User:
    u = User(email=email, password_hash="x", status=status, created_at=created)
    db.add(u)
    db.flush()
    return u


def _session(db, user: User, tag: str, expires: dt.datetime,
             revoked: dt.datetime | None = None) -> UserSession:
    s = UserSession(user_id=user.id, token_hash=tag.ljust(64, "0"),
                    expires_at=expires, revoked_at=revoked)
    db.add(s)
    db.flush()
    return s


@pytest.mark.requires_db
def test_the_purge_removes_what_is_past_its_period_and_nothing_else(db) -> None:
    cfg = _defaults()
    at = now()
    day = dt.timedelta(days=1)

    active = _user(db, "vecchio-ma-attivo@example.com", "active", at - 1000 * day)
    old_pending = _user(db, "mai-confermato@example.com", "pending", at - 31 * day)
    new_pending = _user(db, "appena-iscritto@example.com", "pending", at - 2 * day)

    live = _session(db, active, "a", expires=at + day)
    stale = _session(db, active, "b", expires=at - 366 * day)
    revoked_recently = _session(db, active, "c", expires=at - 400 * day,
                                revoked=at - 10 * day)

    db.add_all([
        EmailToken(user_id=active.id, kind="reset", token_hash="d".ljust(64, "0"),
                   expires_at=at - 31 * day),
        EmailToken(user_id=active.id, kind="reset", token_hash="e".ljust(64, "0"),
                   expires_at=at - 5 * day),
        AuthEvent(kind="sign_in", outcome="ok", at=at - 366 * day),
        AuthEvent(kind="sign_in", outcome="ok", at=at - 364 * day),
    ])
    db.flush()

    would = retention.purge(db, cfg=cfg, at=at, dry_run=True)
    assert db.get(User, old_pending.id) is not None, "a dry run deleted something"
    did = retention.purge(db, cfg=cfg, at=at)
    assert did == would, "the dry run must count exactly what the real run deletes"
    db.expire_all()

    assert db.get(User, active.id) is not None, "an active account is never purged"
    assert db.get(User, old_pending.id) is None
    assert db.get(User, new_pending.id) is not None
    assert db.get(UserSession, live.id) is not None, "a live session is never purged"
    assert db.get(UserSession, stale.id) is None
    assert db.get(UserSession, revoked_recently.id) is not None, \
        "a session counts from when it stopped working, here its revocation"
    tokens = {t.token_hash[0] for t in db.scalars(
        select(EmailToken).where(EmailToken.user_id == active.id))}
    assert tokens == {"e"}
    left = db.scalars(select(AuthEvent.at).where(AuthEvent.at < at - 363 * day)).all()
    assert all(a >= at - 365 * day for a in left), "audit rows older than a year survived"

    assert retention.purge(db, cfg=cfg, at=at) == dict.fromkeys(did, 0), \
        "a second run must find nothing: the purge is idempotent"
