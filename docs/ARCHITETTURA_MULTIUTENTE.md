# Da processo locale a servizio multiutente

## Contesto

Oggi AI_Naples è un sistema a un utente solo: una pipeline Python che gira sul
PC di casa dal Task Scheduler, scrive CSV e Parquet, e una API FastAPI **in
sola lettura** che quei file li rilegge con pandas a ogni richiesta. Il
frontend React parla solo GET.

Si vuole un servizio pubblico con utenti: account e preferenze, schedine
personali, notifiche, ruoli di amministrazione. Con accesso via JWT, Redis per
la cache, database per i dati, su un VPS con Docker, per migliaia di utenti.

**Il punto delicato non è tecnico, è una garanzia.** La regola scritta in
`CLAUDE.md` — *«il track record si scrive solo da processi locali, mai da una
richiesta HTTP»* — oggi è tenuta in piedi da tre guardie:

| guardia | dove | cosa fa |
|---|---|---|
| `_guard_writes()` | `backend/api/__init__.py:80-105` | sostituisce `to_csv`/`to_parquet` di pandas e rifiuta ogni scrittura sotto `track_record/` e `data/` |
| `_assert_read_only_routes()` | `backend/api/__init__.py:108-118` | l'import del package fallisce se una rotta dichiara un metodo diverso da GET |
| CORS `allow_methods=["GET"]` | `backend/api/__init__.py:129` | il browser blocca il preflight di qualunque scrittura |

Nel momento in cui l'API impara a scrivere per registrare un utente, «l'API non
scrive» smette di essere vero e diventa «l'API scrive solo certe cose» — che è
una convenzione, non una garanzia. **Tutto questo piano ruota attorno a come
sostituire quelle guardie con qualcosa che non dipenda dalla disciplina di chi
scrive il codice.**

**Dimensionamento, misurato:** il registro ha 27 righe di dati, tutti i dati
stanno in ~10 MB. Il database non serve per il volume. Serve per gli utenti,
per la concorrenza, e per togliere pandas dal percorso di ogni richiesta.

---

## 1. Il principio: due domini, due ruoli di database

Il database ha **due schemi**, e l'API si collega con un utente che sul primo
può solo leggere:

```
core.*   dati del modello      API: SELECT          caricatore: INSERT/UPDATE
app.*    dati degli utenti     API: SELECT/INSERT/UPDATE/DELETE
```

```sql
-- in una migrazione, non a mano
GRANT USAGE ON SCHEMA core TO ainaples_api;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO ainaples_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO ainaples_api;
-- e NESSUN insert/update/delete: il privilegio non c'è, non "non si usa"
GRANT USAGE ON SCHEMA app TO ainaples_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ainaples_api;
```

Tre ruoli in tutto: `ainaples_api`, `ainaples_loader` (scrive `core`, non vede
`app`), `ainaples_migrator` (proprietario, fa la DDL). L'API non conosce
nemmeno la password degli altri due.

**Perché così e non con un controllo nel codice:** un controllo nel codice lo
si aggira per distrazione, sei mesi dopo, in una funzione che sembra innocua.
Un `INSERT` senza privilegio solleva `InsufficientPrivilege` dal database,
sempre, anche dentro un `try` scritto male. È lo stesso ragionamento che il
progetto già applica con `proteggi_produzione()` negli esperimenti: *la
guardia, non la promessa*.

### Cosa sostituisce le guardie attuali

| oggi | domani |
|---|---|
| `_assert_read_only_routes` | `_assert_write_routes_are_scoped`: una rotta non-GET è ammessa **solo** sotto `/api/auth` o `/api/me`; altrimenti `RuntimeError` all'avvio, come adesso |
| `test_read_only_routes` | `test_write_routes_are_scoped`: stessa regola, più l'elenco esplicito delle rotte di scrittura attese |
| `test_writes_are_refused` (monkey-patch pandas) | `test_api_role_cannot_write_core`: con la connessione dell'API, `INSERT INTO core.predictions` deve sollevare `InsufficientPrivilege` |
| `_guard_writes()` | **resta**: il processo API non deve scrivere file nemmeno per sbaglio |
| CORS `allow_methods=["GET"]` | metodi di scrittura ammessi, ma solo per le origini dichiarate, e `allow_credentials=True` per il cookie di refresh |

