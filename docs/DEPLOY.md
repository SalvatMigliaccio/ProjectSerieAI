# Deploying on a VPS

Written in English, like everything new in this project from 25 September 2026.

The stack is four containers: Caddy serving the dashboard and proxying `/api`,
the API, Postgres, and nothing else. **The pipeline is not containerised and
must not be** — `predict_round` and `close_round` write the track record, and a
prediction is worth something only if a local process wrote it before kick-off.
They run on the host; the containers only ever read what they produce.

## Once, on the server

```bash
git clone <repo> && cd ProjectSerieAI
cp .env.prod.example .env
# fill it in: DOMAIN, the Postgres password, the secret key, real SMTP
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # AI_NAPLES_SECRET_KEY
```

`DOMAIN` must already resolve to this server. Caddy asks Let's Encrypt for a
certificate on first start, and a name that does not point here fails — after
a few attempts you hit the rate limit and wait an hour.

## Start it

```bash
docker compose -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml exec api alembic -c alembic.ini upgrade head
docker compose -f compose.prod.yaml exec api python -m backend.auth.cli check-config
docker compose -f compose.prod.yaml exec api python -m backend.auth.cli create-superadmin --email you@example.com
```

`check-config` exits non-zero when the configuration is unfit. Treat a non-zero
exit as a failed deploy, not a warning.

## What protects what

| thing | how |
|---|---|
| The track record | mounted `:ro`. Not a guard in code — the kernel refuses the write |
| `data/` | mounted `:ro`. The pipeline on the host owns it |
| Postgres | no published port. Reachable only inside the compose network |
| The API | non-root, `no-new-privileges`, no published port; only Caddy reaches it |
| The session cookie | `Secure` forced on in `compose.prod.yaml`, HTTPS from Caddy |
| Secrets | environment only. `.dockerignore` keeps `.env` and `.git` out of the context |

`tests/test_deploy.py` asserts each of these against the files, because every
one of them is silent when wrong: an image that ships `.env` still runs, a
published database port still serves the site.

## Updating

```bash
git pull
docker compose -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml exec api alembic -c alembic.ini upgrade head
```

Migrations run after the new image is up. They are written to be reversible —
`alembic downgrade -1` — but a downgrade that drops a column drops the data in
it, so take a dump first:

```bash
docker compose -f compose.prod.yaml exec db pg_dump -U ainaples ainaples > backup.sql
```

## The pipeline, on the host

Unchanged, and deliberately outside Docker:

```bash
goalmodel predict-round     # Friday, once the odds are published
goalmodel close-round       # once every match of the round has a result
```

They write `track_record/` and `data/`, which the containers read. Nothing over
HTTP can write either.

## Known limits, stated rather than discovered later

- **One API worker.** The rate limiter counts in process memory, so two workers
  means two independent limiters and twice the configured limit. Raising it
  needs the limiter moved to Redis first; `backend/auth/ratelimit.py` says so.
- **`X-Forwarded-For` is not trusted.** Caddy sets it, but the application
  reads the socket address, so audit rows record the proxy. Fixing it properly
  means an explicit "trusted proxy" setting rather than believing a header
  anyone can send.
- **The API image carries the full `goalmodel` dependency set**, LightGBM
  included, although `tests/test_api.py` asserts the API never imports it. It
  is a consequence of `goalmodel` declaring those as hard dependencies. It
  costs image size and build time, not correctness.
