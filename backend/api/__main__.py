"""
Entry point: run the API, or export its schema.

    python -m backend.api --port 8000
    python -m backend.api --export-openapi web/openapi.json

BOUND TO LOCALHOST BY DEFAULT. The tunnel (ngrok) is the only way in during
frontend development, so the server itself never listens on the network. Pass
--host 0.0.0.0 only if you know why you are doing it.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("api")


def export_openapi(destination: Path) -> Path:
    """
    Write the OpenAPI schema — the file the frontend is given.

    Kept in the repository on purpose: it is a contract, and a contract that
    only exists in a running process cannot be reviewed in a diff.
    """
    from . import app

    destination.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    destination.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    log.info("written %s (%d paths)", destination, len(schema.get("paths", {})))
    return destination


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only track record API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="development autoreload")
    ap.add_argument("--export-openapi", metavar="PATH",
                    help="write the OpenAPI schema and exit")
    args = ap.parse_args()

    if args.export_openapi:
        export_openapi(Path(args.export_openapi))
        return

    import uvicorn

    from . import allowed_origins

    log.info("CORS origins: %s", ", ".join(allowed_origins()))
    log.info("docs on http://%s:%d/docs", args.host, args.port)
    uvicorn.run("backend.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
