"""
Entry point for Vercel's `api` service. Nothing else should import it.

Vercel wants a FILE as entrypoint, not a `module:attribute` string: given
`backend.api:app` it looks for `backend/api.py`, which does not exist — the
API is the package `backend/api/`. Pointing it at `backend/api/__init__.py`
would load that file outside its package and break its relative imports
(`from . import routes, store`). This file re-exports the same `app` with an
absolute import, so the package is imported exactly as uvicorn imports it.
"""

from backend.api import app

__all__ = ["app"]