**Bug da correggere prima di introdurre qualsiasi scrittura:** il middleware
che risponde 304 (`backend/api/__init__.py:168-176`) sta **prima** del routing
e non filtra il metodo. Una POST con `If-None-Match` corrispondente
riceverebbe 304 senza mai arrivare alla rotta. Va limitato a GET/HEAD.

---

## 2. Architettura a regime

```
                        ┌───────────────── VPS (docker compose) ──────────────────┐
  browser ──HTTPS──────▶│ Caddy: TLS, rate limit di bordo, /  → frontend statico  │
                        │                                 /api → uvicorn (N worker)│
                        │                                        │                 │
                        │            ┌───────────────────────────┼──────────┐      │
                        │            ▼                           ▼          ▼      │
                        │       Postgres                       Redis     worker    │
                        │    core.*  +  app.*            cache/limiti/code  (arq)  │
                        │            ▲                                             │
                        │            │ solo il caricatore scrive core              │
                        │      backend.loader ◀── git pull dei CSV                 │
                        └─────────────────────────────────────────────────────────┘
                                     ▲
   PC di casa: predict_round / close_round ──▶ CSV in track_record/ ──▶ git push
```

I CSV restano la fonte di verità e viaggiano **nel repository**, che è già
versionato e append-only. Nessun endpoint di ingest: il PC non chiama il
server, spinge un commit.

---

## 3. Schema dati

### `core` — alimentato solo dal caricatore

```sql
core.matches(
  id            bigserial primary key,
  league        text not null,
  season        text not null,
  matchday      int  not null,
  home_team     text not null,
  away_team     text not null,
  match_date    date,
  kickoff_utc   timestamptz,          -- da fbref_schedule, unica fonte
  goals_home    int, goals_away int, ftr char(1),
  unique (league, season, home_team, away_team)   -- la quadrupla del progetto
);

core.predictions(                      -- append-only come il registro
  id               bigserial primary key,
  match_id         bigint not null references core.matches(id),
  model_version    text   not null,
  ts_prediction    timestamptz not null,
  lambda_home      double precision, lambda_away double precision,
  p_home, p_draw, p_away, p_over25, p_btts   double precision,
  odds_home, odds_draw, odds_away            double precision,
  odds_source      text,
  unique (match_id, model_version, ts_prediction)
);
create index on core.predictions (match_id, model_version, ts_prediction);

core.match_metrics(                    -- dai file di giornata chiusa
  match_id bigint references core.matches(id),
  model_version text,
  esito_previsto char(1), azzeccato bool,
  rps, log_loss, brier, rps_mercato  double precision,
  valida bool not null, invalid_reason text,
  primary key (match_id, model_version)
);

core.round_metrics(                    -- da riepilogo.csv
  season text, matchday int,
  n_previsioni int, n_valide int,
  rps, rps_mercato, accuratezza, rps_cumulativo double precision,
  n_cumulate int, closed_at timestamptz,
  primary key (season, matchday)
);

core.data_version(                     -- sostituisce il fingerprint dei file
  id smallint primary key check (id = 1),
  version bigint not null, updated_at timestamptz not null
);

core.load_runs(                        -- com'è andato ogni caricamento
  id bigserial primary key, started_at timestamptz, finished_at timestamptz,
  source_digest text,                  -- sha256 dei CSV letti
  rows_new int, ok bool, error text
);
```

Due assenze volute:

- **Niente tabella delle selezioni.** La regola del progetto è che si
  ricalcolano dai due lambda, perché il criterio si vorrà ritoccare anche sulle
  giornate già chiuse. Restano calcolate; quello che si mette in cache è la
  risposta, non la verità.
