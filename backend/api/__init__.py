"""
Read-only REST API over the track record.

WHAT IT IS FOR. A frontend that shows what the system predicted and how it is
doing, without being able to make it do anything. There is no POST, no PUT, no
DELETE — not "not yet", by design: a prediction counts only if it was written
before kickoff by a local process, and an HTTP write endpoint is exactly how
that guarantee gets lost.

THE GUARANTEE IS ENFORCED, NOT PROMISED
  - `_assert_read_only_routes()` fails at startup if any route declares a
    method other than GET/HEAD/OPTIONS;
  - `_guard_writes()` replaces pandas' `to_csv`/`to_parquet` and refuses any
    write under `track_record/` or `data/`, the same idiom `src/experiments/`
    already uses — a convention holds until someone copies a line from another
    module, a guard does not;
  - no model is ever loaded: heavy imports live inside functions, and
    `tests/test_api.py` asserts `lightgbm` is absent from `sys.modules`.

CACHING. The data changes twice a week, when `predict_round` and `close_round`
run. Responses carry `Cache-Control: public, max-age=300` plus an ETag derived
from the source files' mtimes, so a polling frontend gets 304s and the server
does no work.

Start it with:
    python -m backend.api --port 8000
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from goalmodel import config

from . import routes, store

log = logging.getLogger("api")

TITLE = "AI Naples — track record API"
VERSION = "1.0.0"

DESCRIPTION = """
Read-only view over the Serie A prediction track record.

**The production model is M1**: the bookmaker's opening line with the margin
removed. Fair odds and overround are exposed because they are descriptive;
expected value, stakes and betting advice are not exposed at all — an EV
computed from M1's probabilities against the very odds they come from is
circular by construction, and measured to be negative on every single bet.

