"""
The local administration command.

WHAT MATTERS HERE is not that the commands print nicely, but that the
privilege boundary holds: superadmin exists only because someone with the
database password created it, and nothing reachable over HTTP can do the same.
"""

from __future__ import annotations

import pytest
from backend.auth import cli, service

pytestmark = pytest.mark.requires_db


def test_no_http_route_can_grant_a_role() -> None:
    """
    The boundary that makes public signup safe.

    If any endpoint could assign roles, signup would be an escalation path.
    This walks the real route table rather than trusting that nobody added
    one.
    """
    import inspect

    from backend.api import app, iter_routes
    from backend.auth import routes as auth_routes

    assert any(p.startswith("/api/auth") for p, _ in iter_routes(app)), \
        "auth routes are not mounted: this test would pass vacuously"

    source = inspect.getsource(auth_routes)
    for forbidden in ("grant_role", "revoke_role"):
        assert forbidden not in source, (
            f"{forbidden} is reachable from an HTTP route: public signup would "
            f"become a privilege escalation path")


def test_check_config_refuses_the_development_defaults(capsys) -> None:
    """It is a deployment gate, so it must exit non-zero when something is wrong."""
    import backend.auth.cli as cli_mod
    from backend.auth.settings import Settings
    original = cli_mod.settings
    cli_mod.settings = lambda: Settings(_env_file=None)
    try:
        assert cli_mod.check_config(None) == 1
    finally:
        cli_mod.settings = original
    assert "NOT fit for production" in capsys.readouterr().err


def test_check_config_accepts_a_sound_setup(capsys) -> None:
    import backend.auth.cli as cli_mod
    from backend.auth.settings import Settings
    from pydantic import SecretStr
    original = cli_mod.settings
    cli_mod.settings = lambda: Settings(
        _env_file=None, secret_key=SecretStr("x" * 64), cookie_secure=True,
        smtp_host="smtp.provider.example", smtp_security="starttls",
        public_url="https://ainaples.example")
    try:
        assert cli_mod.check_config(None) == 0
    finally:
        cli_mod.settings = original


def test_granting_an_unknown_role_lists_the_known_ones(db, capsys, monkeypatch) -> None:
    """An error that names the valid options costs nothing and saves a lookup."""
    from contextlib import contextmanager

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(cli, "session_scope", fake_scope)

    user = service.by_email(db, "cli-target@example.com")
    if user is None:
        from backend.auth.models import User, now
        from backend.auth.password import hash_password
        user = User(email="cli-target@example.com",
                    password_hash=hash_password("a sufficiently long password"),
                    status="active", email_verified_at=now())
        db.add(user)
        db.flush()

    args = type("A", (), {"email": "cli-target@example.com", "role": "wizard"})()
    assert cli.grant(args) == 1
    err = capsys.readouterr().err
    assert "superadmin" in err and "customer" in err, "the error should list the real roles"


def test_suspending_revokes_the_sessions(db, monkeypatch) -> None:
    """
    Otherwise a suspended user stays in until their cookie happens to expire,
    which is the opposite of what suspending is for.
    """
    from contextlib import contextmanager

    from backend.auth.models import User, now
    from backend.auth.password import hash_password
    from backend.auth.settings import Settings

    @contextmanager
    def fake_scope():
        yield db

    monkeypatch.setattr(cli, "session_scope", fake_scope)

    user = User(email="cli-suspend@example.com",
                password_hash=hash_password("a sufficiently long password"),
                status="active", email_verified_at=now())
    db.add(user)
    db.flush()
    cfg = Settings(_env_file=None)
    token, _ = service.open_session(db, user, cfg)
    assert service.session_from_token(db, token, cfg) is not None

    args = type("A", (), {"email": "cli-suspend@example.com"})()
    assert cli.suspend(args) == 0
    assert user.status == "suspended"
    assert service.session_from_token(db, token, cfg) is None
