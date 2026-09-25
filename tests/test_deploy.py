"""
The deployment files, checked for the things that are silent when wrong.

An image that ships `.env` still runs. A compose file that publishes Postgres
on a public VPS still serves the site. A container that mounts the track record
writable still answers every request correctly. None of these fail visibly —
they fail the day someone else notices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = ROOT / ".dockerignore"
API_DOCKERFILE = ROOT / "docker" / "api" / "Dockerfile"
WEB_DOCKERFILE = ROOT / "docker" / "web" / "Dockerfile"
CADDYFILE = ROOT / "docker" / "web" / "Caddyfile"
PROD = ROOT / "compose.prod.yaml"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("pattern", [".env", ".git"])
def test_the_build_context_excludes_secrets(pattern: str) -> None:
    """
    `.env` holds the database password and the signing key. `.git` is worse:
    it carries every value ever committed, so removing a secret from the
    working tree does not remove it from what would be copied into a layer.
    """
    assert pattern in _lines(DOCKERIGNORE), (
        f"{pattern} is not in .dockerignore: it would be sent to the daemon "
        f"and can end up readable in an image layer")


def test_the_track_record_is_never_copied_into_the_image() -> None:
    """
    It must stay the file on the host, written by the local commands. A copy
    baked into an image is a second registry that silently diverges.
    """
    assert "track_record/" in _lines(DOCKERIGNORE)


def test_the_api_image_does_not_run_as_root() -> None:
    """
    It mounts the track record. Root in a container means a path bug or an
    escape writes as root on the host, and the registry is the one thing here
    that does not regenerate.
    """
    body = API_DOCKERFILE.read_text(encoding="utf-8")
    assert "USER app" in body, "the API image never drops privileges"
    # The USER instruction must come before CMD, or the process still starts
    # as root and the line is decoration.
    assert body.index("USER app") < body.index("CMD"), \
        "USER comes after CMD: the process would still start as root"


def test_the_api_runs_a_single_worker() -> None:
    """
    The rate limiter keeps its counters in process memory, so N workers means N
    independent limiters and N times the configured limit. Raising this needs
    Redis first; the ceiling is stated in backend/auth/ratelimit.py.
    """
    assert '"--workers", "1"' in API_DOCKERFILE.read_text(encoding="utf-8")


def test_postgres_publishes_no_port_in_production() -> None:
    """
    A database mapped to a public VPS is found by scanners within hours, and an
    accidental `ports:` entry is the usual cause. Inside the compose network
    the API reaches it by service name without any mapping.
    """
    body = PROD.read_text(encoding="utf-8")
    db_block = body[body.index("  db:"):body.index("  api:")]
    assert "ports:" not in db_block, \
        "compose.prod.yaml publishes the database port"


def test_the_mounts_are_read_only() -> None:
    """
    This is what makes 'no HTTP route can write the registry' true at the
    kernel rather than only inside the application guards.
    """
    body = PROD.read_text(encoding="utf-8")
    for mount in ("/srv/track_record:ro", "/srv/data:ro"):
        assert mount in body, f"{mount} is not mounted read-only in production"


def test_production_forces_a_secure_cookie() -> None:
    """
    Behind HTTPS the session cookie must carry Secure, or it travels in the
    clear the first time anything answers over http.
    """
    assert 'AI_NAPLES_COOKIE_SECURE: "1"' in PROD.read_text(encoding="utf-8")


def test_mailpit_is_not_in_the_production_stack() -> None:
    """
    It captures everything and delivers nothing. In production that means every
    verification and reset email silently fails to arrive, while the API
    reports success.
    """
    # The image reference, not the word: the file mentions Mailpit in a comment
    # explaining exactly why it is not here, and matching on prose would make
    # this test fail for saying the right thing.
    assert "axllent/mailpit" not in PROD.read_text(encoding="utf-8"), \
        "compose.prod.yaml runs a mail catcher"


def test_the_api_and_the_dashboard_share_one_origin() -> None:
    """
    Caddy proxies /api, so the session cookie is same-origin: no preflight, no
    second hostname to keep in the allowed list, and SameSite=Lax does its job.
    """
    body = CADDYFILE.read_text(encoding="utf-8")
    assert "handle /api/*" in body
    assert "reverse_proxy api:8000" in body


def test_the_dashboard_sends_the_security_headers() -> None:
    body = CADDYFILE.read_text(encoding="utf-8")
    for header in ("X-Frame-Options", "X-Content-Type-Options",
                   "Content-Security-Policy", "Strict-Transport-Security"):
        assert header in body, f"{header} is missing from the Caddyfile"


def test_the_frontend_build_uses_the_lockfile() -> None:
    """
    `npm ci` installs exactly what is pinned and fails when package.json and
    the lockfile disagree. `npm install` would quietly resolve something else,
    which is how a build stops matching what was tested.
    """
    assert "npm ci" in WEB_DOCKERFILE.read_text(encoding="utf-8")


def test_the_example_env_carries_no_real_secret() -> None:
    """The example file is committed, so anything filled in here is published."""
    body = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
    for line in body.splitlines():
        if line.startswith(("POSTGRES_PASSWORD=", "AI_NAPLES_SECRET_KEY=",
                            "AI_NAPLES_SMTP_PASSWORD=")):
            _, _, value = line.partition("=")
            assert value.strip() == "", f"a value is filled in: {line.split('=')[0]}"


# ---------------------------------------------------------------------------
# The Dockerfile's explicit dependency list
# ---------------------------------------------------------------------------

def _docker_pins() -> dict[str, str]:
    """The `name -> specifier` pairs from the API image's pip install step."""
    import re

    body = API_DOCKERFILE.read_text(encoding="utf-8")
    found = {}
    for raw in re.findall(r'"([a-zA-Z0-9_.\[\]-]+(?:[<>=!,.0-9]+))"', body):
        match = re.match(r"^([a-zA-Z0-9_.-]+)(\[[a-z,]+\])?(.*)$", raw)
        if match and match.group(3):
            found[match.group(1).lower()] = match.group(3)
    return found


