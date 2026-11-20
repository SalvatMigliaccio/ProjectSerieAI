"""
/api/admin: the administration panel, end to end.

What matters most here is what the panel REFUSES: a customer reaching it, an
admin promoting someone, anyone touching a superadmin or their own account.
Each refusal is one of the ways an administration panel turns into a way in.
"""

from __future__ import annotations

import pytest
from backend.api import app
from backend.auth import service
from backend.auth.models import AuthEvent, UserSession
from backend.auth.settings import settings
from fastapi.testclient import TestClient
from tests.test_auth_http import GOOD, _register_and_verify, client, post  # noqa: F401

pytestmark = pytest.mark.requires_db


def _user(db, email: str):
    return service.by_email(db, email)


def _as(db, email: str, *roles: str) -> TestClient:
    """A fresh browser signed in as `email`, holding `roles`."""
    c = TestClient(app)
    _register_and_verify(c, db, email)
    user = _user(db, email)
    for r in roles:
        service.grant_role(db, user, r)
    # COMMIT, not flush. A route that answers with an error rolls the shared
    # session back, and a role that was only flushed would vanish with it:
    # the next request would get a 403 for the wrong reason. The commit only
    # reaches the fixture's savepoint, so the test still leaves nothing behind.
    db.commit()
    return c


@pytest.fixture
def boss(client, db):  # noqa: F811
    return _as(db, "boss@example.com", "superadmin")


@pytest.fixture
def staff(client, db):  # noqa: F811
    return _as(db, "staff@example.com", "admin")


@pytest.fixture
def customer(client, db):  # noqa: F811
    return _as(db, "customer@example.com")


def _id(db, email: str) -> str:
    return str(_user(db, email).id)


# --- who gets in --------------------------------------------------------------

def test_anonymous_and_customers_are_kept_out(client, customer) -> None:  # noqa: F811
    assert TestClient(app).get("/api/admin/users").status_code == 401
    assert customer.get("/api/admin/users").status_code == 403
    assert customer.get("/api/admin/summary").status_code == 403


def test_the_bootstrap_address_becomes_superadmin_only_once_verified(client, db, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings(), "superadmin_emails", "Owner@Example.com, other@example.com")
    c = TestClient(app)
    assert c.post("/api/auth/signup",
                  json={"email": "owner@example.com", "password": GOOD}).status_code == 202
    assert "superadmin" not in {r.name for r in _user(db, "owner@example.com").roles}, \
        "an unverified address must not hold the role: whoever registers first would"

    _register_and_verify(TestClient(app), db, "someone@example.com")
    assert not _user(db, "someone@example.com").roles or \
        "superadmin" not in {r.name for r in _user(db, "someone@example.com").roles}

    from tests.test_auth_http import _token
    token = _token(db, "owner@example.com", "verify")
    assert c.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert "superadmin" in {r.name for r in _user(db, "owner@example.com").roles}
    assert c.get("/api/admin/summary").status_code == 200


# --- reads --------------------------------------------------------------------

def test_search_matches_part_of_the_address_and_takes_wildcards_literally(staff, customer) -> None:
    body = staff.get("/api/admin/users", params={"q": "CUSTOM"}).json()
    assert [r["email"] for r in body["rows"]] == ["customer@example.com"]
    assert staff.get("/api/admin/users", params={"q": "%"}).json()["total"] == 0, \
        "a % typed in the box is a character, not 'everything'"


def test_filters_and_summary(staff, customer, boss) -> None:
    roles = {r["email"] for r in staff.get("/api/admin/users", params={"role": "staff"}).json()["rows"]}
    # A subset, not equality: the local database may hold real accounts too.
    assert {"staff@example.com", "boss@example.com"} <= roles
    assert "customer@example.com" not in roles
    s = staff.get("/api/admin/summary").json()
    assert s["new_24h"] >= 3 and len(s["signups_by_day"]) == 30


# --- ban and reactivate -------------------------------------------------------

def test_suspending_blocks_sign_in_and_ends_sessions(staff, customer, db) -> None:
    target = _id(db, "customer@example.com")
    r = staff.post(f"/api/admin/users/{target}/suspend", json={"reason": "spam"})
    assert r.status_code == 200 and r.json()["user"]["status"] == "suspended"

    assert db.query(UserSession).filter_by(user_id=_user(db, "customer@example.com").id,
                                           revoked_at=None).count() == 0
    assert customer.get("/api/auth/me").status_code == 401, "the session dies at once"
    assert TestClient(app).post("/api/auth/sign-in", json={
        "email": "customer@example.com", "password": GOOD}).status_code == 401

    event = db.query(AuthEvent).filter_by(kind="suspended").one()
    assert event.details["by"] == _id(db, "staff@example.com")
    assert event.details["reason"] == "spam"

    r = staff.post(f"/api/admin/users/{target}/reactivate")
    assert r.status_code == 200 and r.json()["user"]["status"] == "active"
    assert TestClient(app).post("/api/auth/sign-in", json={
        "email": "customer@example.com", "password": GOOD}).status_code == 200


