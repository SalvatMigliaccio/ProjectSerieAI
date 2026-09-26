"""
The deployment files, checked for the things that are silent when wrong.

An image that ships `.env` still runs. A compose file that publishes Postgres
on a public VPS still serves the site. A container that mounts the track record
writable still answers every request correctly. None of these fail visibly —
they fail the day someone else notices.
"""

from __future__ import annotations

import ipaddress
import re
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
# The dependency split
# ---------------------------------------------------------------------------

def _pyproject() -> dict:
    import tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_scraping_stack_is_optional() -> None:
    """
    soccerdata reaches seleniumbase and then PyAutoGUI - synthetic mouse and
    keyboard events, plus screen capture. It is what the ingestion pipeline
    uses to drive a browser against WhoScored, and for two years it was a base
    dependency of goalmodel, so anything installing the library got it.

    That surfaced when a deployment image turned out to be installing it into
    an internet-facing container whose whole job is reading parquet.
    """
    project = _pyproject()["project"]
    base = " ".join(project["dependencies"])
    assert "soccerdata" not in base, \
        "soccerdata is back in the base dependencies: every consumer now gets " \
        "seleniumbase, selenium and PyAutoGUI"
    assert "soccerdata" in " ".join(project["optional-dependencies"]["ingest"])


def test_lightgbm_is_optional() -> None:
    """
    Only `models/gbm.py` imports it, and the API never reaches that module -
    tests/test_api.py asserts it stays out of sys.modules.
    """
    project = _pyproject()["project"]
    assert "lightgbm" not in " ".join(project["dependencies"])
    assert "lightgbm" in " ".join(project["optional-dependencies"]["ml"])


def test_the_base_set_keeps_what_the_api_really_needs() -> None:
    """
    The counterpart to the two tests above, and the one that catches
    over-trimming.

    scikit-learn is the example worth naming: `models/baseline.py` imports
    PoissonRegressor at module level, and the API reaches it through a lazy
    import inside a route. A hand-written runtime list left it out, the image
    built cleanly, and /api/health answered 500.
    """
    base = " ".join(_pyproject()["project"]["dependencies"])
    for needed in ("pandas", "numpy", "pyarrow", "scipy", "scikit-learn"):
        assert needed in base, (
            f"{needed} left the base dependencies: the API imports it through "
            f"goalmodel and would fail at runtime, not at install time")


def test_the_image_installs_the_extras_instead_of_a_handwritten_list() -> None:
    """
    The list used to be maintained by hand with `--no-deps`. It worked until it
    silently omitted scikit-learn. pip resolving a declared extra cannot drift
    the way a comment in a Dockerfile does.
    """
    body = API_DOCKERFILE.read_text(encoding="utf-8")
    assert '".[api,auth]"' in body, \
        "the image no longer installs the declared extras"


def test_the_build_imports_the_lazy_modules_too() -> None:
    """
    Importing `backend.api` alone is not enough: the routes pull
    goalmodel.prediction.backtest_log from inside a function, which reaches
    models.baseline and scikit-learn. That is exactly how the 500 got past an
    earlier version of this check.
    """
    body = API_DOCKERFILE.read_text(encoding="utf-8")
    for lazy in ("goalmodel.prediction.backtest_log",
                 "goalmodel.reporting.sezioni",
                 "goalmodel.models.baseline"):
        assert lazy in body, f"the build does not import {lazy}"


def test_the_container_is_told_where_the_repository_root_is() -> None:
    """
    goalmodel derives its paths from its own __file__, which is right from a
    checkout and wrong from an installed package: in the image it resolves to
    the Python directory, so DATA pointed inside site-packages while the files
    were mounted at /srv/data. The API then reported a missing dataset, which
    blames the data rather than the path.
    """
    assert "AI_NAPLES_ROOT: /srv" in PROD.read_text(encoding="utf-8")


def test_the_image_carries_alembic_ini() -> None:
    """
    Without it the deploy cannot migrate, and the failure is late and
    misleading: the stack comes up healthy and the documented
    `alembic -c alembic.ini upgrade head` reports a missing `script_location`
    key, which reads like a broken configuration rather than an absent file.
    """
    body = API_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --chown=app:app alembic.ini" in body, \
        "alembic.ini is not copied into the API image: migrations cannot run"
    assert "ScriptDirectory.from_config" in body, \
        "the build does not verify that the migrations are reachable"


def test_alembic_ini_is_not_excluded_from_the_build_context() -> None:
    """A COPY of a file the context drops fails the build, but only late."""
    ignored = _lines(DOCKERIGNORE)
    assert "alembic.ini" not in ignored
    assert "*.ini" not in ignored


def test_the_api_trusts_exactly_the_proxy_it_sits_behind() -> None:
    """
    Caddy sends X-Forwarded-For, but uvicorn believes it only from an address
    in FORWARDED_ALLOW_IPS, whose default is 127.0.0.1 — nobody, behind a
    container. The header was therefore dropped and every caller looked like
    the proxy: audit rows recorded Caddy, and the per-address rate limiter
    became a single global bucket, where twenty sign-in attempts from anyone
    locked out everyone.

    Nothing about that is visible in a response, which is why it is a test.
    The three values have to agree, and they live in three places, so this
    checks the agreement rather than any one of them:
      - the API trusts the web container's address,
      - the web container is pinned to it,
      - and it is inside the subnet the stack declares.
    """
    body = PROD.read_text(encoding="utf-8")

    trusted = re.search(r"FORWARDED_ALLOW_IPS: \$\{WEB_ADDRESS:-([\d.]+)\}", body)
    pinned = re.search(r"ipv4_address: \$\{WEB_ADDRESS:-([\d.]+)\}", body)
    subnet = re.search(r"subnet: \$\{COMPOSE_SUBNET:-([\d./]+)\}", body)
    assert trusted and pinned and subnet, \
        "the proxy-trust settings are not all in compose.prod.yaml"
    assert trusted.group(1) == pinned.group(1), \
        f"the API trusts {trusted.group(1)} but web is pinned to {pinned.group(1)}"
    assert ipaddress.ip_address(pinned.group(1)) in ipaddress.ip_network(subnet.group(1)), \
        f"{pinned.group(1)} is outside {subnet.group(1)}: the container will not start"

    # And the proxy has to SET the header rather than append to it: appending
    # would leave a client-supplied value in the list, which is the forgery
    # this whole arrangement is supposed to close.
    assert "header_up X-Forwarded-For {remote_host}" in CADDYFILE.read_text(encoding="utf-8"), \
        "Caddy does not overwrite X-Forwarded-For with the real peer"