def _pyproject_pins() -> dict[str, str]:
    import re
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(data["project"]["dependencies"])
    for extra in data["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)

    pins = {}
    for raw in declared:
        match = re.match(r"^([a-zA-Z0-9_.-]+)(\[[a-z,]+\])?(.*)$", raw)
        if match:
            pins[match.group(1).lower()] = match.group(3)
    return pins


def test_the_image_pins_match_pyproject() -> None:
    """
    The Dockerfile lists the API's dependencies by hand, so the versions exist
    in two places and can drift.

    WHY THEY ARE DUPLICATED AT ALL. `pip install ".[api,auth]"` drags in
    goalmodel's whole base set, and through soccerdata and seleniumbase that
    includes PyAutoGUI - synthetic input and screen capture - inside an
    internet-facing container. The list is explicit to keep that out.

    Duplicating a version is acceptable; letting the two copies disagree is
    not, because the image would then run something the suite never tested.
    """
    docker, project = _docker_pins(), _pyproject_pins()
    assert docker, "no pinned dependencies found in the API Dockerfile"

    mismatched = {
        name: (spec, project[name])
        for name, spec in docker.items()
        if name in project and project[name] != spec
    }
    assert not mismatched, (
        "the API image pins differ from pyproject.toml "
        f"(name: image, pyproject): {mismatched}")

    unknown = sorted(set(docker) - set(project))
    assert not unknown, (
        f"the image installs packages pyproject does not declare: {unknown}. "
        f"Either add them to pyproject or drop them from the image.")


def test_the_image_refuses_the_browser_automation_chain() -> None:
    """
    A build-time check, not a runtime hope.

    PyAutoGUI arrives through goalmodel -> soccerdata -> seleniumbase. It
    injects mouse and keyboard events and captures the screen; it is what the
    ingestion pipeline uses to drive a browser against WhoScored, and it is
    the same library whose Wayland portal prompts looked like somebody
    requesting remote access to the machine. None of it belongs in a container
    that reads parquet and answers JSON.
    """
    body = API_DOCKERFILE.read_text(encoding="utf-8")
    assert "--no-deps" in body, \
        "without --no-deps the image pulls goalmodel's whole dependency tree"
    for name in ("pyautogui", "seleniumbase", "lightgbm"):
        assert name in body, (
            f"the build does not verify that {name} is absent: with --no-deps "
            f"nothing else would notice if it came back")


def test_the_build_walks_the_import_graph() -> None:
    """
    `--no-deps` moves a missing package from build time to runtime, which for a
    route nobody exercises in staging means production. Importing the app
    during the build brings that back to where it can still fail.
    """
    assert "import backend.api, backend.auth.routes, backend.auth.cli" in \
        API_DOCKERFILE.read_text(encoding="utf-8")
