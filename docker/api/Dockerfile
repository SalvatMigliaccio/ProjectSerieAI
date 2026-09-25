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

# Dependency metadata first, sources second. Editing a Python file then does
# not invalidate the layer that installed the dependencies, which is the
# difference between a 10-second rebuild and a 3-minute one.
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
