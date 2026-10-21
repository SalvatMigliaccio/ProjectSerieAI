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

# Dependency metadata first, sources second, so editing a Python file does not
# invalidate the layer that installed the dependencies.
#
# `.[api,auth]` IS NOW ENOUGH, and it was not before. goalmodel used to declare
# soccerdata among its base dependencies, which reached seleniumbase and then
# PyAutoGUI - synthetic input and screen capture - inside a web-facing image.
# The base set is now what `import goalmodel` actually needs; lightgbm lives in
# `[ml]` and soccerdata in `[ingest]`, neither of which is installed here.
#
# An earlier version of this file listed the packages by hand and used
# `--no-deps`. That worked and was fragile: the list missed scikit-learn,
# because `models/baseline.py` imports it at module level and the API only
# reaches it through a lazy import inside a route. Declaring the split in
# pyproject means pip works it out instead of a human keeping a list current.
COPY pyproject.toml README.md ./
COPY src/goalmodel/__init__.py src/goalmodel/__init__.py
COPY backend/__init__.py backend/__init__.py
RUN pip install --no-cache-dir ".[api,auth]"

COPY src/ src/
COPY backend/ backend/
RUN pip install --no-cache-dir --no-deps .

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
# alembic.ini, WITHOUT WHICH THE DEPLOY CANNOT MIGRATE. It was missing from the
# first version of this file, and the symptom was not subtle but it was late:
# the stack came up healthy and `alembic -c alembic.ini upgrade head` - the very
# next command in docs/DEPLOY.md - answered "No 'script_location' key found in
# configuration", which reads like a broken config rather than an absent file.
COPY --chown=app:app alembic.ini alembic.ini
# manual/ is versioned and small: team name maps, derbies, coach changes. The
# API reads them through the results chain, so they are baked in rather than
# mounted - one less thing to get wrong on the server.
COPY --chown=app:app manual/ manual/

# The directories goalmodel expects, created here with the right owner. It also
# creates them on import, but that has to stay best-effort: the mounts below
# are read-only and the process is not root, so a mkdir that insisted would
# stop the API from starting at all - which is exactly what it did.
RUN mkdir -p /srv/data/raw /srv/data/interim /srv/data/processed /srv/track_record \
 && chown -R app:app /srv

# VERIFIED ON THE IMAGE THAT SHIPS, not on the builder. The first version of
# these checks ran in the builder stage, where `alembic.ini` had never been
# copied - so the alembic one failed for the wrong reason and the other two
# proved something about a layer that gets thrown away. What matters is this
# image.
#
# FAIL THE BUILD, NOT THE DEPLOY. With `--no-deps` a missing package does not
# surface until something imports it, which for a route nobody exercises in
# staging means in production.
# THE LAZY IMPORTS TOO, and that is the whole point of this step. Importing
# `backend.api` alone is not enough: the routes pull
# `goalmodel.prediction.backtest_log` from inside a function, which reaches
# `evaluation.evaluate` and then `models.baseline`, which needs scikit-learn at
# module level. An earlier build passed this check and then answered 500 on
# /api/health for exactly that reason. Everything the API can reach gets
# imported here, while the build can still fail.
RUN python - <<'PYEOF'
import importlib

# Eager: the application itself.
for name in ("backend.api", "backend.auth.routes", "backend.auth.cli"):
    importlib.import_module(name)

# Lazy: imported inside route handlers, so the line above never touches them.
# Keep this list in step with the `from goalmodel...` imports in backend/.
for name in ("goalmodel.prediction.backtest_log",
             "goalmodel.prediction.rounds",
             "goalmodel.reporting.sezioni",
             "goalmodel.evaluation.evaluate",
             "goalmodel.models.baseline"):
    importlib.import_module(name)

print("import graph ok, lazy imports included")
PYEOF

# Migrations reachable. Needs no database: it only proves the ini resolves and
# the script directory is present.
RUN python -c "\
from alembic.config import Config; \
from alembic.script import ScriptDirectory; \
n = len(ScriptDirectory.from_config(Config('alembic.ini')).get_revisions('heads')); \
print(f'migrations reachable, {n} head(s)')"

# And that the browser-automation chain really is absent, so nothing quietly
# reintroduces it.
RUN python - <<'PYEOF'
import importlib.util
import sys
unwanted = [n for n in ("pyautogui", "seleniumbase", "lightgbm", "matplotlib")
            if importlib.util.find_spec(n) is not None]
if unwanted:
    sys.exit(f"these must not be in the API image: {unwanted}")
print("no browser automation, no model library")
PYEOF

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
