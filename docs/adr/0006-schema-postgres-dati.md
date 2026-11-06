# ADR 0006 — Lo schema dei dati in Postgres, e come ci si arriva

- **Stato**: accettata, implementata. Produzione ancora su `parquet`, vedi "Il passaggio"
- **Data**: 6 novembre 2026
- **Contesto**: fase 2. Attua l'ADR 0002, che lasciava aperto lo schema
- **Decide**: com'e' fatta ogni tabella, chi la scrive, e in che ordine si
  spegne il parquet

---

## Il vincolo che decide tutto

`test_production_unchanged` confronta M1 **bit a bit**, e l'ADR 0002 chiedeva
di misurare il passaggio dal driver invece di darlo per buono. Misurato il
6 novembre 2026 su **tutti i 35 file** di `data/` (non su un campione): ogni
float torna con gli stessi 64 bit, ogni dtype pandas torna uguale, NULL resta
distinto da NaN e la stringa vuota resta distinta da NULL. Lo verifica
`goalmodel db --verifica`, che si rilancia quando si vuole.

Uno schema relazionale "progettato", che spezzasse le 236 colonne di
`matches_master` in tabelle normalizzate, avrebbe dovuto ricostruire quella
garanzia da zero, colonna per colonna. Ed era gia' escluso dall'ADR 0002:
le colonne seguono le fonti, e le fonti cambiano da sole.

## Lo schema

### Tre schemi per i dati, uno per il catalogo

| schema Postgres | cartella di prima | contenuto |
|---|---|---|
| `raw` | `data/raw/` | 19 dati grezzi dalle fonti, uno per stage (WhoScored: un calendario per stagione) |
| `interim` | `data/interim/` | `matches_master`, il dataset del modello |
| `processed` | `data/processed/` | i quattro blocchi di feature, i walk-forward, la taratura |
| `goalmodel` | — | `catalogo` e la sequenza `versioni` |
| `public` | — | account, sessioni, ruoli: Alembic, `backend/auth`, invariato |

Gli schemi ricalcano le cartelle e non hanno nomi nuovi (`core`,
`features`...): la cartella da cui un dato viene **e'** gia' la sua
classificazione, e un secondo nome vorrebbe dire una tabella di traduzione in
piu' da tenere allineata.

Una tabella per dato, con lo stesso nome; la sottocartella diventa `__`
(`whoscored/schedule_1516` -> `raw.whoscored__schedule_1516`).

### Le colonne seguono il DataFrame

Ogni tabella ha le colonne del DataFrame che la scrive, con il nome esatto
(`"B365>2.5"`, `"FTHG"`, fra virgolette) e un tipo che **riporta indietro
esattamente** il tipo arrow di partenza:

| arrow / pandas | Postgres | perche' cosi' |
|---|---|---|
| `double` (`float64`, `Float64`) | `double precision` | IEEE 754 a 64 bit, NaN e infinito compresi |
| `int64` (`int64`, `Int64`) | `bigint` | |
| `bool` (`bool`, `boolean`) | `boolean` | |
| `string`, `large_string` (`str`, `string`) | `text` | |
| `timestamp[us]` | `timestamp` | |
| `timestamp[us, tz=UTC]` | `timestamptz` | letto in uscita `AT TIME ZONE 'UTC'` |
| `null` (colonna tutta vuota) | `text` | |
| liste e strutture (`incidents`, `bets` di WhoScored) | `jsonb` | |

Un tipo fuori da questa tabella **solleva** (`pg.TipoNonSupportato`) invece
di finire in `text`. Un ripiego silenzioso e' il primo dei tre modi di
rompersi scritti in `schema.py`: un intero che diventa stringa e un merge che
smette di agganciare.

**Le colonne non si migrano: la tabella si sostituisce.** Come faceva il
parquet. football-data aggiunge un book, soccerdata una statistica, e un
`ALTER TABLE` scritto a mano per ciascuno sarebbe una migrazione dimenticata
alla prima stagione. Per questo le tabelle dei dati non sono in Alembic, e
`backend/auth/migrations/env.py` le ignora gia' (filtra sulle tabelle sue).

### Il catalogo

```sql
goalmodel.catalogo (
    livello       text,          -- raw | interim | processed
    nome          text,          -- il nome del dato, come il file
    tabella       text,
    schema_arrow  bytea,         -- lo schema arrow con i metadati pandas
    righe         bigint,
    colonne       integer,
    versione      bigint,        -- da goalmodel.versioni, cresce a ogni scrittura
    scritto_il    timestamptz,
    PRIMARY KEY (livello, nome)
)
```

`schema_arrow` e' cio' che rende esatto il ritorno. Contiene i dtype pandas
(`Int64`, `Float64`, `str`, `string`), che Postgres da solo non saprebbe
distinguere: alla lettura si ricostruisce lo stesso schema e arrow fa la
stessa conversione verso pandas che fa `read_parquet`. Stessi ingressi,
stessa uscita.

`versione` sostituisce mtime e dimensione del file nelle cache dell'API: cambia
se e solo se il dato e' stato riscritto.

### I vincoli: il contratto di `schema.py`, imposto dal database

L'ADR 0002 prometteva che i tre modi di perdere dati in silenzio sarebbero
diventati impossibili a livello di storage. Lo sono, per i dati che hanno un
contratto in `schema.PER_NOME`, e il contratto e' quello, non uno nuovo:

| dato | vincolo |
|---|---|
| `interim.matches_master` | chiave `NOT NULL`, `UNIQUE (league, season, home_team, away_team)` |
| `raw.matches` | idem |
| `raw.fbref_schedule` | chiave `NOT NULL`, `UNIQUE (...) WHERE week IS NOT NULL` |