- **Niente tabella della classifica.** L'ordinamento della Serie A include gli
  scontri diretti, e quella logica esiste già in `backend/api/standings.py`.
  Riscriverla in SQL significherebbe avere due regole da tenere d'accordo.
  Resta in Python, con i risultati letti dal DB e la risposta in cache.

### `app` — scritto dall'API, per utente autenticato

```sql
app.users(id uuid pk, email citext unique not null, password_hash text not null,
          role text not null default 'user' check (role in ('user','admin')),
          email_verified_at timestamptz, created_at, last_login_at, disabled_at);

app.sessions(id uuid pk, user_id uuid references app.users on delete cascade,
             refresh_hash bytea not null unique,   -- mai il token in chiaro
             issued_at, expires_at, rotated_from uuid, revoked_at,
             user_agent text, ip inet);

app.user_preferences(user_id uuid pk references app.users on delete cascade,
                     odds_threshold numeric(4,2) default 1.50,
                     favourite_teams text[], updated_at);

app.slips(id uuid pk, user_id uuid references app.users on delete cascade,
          season text, matchday int, created_at);
app.slip_items(slip_id uuid references app.slips on delete cascade,
               match_id bigint references core.matches(id),
               market text, market_label text,
               probability double precision, fair_odds double precision,
               taken_at timestamptz,
               primary key (slip_id, match_id, market));

app.notification_settings(user_id uuid pk, on_predictions bool, on_round_closed bool,
                          channel text default 'email', updated_at);
app.outbox(id bigserial pk, user_id uuid, kind text, payload jsonb,
           created_at, sent_at, attempts int default 0, last_error text);
app.email_tokens(id uuid pk, user_id uuid, kind text check (kind in ('verify','reset')),
                 token_hash bytea, expires_at, used_at);
```

**Le schedine non hanno importi.** Nessuna colonna stake, nessun ROI, nessun
valore atteso: è la stessa regola che vale per l'interfaccia, e su 1140 partite
fuori campione l'EV è -5.2% su ogni riga. L'utente annota *quale selezione ha
preso*, e vede quante ne ha prese. L'esito non si salva: si calcola con
`backend/api/selections.py::resolve` dal risultato in `core.matches`, perché
salvarlo congelerebbe un criterio che si vorrà cambiare.

---

## 4. Il caricatore

Nuovo package `backend/loader/` con `python -m backend.loader`:

```bash
python -m backend.loader            # incrementale, idempotente
python -m backend.loader --rebuild  # da zero: TRUNCATE core.* e ricarica
python -m backend.loader --check    # non scrive: dice se DB e file divergono
```

- Riusa le funzioni di lettura che già esistono — `src.backtest_log.load_log`
  (che applica la regola «vince la riga più vecchia») e la lettura dei
  `rounds/*.csv` — invece di riscrivere il parsing: due parser dello stesso
  file divergono sempre.
- `INSERT ... ON CONFLICT DO NOTHING` sulle previsioni (append-only anche nel
  DB), `DO UPDATE` su `core.matches` (i risultati arrivano dopo) e sulle
  metriche di giornata.
- Tutto in **una transazione**, che in coda incrementa `core.data_version` e
  scrive una riga in `core.load_runs` con il digest dei file letti.
- **`--rebuild` è il test di correttezza**: se ricostruire da zero dà un DB
  diverso dal caricamento incrementale, c'è un errore. Va fatto girare in CI.

Integrazione: `scripts/run_task.cmd` lo chiama in coda a `predict_round` e
`close_round` (come già fa con `predict_m5`), e sul VPS un task orario fa
`git pull` + `loader` come rete di sicurezza.

---

## 5. Accesso

- **Access token JWT**, 15 minuti, `sub` = uuid utente, `role`, `jti`. In
  memoria nel frontend, **mai in localStorage**: un XSS lo ruberebbe e non
  sarebbe revocabile.