def test_nobody_acts_on_their_own_account(staff, db) -> None:
    me = _id(db, "staff@example.com")
    assert staff.post(f"/api/admin/users/{me}/suspend", json={}).status_code == 409


def test_an_admin_cannot_touch_another_admin_or_a_superadmin(staff, boss, db) -> None:
    other = _as(db, "staff2@example.com", "admin")  # noqa: F841
    assert staff.post(f"/api/admin/users/{_id(db, 'staff2@example.com')}/suspend",
                      json={}).status_code == 403
    assert staff.post(f"/api/admin/users/{_id(db, 'boss@example.com')}/suspend",
                      json={}).status_code == 403


def test_a_superadmin_can_suspend_an_admin_but_nobody_a_superadmin(boss, staff, db) -> None:
    assert boss.post(f"/api/admin/users/{_id(db, 'staff@example.com')}/suspend",
                     json={}).status_code == 200
    _as(db, "boss2@example.com", "superadmin")
    assert boss.post(f"/api/admin/users/{_id(db, 'boss2@example.com')}/suspend",
                     json={}).status_code == 403


# --- roles --------------------------------------------------------------------

def test_only_a_superadmin_names_administrators(boss, staff, customer, db) -> None:
    target = _id(db, "customer@example.com")
    assert staff.post(f"/api/admin/users/{target}/roles/admin/grant").status_code == 403

    r = boss.post(f"/api/admin/users/{target}/roles/admin/grant")
    assert r.status_code == 200 and "admin" in r.json()["user"]["roles"]
    assert customer.get("/api/admin/users").status_code == 200, \
        "admin routes read the roles from the database: no new sign-in needed"

    assert boss.post(f"/api/admin/users/{target}/roles/admin/revoke").status_code == 200
    assert customer.get("/api/admin/users").status_code == 403, \
        "revocation is immediate on admin routes, not after the access token lapses"


def test_superadmin_cannot_be_granted_over_http(boss, customer, db) -> None:
    target = _id(db, "customer@example.com")
    assert boss.post(f"/api/admin/users/{target}/roles/superadmin/grant").status_code == 403
    assert boss.post(f"/api/admin/users/{target}/roles/customer/revoke").status_code == 403


def test_the_detail_tells_the_interface_what_the_caller_may_do(staff, boss, customer, db) -> None:
    d = staff.get(f"/api/admin/users/{_id(db, 'customer@example.com')}").json()
    assert d["can_manage"] and not d["can_change_roles"]
    assert d["events"], "the account's history is shown to staff"
    d = staff.get(f"/api/admin/users/{_id(db, 'boss@example.com')}").json()
    assert not d["can_manage"]
    d = boss.get(f"/api/admin/users/{_id(db, 'customer@example.com')}").json()
    assert d["can_change_roles"]


def test_the_audit_list_names_who_did_what(staff, customer, db) -> None:
    staff.post(f"/api/admin/users/{_id(db, 'customer@example.com')}/suspend", json={})
    rows = staff.get("/api/admin/audit").json()
    top = rows[0]
    assert top["kind"] == "suspended"
    assert top["user_email"] == "customer@example.com"
    assert top["actor_email"] == "staff@example.com"


# --- metrics ------------------------------------------------------------------

def test_metrics_count_failures_and_activity(staff, customer, db) -> None:
    TestClient(app).post("/api/auth/sign-in",
                         json={"email": "customer@example.com", "password": "wrong password!"})
    m = staff.get("/api/admin/metrics").json()
    assert m["active_24h"] >= 2, "staff and customer both hold a fresh session"
    assert m["failed_sign_ins_24h"] >= 1
    assert any(c["label"] == "wrong_password" for c in m["failure_causes_7d"])
    assert len(m["daily"]) == 30 and m["daily"][-1]["failed"] >= 1
    assert m["verified_30d"] <= m["signups_30d"]


def test_metrics_are_staff_only(customer) -> None:
    assert customer.get("/api/admin/metrics").status_code == 403