L'ultima e' la regola n.5 con la sua eccezione: lo spareggio Spezia-Verona
2022/23 ha `week` nullo ed entra, un doppione vero no. Ogni tabella con
`league` e `season` ha anche un indice su quelle due, che e' il filtro di ogni
lettura dell'API.

`schema.verifica` continua a girare alla lettura, in `data.py`: i tipi
testuali e le date li controlla ancora lui, e costa millisecondi.

### Le scritture sono atomiche

Una scrittura crea `tabella__nuova`, la riempie con `COPY`, butta la vecchia,
rinomina, impone i vincoli e aggiorna il catalogo: **una transazione**. Chi
legge nel frattempo vede la versione precedente intera. Se un vincolo salta,
non cambia niente. Il parquet invece veniva sovrascritto comunque, e la
violazione si scopriva alla lettura successiva. Due scritture dello stesso
dato si mettono in fila (`pg_advisory_xact_lock`).

Il trasporto e' CSV via `COPY`, in entrambi i sensi: 0.9 s per scrivere
`matches_master` (22041 x 236), 0.5 s per leggerlo.

## Chi scrive, chi legge

```
pipeline ── storage.scrivi ──┬── data/*.parquet        (parquet, doppio)
                             └── pg.scrivi ── Postgres (doppio, postgres)

goalmodel ── data.py ── storage.leggi ── file oppure Postgres
backend/api ── store._read_data ── idem, solo per i percorsi dentro data/
```

- `storage.py` e' l'unico modulo che sa dove sta un dato. Il confine di
  lettura c'era gia' (`data.py`); quello di **scrittura** no: quindici
  `to_parquet` sparsi fra ingestion, normalizzazione, feature e valutazione.
  `tests/test_confine_lettura.py` ora sorveglia tutti e due.
- `pg.py` sa solo portare un DataFrame in una tabella e indietro. Nessun altro
  modulo lo importa, tranne `archivio.py` (`goalmodel db`).
- **Le guardie si estendono**: `experiments.proteggi_produzione()` e
  `_guard_writes()` dell'API chiudono `storage.scrivi`, perche' con i dati in
  Postgres una scrittura di produzione non passa piu' per `to_parquet`.
- **I test non toccano i dati veri, in nessuna delle due forme**: `conftest.py`
  forza `GOALMODEL_STORAGE=parquet`, fa fallire ogni `storage.scrivi` diretto
  dentro il `data/` vero, e manda Postgres sugli schemi `test_*`
  (`GOALMODEL_PG_PREFISSO`). Non e' prudenza teorica: il passaggio a
  `storage` ha reso inefficaci quattro test che dirottavano `ingest.RAW`, e
  avrebbero scritto nel `data/raw` vero. Trovati prima di lanciarli.

## Cosa NON si sposta

Invariato dall'ADR 0002: **il registro delle previsioni e gli archivi di
giornata restano file versionati** (`track_record/`), scritti solo da
`predict_round` e `close_round` sulla macchina. Il report HTML resta un file.

Restano file anche, per ora: le previsioni di M5 (`experiments/output/`), lo
stato dello scheduler (`logs/last_run.json`), `manual/`. Non sono dati del
modello e il deploy li porta gia' con se'; spostarli e' una decisione a parte.

**Il calcolo resta locale.** Postgres toglie la dipendenza da `data/` a chi
*legge* — l'API in un container o su Vercel. Ingestion, addestramento e
previsione girano sulla macchina, ora scrivendo nel database.

## Il passaggio, in tre modalita'

`GOALMODEL_STORAGE`, letta a ogni operazione:

| modalita' | scrive | legge | a cosa serve |
|---|---|---|---|
| `parquet` | file | file | il predefinito, finche' il passaggio non e' chiuso |
| `doppio` | file **e** Postgres | file | far girare la pipeline vera contro il database senza dipenderne: se Postgres e' giu', il file e' gia' scritto e si prosegue con un errore nel log |
| `postgres` | Postgres | Postgres | lo stato finale: `data/` non serve piu' |

Verificato il 6 novembre 2026, sul Postgres locale:

1. `goalmodel db --carica` e `--verifica`: 35 dati su 35 identici bit a bit;
2. `predict-round --dry-run` in modalita' `postgres`: `matches_master` e i
   quattro blocchi di feature ricostruiti leggendo e scrivendo **solo**
   Postgres, nessun file di `data/` modificato, e il risultato identico bit a
   bit ai file costruiti in modalita' `parquet` la sera stessa;
3. l'API (`round_states`, `track_record`, `rounds`, risultati, marcatori,
   quote, freschezza) da' risposte identiche byte per byte nelle due
   modalita';
4. l'impronta di `test_production_unchanged` e' la stessa di prima del lavoro.

**Cosa resta da fare, in ordine:**

1. una o due giornate in `doppio` (`predict-round`, `close-round`), con
   `goalmodel db --verifica` dopo ciascuna;
2. `postgres` sulla macchina della pipeline;
3. caricare Neon (`AI_NAPLES_DATABASE_URL` di produzione, `goalmodel db
   --carica` dalla macchina) e impostare `GOALMODEL_STORAGE=postgres` nei
   servizi dell'API: da li' Vercel non ha piu' bisogno di `data/`, e
   `.vercelignore` lo puo' togliere;
4. solo allora `data/` smette di servire, e si toglie.

Fino al punto 3 Vercel e compose continuano a leggere i file, come oggi.
