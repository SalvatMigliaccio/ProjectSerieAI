# The read-only API.
#
# MULTI-STAGE because the build needs a compiler and the runtime does not.
# psycopg and argon2-cffi pull in build tooling that has no business being in a
# running container: it is attack surface, and it doubles the image.

FROM python:3.14-slim AS builder

# Never write .pyc into the build layer, and do not buffer logs: a crashed
# container that swallowed its last log line is the hardest kind to diagnose.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# A virtualenv rather than --user: it copies to the runtime stage as one
# directory, with no guessing about where pip put things.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# THE API'S REAL DEPENDENCIES, LISTED EXPLICITLY. `pip install ".[api,auth]"`
# would be shorter and is what this file did first. It is also wrong, and not
# merely heavy:
#
#   goalmodel -> soccerdata -> seleniumbase -> PyAutoGUI
#                                              -> pyscreeze, pygetwindow, mouseinfo
#
# PyAutoGUI injects synthetic mouse and keyboard events and captures the
# screen. It belongs to the ingestion pipeline, which drives a real browser
# against WhoScored; it has no business inside an internet-facing container
# that only reads parquet and answers JSON. The same chain also brings a whole
# browser-automation framework, LightGBM, matplotlib, scikit-learn and
# statsmodels, none of which the API imports - `tests/test_api.py` asserts as
# much for LightGBM.
#
# So the runtime set is spelled out and `goalmodel` goes in with `--no-deps`.
# `tests/test_deploy.py` checks this list against pyproject so the two cannot
# drift silently. The cleaner fix is moving ingestion and modelling out of
# `goalmodel`'s base dependencies, which changes what `pip install goalmodel`
# gives everyone and is therefore a decision, not a Dockerfile edit.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir \
    "fastapi>=0.110,<1" \
    "uvicorn[standard]>=0.27,<1" \
    "pandas>=3.0,<4" \
    "numpy>=2.5,<3" \
    "pyarrow>=25,<26" \
    "scipy>=1.18,<2" \
    "psycopg[binary]>=3.2,<4" \
    "sqlalchemy>=2.0,<3" \
    "alembic>=1.14,<2" \
    "argon2-cffi>=23.1,<26" \
    "itsdangerous>=2.2,<3" \
    "email-validator>=2.2,<3" \
    "pydantic-settings>=2.7,<3"

COPY src/ src/
COPY backend/ backend/
RUN pip install --no-cache-dir --no-deps .

# FAIL THE BUILD, NOT THE DEPLOY. With `--no-deps`, a missing package does not
# surface until something imports it at runtime - which, for a route nobody
# exercises in staging, means in production. Importing the app here walks the
# whole graph while the build can still fail, and the second line proves the
# browser-automation chain really is absent.
RUN python -c "import backend.api, backend.auth.routes, backend.auth.cli; print('import graph ok')"
RUN python - <<'PYEOF'
import importlib.util
import sys
unwanted = [n for n in ("pyautogui", "seleniumbase", "lightgbm", "matplotlib")
            if importlib.util.find_spec(n) is not None]
if unwanted:
    sys.exit(f"these must not be in the API image: {unwanted}")
print("no browser automation, no model library")
PYEOF

# ---------------------------------------------------------------------------

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# NON-ROOT, and not as a formality. The API mounts the track record and the
# data directory; running as root means a container escape or a path bug writes
# as root on the host. The registry is the one thing in this project that does
# not regenerate.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv
COPY --chown=app:app backend/ backend/
COPY --chown=app:app src/ src/

USER app

EXPOSE 8000

# The healthcheck hits /api/health, which is public precisely so that a monitor
# does not depend on the thing it monitors.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

# One worker on purpose. The rate limiter keeps its counters in process memory,
# so N workers means N independent limiters and N times the configured limit.
# Raising this requires moving the limiter to Redis first — see
# backend/auth/ratelimit.py, which states that ceiling.
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