- **Refresh token opaco** (32 byte casuali), 30 giorni, salvato **solo come
  hash** in `app.sessions`, in cookie `httpOnly; Secure; SameSite=Lax;
  path=/api/auth`. Rotazione a ogni uso; se un refresh già ruotato torna
  indietro, è un furto: si revoca l'intera famiglia.
- **Password**: argon2id (`argon2-cffi`), minimo 10 caratteri, nessuna domanda
  segreta. Verifica email obbligatoria prima di poter salvare schedine.
- **Limiti** su Redis: accesso 5/minuto per IP+email, registrazione 3/ora per
  IP, API pubblica 120/minuto per IP.
- **Ruoli**: `user` e `admin`. Le rotte admin mostrano lo stato del sistema e
  gestiscono gli utenti — **nessuna rotta admin scrive in `core`**, nemmeno per
  «rilanciare le previsioni»: quel comando resta locale.

Rotte nuove, tutte sotto i due prefissi consentiti:

```
POST   /api/auth/register  /login  /refresh  /logout  /verify  /password/reset
GET    /api/me             PATCH /api/me/preferences
GET    /api/me/slips       POST /api/me/slips        DELETE /api/me/slips/{id}
GET    /api/me/notifications   PATCH /api/me/notifications
DELETE /api/me             (cancellazione account: cascata su app, mai su core)
GET    /api/admin/...      (sola lettura)
```

---

## 6. Redis

| uso | chiave | note |
|---|---|---|
| cache risposte pubbliche | `v1:{data_version}:{path}?{query}` | TTL 300s; al cambio di `data_version` le vecchie scadono da sole |
| limiti di frequenza | `rl:{scope}:{id}` | `INCR` + `EXPIRE` |
| revoche immediate | `jti:revoked:{jti}` | TTL = vita residua dell'access token |
| coda notifiche | `arq` | worker separato che legge `app.outbox` |

