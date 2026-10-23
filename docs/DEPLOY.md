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

**If the network settings change, use `down` and then `up`, not `up -d`.** This
is a one-off for the release that pinned the subnet, and it is worth knowing
because of how it fails. `up -d` rebuilt the network and REWIRED the running
containers into it instead of recreating them, which drops their service
aliases: `db` stopped resolving, while the API still reported healthy — the
healthcheck hits `/api/health`, which does not touch the database. The visible
symptom was sign-in answering 500 with "failed to resolve host 'db'".

```bash
docker compose -f compose.prod.yaml down        # keeps the volumes
docker compose -f compose.prod.yaml up -d --build
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
- **The proxy is trusted by address, and only one address.** Caddy overwrites
  `X-Forwarded-For` with the real peer, and uvicorn believes that header only
  from `FORWARDED_ALLOW_IPS` — set to the web container's pinned address. Both
  values and the subnet they live in are in `compose.prod.yaml`, and
  `tests/test_deploy.py` fails if they stop agreeing.

  **The limit that remains**: this trusts whoever holds that address on the
  compose network. That is Caddy today, and the API publishes no port, so
  nothing outside the network can reach it to lie — but a second service added
  to this stack is a service that could forge a caller's address. If that ever
  happens, put the API on a network of its own with Caddy.
- **The image is 726 MB, 46 packages, and that is close to the floor.** What
  is left is pandas, numpy, scipy, pyarrow and scikit-learn, all of which the
  API imports on the path that answers a request. Browser automation is gone —
  `soccerdata -> seleniumbase -> PyAutoGUI` used to be installed here, which
  put synthetic input and screen capture inside an internet-facing container —
  and the build fails if any of it comes back.

  It went away by splitting `goalmodel`'s dependencies rather than by opting
  out of them: modelling is in `[ml]`, ingestion in `[ingest]`, and the image
  installs `.[api,auth]`. An earlier version listed the packages by hand with
  `--no-deps`, which was smaller and wrong: the list missed scikit-learn, the
  build passed, and `/api/health` answered 500 because a route reaches
  `models/baseline.py` through a lazy import.

---

# Deploying on Vercel

An alternative to the VPS, with the same split: **Vercel serves the dashboard
and the API; the pipeline stays on your machine.** `vercel.json` declares two
services in one project:

| service | what | public on |
|---|---|---|
| `api` | FastAPI, `backend.api:app` | `/api/*`, `/openapi.json` |
| `frontend` | the Vite build of `frontend/` | everything else |

No bindings: the frontend is static and the browser calls `/api` on the same
origin, through the public route. `/docs` (Swagger) is not public, as behind
Caddy.

## Once

1. **Database.** Add Neon from the Vercel Marketplace and connect it to the
   project. Neon sets its own `DATABASE_URL`; the API reads
   `AI_NAPLES_DATABASE_URL`, with the psycopg driver. Copy the **pooled**
   connection string (host with `-pooler`: serverless instances open many
   short connections) and change the scheme:

   ```
   postgresql+psycopg://USER:PASSWORD@HOST-pooler.REGION.aws.neon.tech/DB?sslmode=require
   ```

2. **Environment variables** (Project → Settings → Environment Variables,
   Production):

   | variable | value |
   |---|---|
   | `AI_NAPLES_DATABASE_URL` | the string above |
   | `AI_NAPLES_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
   | `AI_NAPLES_COOKIE_SECURE` | `1` |
   | `AI_NAPLES_PUBLIC_URL` | `https://<your-domain>` — builds the links in emails |
   | `AI_NAPLES_SMTP_*` | a real provider, `starttls` or `ssl` |

   Not needed: `AI_NAPLES_ROOT` (on Vercel the package uses the working
   directory, see `src/goalmodel/config.py`) and `AI_NAPLES_CORS_ORIGINS`
   (one origin, no CORS).

3. **Migrations**, from your machine against Neon — Vercel runs no command at
   deploy, and `backend/auth/migrations` is not even uploaded:

   ```bash
   AI_NAPLES_DATABASE_URL="<the string above>" alembic -c alembic.ini upgrade head
   AI_NAPLES_DATABASE_URL="<...>" python -m backend.auth.cli create-superadmin --email you@example.com
   ```

## Deploying: from the pipeline machine, after every matchday

`data/` and `experiments/output/` are not in git, by design, and a deploy from
Git would ship an API with no data. **Deploy with the CLI from the machine
where `predict_round` and `close_round` run**: the CLI ignores `.gitignore`
and uploads what `.vercelignore` allows — an allowlist, so `.env` never leaves.

```bash
npm i -g vercel          # once
vercel link              # once, picks the project
vercel deploy --prod     # after predict-round and after close-round
```

The data on Vercel is a snapshot: it changes only with the next deploy. A
matchday predicted on Friday reaches the dashboard when the Friday deploy
does.

**Check the first build log**: it must show `fastapi`, `sqlalchemy` and
`pyjwt` being installed. They come from the `api` and `auth` extras through
`requirements.txt` (`.[api,auth]`); Vercel documents no other way to ask
pyproject for extras.

Locally, `vercel dev` runs both services with the same routing.

## What changes against the VPS

| thing | VPS | Vercel |
|---|---|---|
| Track record | mounted read-only | a read-only copy in the bundle; the guards in `backend/api` still refuse writes |
| Caller's address | from Caddy via `FORWARDED_ALLOW_IPS` | from `x-real-ip`, trusted only when `VERCEL` is set (`client_ip`) |
| Rate limits | one process, one counter | **one counter per instance**: the effective limit grows with the instances Vercel starts. The account lockout, in Postgres, is unaffected |
| Scheduler | the host | still the host. Vercel runs no pipeline step |
