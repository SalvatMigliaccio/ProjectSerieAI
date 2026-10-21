"""
The rate limits: that they bite, that they say when to come back, and that
they forget.

The limiter itself is plain Python and needs nothing; the HTTP checks reuse
the fixtures of `test_auth_http` and therefore need Postgres.
"""

from __future__ import annotations

from backend.auth.ratelimit import (
    RateLimiter,
    api_limiter,
    password_change_limiter,
    refresh_limiter,
)
from tests.test_auth_http import GOOD, _register_and_verify, client, post  # noqa: F401


def test_the_window_bites_and_names_the_wait() -> None:
    lim = RateLimiter(limit=3, window_seconds=60)
    assert all(lim.allow("a") for _ in range(3))
    assert not lim.allow("a")
    assert 1 <= lim.retry_after("a") <= 61
    assert lim.allow("b"), "one address does not spend another's budget"
    assert lim.retry_after("b") == 0


def test_it_forgets_without_being_asked() -> None:
    """`prune` used to exist and never run: one entry per address, forever."""
    lim = RateLimiter(limit=1, window_seconds=0.0)
    lim.PRUNE_EVERY = 50
    for i in range(200):
        lim.allow(f"10.0.{i // 256}.{i % 256}")
    assert len(lim._hits) < 60


def test_the_whole_api_answers_429_with_retry_after(client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(api_limiter, "limit", 3)
    for _ in range(3):
        assert client.get("/api/picks/2026-27").status_code == 200
    r = client.get("/api/picks/2026-27")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert client.get("/api/health").status_code == 200, "monitors are not counted"


def test_refresh_is_limited(client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(refresh_limiter, "limit", 2)
    codes = [client.post("/api/auth/refresh").status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_password_change_is_limited_per_account(client, db, monkeypatch) -> None:  # noqa: F811
    """A stolen access token must not buy unlimited guesses at the password."""
    monkeypatch.setattr(password_change_limiter, "limit", 2)
    _register_and_verify(client, db, "limite@example.com")
    body = {"current_password": "wrong-wrong-wrong", "new_password": "another long password"}
    codes = [client.post("/api/auth/change-password", json=body).status_code for _ in range(3)]
    assert codes == [403, 403, 429]
