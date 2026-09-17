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
import logging
import os

import json

import pandas as pd
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import config
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
    """Refuse any write under track_record/ or data/ from inside this process."""
    if getattr(pd.DataFrame.to_csv, "_api_guarded", False):
        return

    def _forbidden(kind: str, target) -> bool:
        try:
            path = os.path.abspath(str(target))
        except Exception:  # noqa: BLE001
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


def _assert_read_only_routes(app: FastAPI) -> None:
    """A write route must never reach production, so fail at startup instead."""
    allowed = {"GET", "HEAD", "OPTIONS"}
    offenders = [
        f"{sorted(set(r.methods) - allowed)} {r.path}"
        for r in app.routes
        if getattr(r, "methods", None) and set(r.methods) - allowed
    ]
    if offenders:
        raise RuntimeError(
            "the API must expose read-only routes, found: " + "; ".join(offenders))


def create_app() -> FastAPI:
    _guard_writes()
    app = FastAPI(title=TITLE, version=VERSION, description=DESCRIPTION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # The schema digest is part of every ETag, so a change to a payload's shape
    # invalidates the browser's cache even though no data file moved. Without
    # it, a redeployed field would stay invisible behind a 304 until the next
    # Friday — which is exactly the kind of bug that gets blamed on the
    # frontend.
    schema_digest = hashlib.md5(
        json.dumps(app.openapi(), sort_keys=True).encode()
    ).hexdigest()[:8]

    def etag_for(request: Request) -> str | None:
        """
        ETag over: the schema, the resource asked for, and the data on disk.

        The resource belongs in it because the tag is otherwise identical on
        every URL, and an entity tag that does not identify the entity is a
        trap waiting for the first caching proxy.
        """
        try:
            fingerprint = repr(store._fingerprint(store.default_sources()))
        except Exception:  # noqa: BLE001 - never fail a request over an ETag
            return None
        material = f"{schema_digest}|{request.url.path}?{request.url.query}|{fingerprint}"
        return '"' + hashlib.md5(material.encode()).hexdigest()[:16] + '"'

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
                               f"produced by 'python -m src.predict_round' and "
                               f"'python -m src.close_round'."},
        )

    app.include_router(routes.router)
    _assert_read_only_routes(app)
    return app


app = create_app()