Writing happens only from local scheduled commands, never over HTTP.
"""

# Comma-separated list, e.g. "http://localhost:5173,https://napoli.example".
# No wildcard default: an API that answers everyone by default is a decision
# nobody made.
CORS_ENV = "AI_NAPLES_CORS_ORIGINS"
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:3000", "http://127.0.0.1:3000")

CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=3600"

PROTECTED = (config.TRACK_RECORD.resolve(), config.DATA.resolve())


def allowed_origins() -> list[str]:
    raw = os.environ.get(CORS_ENV, "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or list(DEFAULT_ORIGINS)


def _guard_writes() -> None:
    """
    Refuse any write under track_record/ or data/ from inside this process.

    UNCHANGED BY AUTHENTICATION, deliberately. Authentication writes rows to
    Postgres, which this guard never covered and does not need to: what it
    protects is the registry on disk. Loosening it to let auth through would
    have been the easy mistake — auth does not need it loosened.

    Still partial, and still says so: it wraps `to_parquet` and `to_csv`, not
    `to_pickle`, `open()`, `shutil.copy` or pyarrow (security rule 9).
    """
    if getattr(pd.DataFrame.to_csv, "_api_guarded", False):
        return

    def _forbidden(kind: str, target) -> bool:
        try:
            path = str(Path(str(target)).resolve())
        except Exception:
            return False
        return any(path.startswith(str(root)) for root in PROTECTED)

    def _wrap(original, kind: str):
        def guarded(self, path_or_buf=None, *args, **kwargs):
            if path_or_buf is not None and _forbidden(kind, path_or_buf):
                raise PermissionError(
                    f"the API is read-only: refusing to write {path_or_buf}. "
                    f"The track record is written by predict_round and "
                    f"close_round, never by an HTTP request."
                )
            return original(self, path_or_buf, *args, **kwargs)
        guarded._api_guarded = True
        return guarded

    pd.DataFrame.to_csv = _wrap(pd.DataFrame.to_csv, "csv")
    pd.DataFrame.to_parquet = _wrap(pd.DataFrame.to_parquet, "parquet")


def config_digest() -> str:
    """
    A digest of every setting in `src/config.py`.

    Computed on each request, not once at startup, and from the values rather
    than the file: it is a few dozen constants, it costs nothing, and it lets a
    test change one and see the tag move. Every public constant enters, not a
    hand-picked list of "the ones the API uses" — that list would be exactly
    as incomplete as the ETag was, the day someone adds a constant.
    """
    semplici = (int, float, str, bool, tuple, list, dict)
    voci = sorted(
        (nome, repr(valore)) for nome, valore in vars(config).items()
        if nome.isupper() and isinstance(valore, semplici)
    )
    return hashlib.md5(repr(voci).encode(), usedforsecurity=False).hexdigest()[:8]


# The one prefix allowed to declare write methods. Signing in has to POST
# somewhere, and there is no way around that.
#
# THIS IS A NARROWING, NOT A RELAXATION, and the difference is the whole point.
# Before authentication existed the rule was "no write route at all". It is now
# "no write route except under /api/auth", which still guarantees the thing
# that mattered: **nothing reachable over HTTP can touch the track record.**
# The auth routes write to Postgres; the registry stays a set of files that
# only predict_round and close_round, running locally, ever open for writing.
#
# A prediction is worth something only if it was written before kick-off by a
# process that did not know the result. A write endpoint over the registry is
# precisely how that guarantee is lost, so the day someone needs one, the
# answer is still no.
WRITABLE_PREFIX = "/api/auth/"


def iter_routes(app: FastAPI) -> Iterator[tuple[str, set[str]]]:
    """
    Every route actually served, as `(path, methods)`.

    WHY THIS IS NOT JUST `app.routes`, AND WHY IT MATTERS. FastAPI 0.141
    changed `include_router`: instead of copying the router's routes into
    `app.routes`, it appends one `_IncludedRouter` wrapper. So `app.routes`
    now holds four entries — /docs, /redoc, /openapi.json and the oauth2
    redirect — and none of the endpoints.

    Anything that walked `app.routes` looking for endpoints therefore stopped
    seeing any, and **passed**. That is what happened to
    `_assert_read_only_routes` and to its test: both kept reporting no write
    routes because both were looking at a list that no longer contained
    routes. A guard that cannot fail is not a guard, and this one could not
    have failed since the FastAPI upgrade.

    The recursion is deliberate: a router included into a router nests the
    wrappers, and stopping at one level would reintroduce the same blindness
    one layer down. Inner paths already carry their prefix.
    """
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            yield route.path, set(methods)
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from iter_routes(inner)


def _assert_read_only_routes(app: FastAPI) -> None:
    """A write route outside /api/auth must never reach production."""
    allowed = {"GET", "HEAD", "OPTIONS"}
    offenders = [
        f"{sorted(methods - allowed)} {path}"
        for path, methods in iter_routes(app)
        if methods - allowed and not path.startswith(WRITABLE_PREFIX)
    ]
    if offenders:
        raise RuntimeError(
            f"only routes under {WRITABLE_PREFIX} may write, found: "
            + "; ".join(offenders))


def create_app() -> FastAPI:
    _guard_writes()
    app = FastAPI(title=TITLE, version=VERSION, description=DESCRIPTION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        # Credentials are on because the session lives in a cookie, and a
        # cross-origin request drops cookies without it. That is exactly why
        # `allow_origins` must stay an explicit list: browsers refuse the
        # combination of credentials and `*`, and a wildcard here would let any
        # site read an authenticated response.
        allow_credentials=True,
        # POST for /api/auth only. Every other route still rejects it at
        # startup, via _assert_read_only_routes.
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    # The schema digest is part of every ETag, so a change to a payload's shape
    # invalidates the browser's cache even though no data file moved. Without
    # it, a redeployed field would stay invisible behind a 304 until the next
    # Friday — which is exactly the kind of bug that gets blamed on the
    # frontend.
    schema_digest = hashlib.md5(
        json.dumps(app.openapi(), sort_keys=True).encode(), usedforsecurity=False
    ).hexdigest()[:8]

    def etag_for(request: Request) -> str | None:
        """
        ETag over: the schema, the configuration, the resource asked for, and
        the data on disk.

        The resource belongs in it because the tag is otherwise identical on
        every URL, and an entity tag that does not identify the entity is a
        trap waiting for the first caching proxy.

        The configuration belongs in it for the same reason as the schema, and
        it was missing until 22 September 2026: lowering
        `QUOTA_MINIMA_SELEZIONE` from 1.50 to 1.30 changed what /api/picks
        returns without touching a single data file, so the tag stayed the
        same, every revalidation got a 304, and the browser kept showing the
        old picks — forever, for the closed rounds, whose files never change
        again.
        """
        try:
            fingerprint = repr(store._fingerprint(store.default_sources()))
        except Exception:
            return None
        material = (f"{schema_digest}|{config_digest()}|"
                    f"{request.url.path}?{request.url.query}|{fingerprint}")
        return '"' + hashlib.md5(material.encode(), usedforsecurity=False).hexdigest()[:16] + '"'

    @app.middleware("http")
    async def cache_headers(request: Request, call_next):
        """
        Cache-Control plus a content-derived ETag.

        The data changes twice a week, when `predict_round` and `close_round`
        write, so a polling frontend gets 304 on nearly every request in
        between and the server does no work.
        """
        tag = etag_for(request)

        if tag and request.headers.get("if-none-match") == tag:
            # A 304 CARRIES NO BODY. uvicorn forces Content-Length to 0 on it
            # and raises "Response content longer than Content-Length" if
            # anything is written — `JSONResponse(content=None)` writes b"null"
            # and takes the request down. Starlette's TestClient does not
            # enforce that rule, so this only ever failed against the real
            # server: hence the empty-body assertion in tests/test_api.py.
            return Response(status_code=304,
                            headers={"ETag": tag, "Cache-Control": CACHE_CONTROL})

        response = await call_next(request)
        if request.method in ("GET", "HEAD") and response.status_code == 200:
            response.headers["Cache-Control"] = CACHE_CONTROL
            if tag:
                response.headers["ETag"] = tag
        return response

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request: Request, exc: FileNotFoundError):
        """Data that should exist but has not been built: 503, naming the fix."""
        log.warning("missing data for %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": f"data not available: {exc}. The track record is "
                               f"produced by 'python -m goalmodel.prediction.predict_round' and "
                               f"'python -m goalmodel.prediction.close_round'."},
        )

    app.include_router(routes.public)
    app.include_router(routes.router)

    # FAIL CLOSED. This import is NOT optional, and an earlier draft of it was
    # — wrapped in try/except ImportError so the API could still start without
    # the `auth` extra. That is exactly the wrong failure mode now that the
    # data routes require a permission: a missing dependency would take the
    # guards with it and serve everything to anyone, while looking healthy.
    #
    # A broken install must stop the process, not quietly widen access.
    from backend.auth import routes as auth_routes

    app.include_router(auth_routes.router)

    _assert_read_only_routes(app)
    return app


app = create_app()