**L'ETag cambia sorgente ma non forma**: oggi è
`md5(schema_digest | path?query | fingerprint dei file)`; diventa
`md5(schema_digest | path?query | data_version)`. Le risposte che dipendono
dall'utente non entrano nella cache pubblica: `Cache-Control: private,
no-store` e nessun ETag condiviso, altrimenti il proxy servirebbe le schedine
di un utente a un altro.

---

## 7. Prestazioni

Oggi ogni richiesta rilegge Parquet e CSV con pandas; la cache `lru_cache` su
`_fingerprint` lo rende sopportabile per un utente solo, ma non regge migliaia
di sessioni e non si condivide fra worker.

- **In SQL**: `matches`, `rounds`, `round_matches`, `season_summary`,
  `track_record` (il cumulativo con una funzione finestra).
- **Restano in Python**: `standings` (scontri diretti), `picks` e `selections`
  (si ricalcolano per regola) — ma su dati letti dal DB e con la risposta in
  cache Redis. `src.report.selezioni` è la parte più costosa e viene chiamata
  ad ogni richiesta di `/api/picks`: con la cache diventa irrilevante.
- **Async**: FastAPI con `asyncpg` (SQLAlchemy 2.0 async o query scritte a
  mano); pool 10-20 connessioni per worker; worker = 2×core dietro il proxy.
- **Pandas esce dal percorso di richiesta** e resta dove serve: pipeline e
  caricatore.

---

## 8. Fasi di lavoro

Ognuna è rilasciabile da sola e lascia il sistema attuale funzionante.

| # | fase | verifica |
|---|---|---|
| 0 | `docker-compose.yml` locale (Postgres, Redis), Alembic, schemi `core`/`app`, i tre ruoli | `alembic upgrade head`; da psql, l'utente API non riesce a scrivere in `core` |
| 1 | `backend/loader/` + `--rebuild` + `--check` | il DB ricostruito da zero coincide con quello incrementale |
| 2 | `store.Sources` diventa un `Repository` con due implementazioni (file e SQL), scelto da `AI_NAPLES_SOURCE` | **i 25 test di `tests/test_api.py` passano con entrambe le sorgenti, e le risposte JSON sono identiche rotta per rotta** |
| 3 | Cache Redis, `data_version` al posto del fingerprint, correzione del 304 sui metodi non-GET | ETag cambia solo quando cambia il dato; 304 resta a corpo vuoto |
| 4 | Utenti: schema `app`, `/api/auth/*`, JWT, limiti, sostituzione delle guardie; accesso nel frontend | `test_api_role_cannot_write_core`, `test_write_routes_are_scoped`; giro completo registrazione → verifica → accesso → refresh → uscita |
| 5 | Preferenze e schedine | una schedina di un utente non è mai visibile a un altro (test esplicito) |
| 6 | Notifiche: `app.outbox` + worker `arq` + invio email | una giornata chiusa genera le notifiche attese, e un secondo giro non le duplica |
| 7 | VPS: compose completo, Caddy con HTTPS, trasporto dei CSV via git, backup, monitoraggio | dal PC: `predict_round` → push → il sito mostra la giornata entro un'ora |

La **fase 2 è quella che rende tutto reversibile**: finché l'interruttore
esiste, si torna ai file con una variabile d'ambiente.

---

## 9. File toccati

**Nuovi**: `docker-compose.yml`, `Dockerfile`, `Caddyfile`,
`backend/db/` (motore, sessione, modelli, `migrations/` Alembic),
`backend/loader/`, `backend/api/auth/` (rotte, token, password, dipendenze),
`backend/api/repository/` (`files.py`, `sql.py`), `backend/worker/`,
`tests/test_db_roles.py`, `tests/test_loader_rebuild.py`, `tests/test_auth.py`.

**Modificati**: `backend/api/__init__.py` (guardie, CORS, middleware 304),
`backend/api/store.py` (diventa un'implementazione del repository),
`backend/api/routes.py` (dipendenze di sessione), `tests/test_api.py` (i tre
test strutturali), `web/openapi.json` (ri-esportato), `requirements.txt`,
`scripts/run_task.cmd`, `frontend/src/api/client.ts` (access token, refresh,
401 → riprova), `CLAUDE.md` e `COMANDI.md`.

---

## 10. Verifica finale, end-to-end

```bash
docker compose up -d                       # Postgres, Redis, API, worker, Caddy
alembic upgrade head
python -m backend.loader --rebuild         # DB dai CSV
python -m tests.test_api                   # 25 test, con AI_NAPLES_SOURCE=db
python -m tests.test_db_roles              # l'API non scrive in core
python -m tests.test_auth                  # registrazione → accesso → refresh → uscita
python -m tests.test_production_unchanged  # M1 identico bit a bit: la pipeline non è cambiata
```

Più una prova a mano: `predict_round` sul PC, `git push`, `loader` sul VPS, e
la giornata compare sul sito con lo stesso contenuto del file.

---

## 11. Rischi e cosa non fare

**Il rischio maggiore è lo scivolamento della fonte di verità**: un giorno
qualcuno scrive una correzione direttamente nel DB, i CSV non la contengono, e
il registro smette di essere il registro. Difese: il caricatore registra il
digest dei file in `core.load_runs`, `/api/status` mostra «allineato ai file
del …», e un controllo giornaliero confronta i conteggi.

**Il PC spento è un caso normale, non un guasto**: se la pipeline non gira, il
sito mostra dati vecchi. Lo stato lo dice già oggi e deve continuare a dirlo.

**Da non fare, mai:**
- una rotta che lancia `predict_round` (anche solo per admin): la previsione
  vale perché la scrive un processo che non conosce il risultato;
- scritture dell'API in `core`, nemmeno «temporanee»;
- materializzare le selezioni o l'esito delle schedine;
- importi, ROI o valore atteso nelle schedine;
- un token di accesso in `localStorage`.

**Da estendere**: `test_no_betting_advice_exposed` deve coprire anche i payload
nuovi delle schedine, altrimenti la regola vale solo per le rotte vecchie.
