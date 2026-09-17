# COMANDI

Riferimento operativo. **Questo file e' l'unica fonte di verita' sui comandi**:
`CLAUDE.md` spiega il *perche'* delle scelte e rimanda qui per il *come*. Se un
comando cambia, si aggiorna qui e basta — elenchi duplicati divergono.

Ambiente: **Windows con PowerShell**, virtualenv in `.venv`.
I comandi sotto presuppongono `.\.venv\Scripts\python.exe` come interprete;
dove si legge `python`, usa quello.

Tempi misurati sulla macchina di sviluppo (i7-13620H, 10 core) con Serie A,
13 stagioni, 4580 partite.

---

## 1. I due comandi della giornata

L'unita' di lavoro e' la **giornata di campionato**, non la settimana. Ogni
giornata ha uno stato, e i due comandi la fanno avanzare:

```
futura   -> aperta   -> predetta      -> giocata -> chiusa
           (quote)     predict_round             close_round
```

| stato | vuol dire |
|---|---|
| `futura` | nessuna quota disponibile |
| `aperta` | quote presenti, partite non ancora giocate |
| `predetta` | previsioni registrate (`predetta in parte` se non tutte) |
| `giocata` | tutti i risultati disponibili |
| `chiusa` | risultati agganciati, errore calcolato, giornata archiviata |

```bash
python -m src.predict_round     # da aperta a predetta
python -m src.close_round       # da giocata a chiusa
python -m src.rounds --status   # dove sta ogni giornata della stagione
```

Nessuno dei due chiede quale giornata: la deducono da quote, registro e
risultati. **Si lanciano quando si vuole e quante volte si vuole.** Se non c'e'
niente da fare escono con codice **0** e un messaggio che nomina la giornata e
il suo stato. Entrambi finiscono con due righe, `FATTO` e `IN SOSPESO`.

**Opzioni comuni a tutti e due**

```bash
--round N        forza una giornata invece di dedurla
--dry-run        tutto tranne la scrittura (registro o archivio)
--skip-ingest    riusa i dati gia' scaricati, non tocca la rete
--verbose        log completi dei moduli chiamati
```

`python -m src.predict_round` accetta anche `--no-open`, per non aprire il
report nel browser.

### `predict_round` — da aperta a predetta

```bash
python -m src.predict_round
```

1. `ingest`: `matches`, `understat`, `schedule`, `fixtures` (non fatali)
2. `normalize --build`, `features.form`, `features.market` (fatali)
3. trova la prima giornata con partite **predicibili adesso**: con le quote,
   non gia' in registro, e con il calcio d'inizio ancora davanti
4. riaddestra il modello su tutto lo storico e predice tutte le sue partite
5. scrive nel registro saltando le gia' presenti, genera il report e lo apre

**Giornata coperta a meta'.** Lo snapshot quote e' una finestra sul turno
imminente: puo' contenerne sei su dieci. Si registrano le sei e si dice quali
restano scoperte; al lancio dopo si completano le mancanti senza duplicare
niente. La giornata resta `predetta in parte` finche' non sono coperte tutte.

**Nota informativa, non un vincolo del codice** (nessun giorno della settimana
compare da nessuna parte): football-data pubblica le quote il **venerdi' entro
le 17:00 UK** per il weekend e il **martedi' entro le 13:00** per gli
infrasettimanali. Prima di quei momenti la giornata risultera' ancora `futura`,
e il comando lo dira'.

### `close_round` — da giocata a chiusa

```bash
python -m src.close_round
```

1. `ingest`: `matches`, `understat` (non fatali)
2. `normalize --build` e feature (fatali — senza, i risultati appena scaricati
   non arrivano a `matches_master` e l'aggancio non trova niente)
3. trova la prima giornata predetta e **interamente** giocata
4. aggancia i risultati e archivia in
   `track_record/rounds/round_<stagione>_<NN>.csv`, con previsione, risultato
   ed errore per singola partita
5. rifa' `track_record/rounds/riepilogo.csv` e rigenera il report

**Una giornata incompleta non si chiude**, nemmeno con `--round` esplicito: se
manca un solo risultato resta aperta e si riprova al lancio dopo. Archiviare un
RPS calcolato su nove partite su dieci lo renderebbe non piu' correggibile,
perche' la giornata risulterebbe gia' chiusa.

**Una giornata gia' chiusa non si riscrive.** Lo stato "chiusa" *e'*
l'esistenza del file: per rifarla, cancella il CSV e rilancia.

### `rounds --status` — dove sta ogni giornata

```bash
python -m src.rounds --status
python -m src.rounds --status --season 2526
```

Una riga per giornata: date, partite, quote, previsioni, risultati, stato e RPS
se chiusa. `>` marca la giornata che `predict_round` predirebbe adesso, `*`
quella che `close_round` chiuderebbe.

### `weekly` non esiste piu'

`python -m src.weekly` stampa i due comandi nuovi ed esce con codice 2. Faceva
due cose con precondizioni opposte — predire vuole le quote e nessun risultato,
chiudere vuole tutti i risultati — quindi una delle due era sempre fuori tempo.

### Il report su file

Entrambi i comandi scrivono un report HTML, e lo si puo' rigenerare da solo:

```bash
python -m src.report               # rigenera track_record/report.html
python -m src.report --open        # lo rigenera e lo apre nel browser
python -m src.report --no-diverge  # salta la sezione 3, che addestra M4
```

```
track_record/report.html                        percorso fisso, da tenere aperto
data/processed/reports/giornata_2627_03.html    archivio, una per giornata
```

Aprilo con un doppio clic. Cinque sezioni, in quest'ordine:

1. **la giornata in arrivo** — 1/X/2, gol attesi, over 2.5, gol-gol, le
   selezioni piu' probabili con la **quota equa** accanto, e la squadra seguita
   in evidenza con i primi cinque punteggi esatti;
2. **cosa e' cambiato** — i gol attesi di ogni squadra contro quelli
   dell'ultima previsione che la riguardava. Un lambda isolato non e'
   leggibile, un lambda che si e' mosso si';
3. **dove il modello diverge dal mercato** — M4 senza quote contro M1. E'
   **diagnostica, non un segnale di scommessa**: il test set ha stabilito che
   nessun modello statistico batte le quote, quindi uno scarto grande e'
   un'anomalia da capire, non un'occasione;
4. **track record** — RPS cumulativo delle previsioni vere nel tempo con la
   linea del backtest a 0.1881, curva di calibrazione, e quante previsioni
   servono ancora perche' il confronto significhi qualcosa;
5. **stato del sistema** — data dello snapshot quote, partite scoperte,
   avvisi. Se qualcosa non ha funzionato si vede qui, non solo nel log.

Si adatta al tema chiaro/scuro del browser e non dipende da niente di esterno:
CSS dentro il file, grafici in SVG generato, nessun CDN.

`python -m src.report` **non tocca mai il registro**: le previsioni si scrivono
una volta sola, da `python -m src.predict_round`, prima del calcio d'inizio. Il
report lo dichiara in fondo, cosi' un file rigenerato non si confonde con quello
del comando vero.

Il report e' una **vista**: il dato e' `predictions_log.csv`. Rigenerarlo non
cambia nulla, cancellarlo nemmeno. Per questo `report.html` sta in
`track_record/` ma **non** e' versionato.

### Se serve fare i passi a mano

```bash
python ingest.py --stage matches      # risultati appena giocati
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage fixtures     # quote del turno imminente
python ingest.py --stage cups         # calendario Champions/Europa/Conference
python ingest.py --stage missing      # infortunati WhoScored: LENTO, ~11s/partita, browser
python ingest.py --stage player_stats # minuti e gol per giocatore: LENTO, ~13s/partita
python -m src.normalize --build
python -m src.features.form
python -m src.features.market
python -m src.predict --next          # SCRIVE nel registro
python -m src.backtest_log
```

**`predict` e' l'unico passaggio non recuperabile.** Verifica con un assert che
il timestamp UTC preceda il calcio d'inizio: se il registro non viene scritto
in tempo, quella giornata e' persa per sempre ai fini del track record.
Ricostruirla dopo con `--as-of` finisce in un file separato che **non** e' un
track record.

---

## 2. Primo avvio, da zero

```bash
python -m pip install -r requirements.txt

python ingest.py --stage matches
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage fixtures

python -m src.normalize --report --apply   # costruisce team_name_map.json
python -m src.normalize --build

python -m src.features.form
python -m src.features.market

python -m src.evaluate                     # ~10 minuti, riproduce la tabella
```

Gli stage lenti (`lineups`, `player_stats`, `missing`) non servono a niente di
quanto e' stato costruito finora: saltali.

---

## 3. Ingestion

| comando | tempo | scrive |
|---|---|---|
| `python ingest.py --stage matches` | ~10s | `data/raw/matches.parquet` |
| `python ingest.py --stage understat` | ~2 min | `understat_schedule`, `understat_team_match` |
| `python ingest.py --stage fixtures` | ~5s | `data/raw/fixtures_odds.parquet` |
| `python ingest.py --stage schedule` | ~5 min | `data/raw/fbref_schedule.parquet` |
| `python ingest.py --stage elo` | ~1 min | `clubelo_history` — opzionale, servizio spesso giu' |

Quando football-data e' irraggiungibile (risponde 503 su tutto il dominio piu'
spesso di quanto dovrebbe), scarica `fixtures.csv` dal browser e poi:

```bash
python ingest.py --stage fixtures --fixtures-file C:\percorso\fixtures.csv
```

**Stage lenti**, ore, rate-limited, interrompibili grazie alla cache:

```bash
python ingest.py --stage lineups
python ingest.py --stage player_stats
python ingest.py --stage missing        # richiede Chrome/Chromium
```

---

## 4. Normalizzazione

```bash
python -m src.normalize --report           # diagnosi dei nomi squadra
python -m src.normalize --report --apply   # scrive la mappa automatica
python -m src.normalize --build            # costruisce matches_master.parquet
```

`--build` fallisce se il join scende sotto la soglia: e' voluto, significa che
mancano voci in `manual/team_name_map.json`.

---

## 5. Feature

```bash
python -m src.features.form                # medie mobili leakage-safe
python -m src.features.market              # de-vigging Shin + proporzionale
python -m src.features.market --coverage   # copertura quote, book per stagione
python -m src.features.context             # blocco A: riposo, congestione, derby
python -m src.features.players             # blocco B: peso delle assenze
```

`--coverage` va rilanciato dopo ogni ingestion: i bookmaker spariscono senza
preavviso (Pinnacle si e' spento nel 2025/26 a meta' stagione).

`context` legge `manual/derbies.csv` (colonne `home_team, away_team,
intensity` con intensity in city/regional/rivalry, coppia NON ordinata). Se il
file manca, le due colonne del derby non vengono prodotte e lo dice: non finge
che nessuna partita sia un derby, che sarebbe un dato falso invece che assente.

**Le coppe europee infrasettimanali NON ci sono**, e non e' una dimenticanza:
`fbref_schedule` contiene la sola Serie A, quindi una partita di Champions del
martedi' non compare da nessuna parte. Servirebbe una ingestion nuova col
calendario UEFA. Dedurre chi gioca in Europa dalla classifica dell'anno prima
sarebbe una funzione dei risultati passati, cioe' proprio cio' che il piano
esclude.

---

## 6. Valutazione

```bash
python -m src.evaluate                # ~10 min — tabella + confronto appaiato
python -m src.evaluate --calibration  # curve di calibrazione ed ECE
python -m src.evaluate --bias         # favourite-longshot, stagione per stagione
python -m src.evaluate --no-save      # non riscrive walk_forward_predictions
```

Il default riaddestra 10 modelli su 114 giornate: e' il grosso dei 10 minuti.

---

## 7. Modelli: controlli e taratura

**La taratura gira SEMPRE sulla validazione (2122, 2223), mai sul test.**
Se ritari qualcosa, aggiorna il valore in `src/config.py` e rifai il punto 6.

```bash
# Controlli di correttezza (secondi)
python -m src.models.baseline --demo        # matrice risultati, segno di rho, gol-gol
python -m src.models.dixon_coles --check    # gradiente analitico vs differenza centrata

# Taratura
python -m src.models.dixon_coles --tune               # 28s  -> config.DC_HALFLIFE
python -m src.models.gbm --tune --workers 8           # ~11 min, tutte le varianti
python -m src.models.gbm --tune --variants ancorato   # ~2 min, solo M5
python -m src.models.gbm --blend                      # 61s  -> config.BLEND_WEIGHT

# Diagnostica
python -m src.models.gbm --importance       # 4s — cosa usa M4 senza mercato

# Misura di un blocco di feature, col protocollo fissato (~40 min)
python -m src.evaluate --blocco contesto
python -m src.evaluate --blocco giocatori

# Il blocco spiega il contenuto o solo la disponibilita' del dato? (~10 min)
python -m src.evaluate --blocco-nan giocatori

# Potenza: quale effetto questo test set puo' vedere? (~40s)
python -m src.power_analysis
python -m src.power_analysis --quota-partite 0.15 --shift-lambda 0.20
```

**Da lanciare PRIMA di costruire un blocco di feature**, non dopo. Misura la
correlazione dentro la giornata (`rho`, oggi 0.004) e ne ricava l'effetto
minimo rilevabile nei tre scenari — Serie A, Big 5 per giornata, Big 5 per
settimana.

`--quota-partite` e' la **frazione di partite** che il sottoinsieme seleziona,
**non** la soglia che lo definisce: "oltre il 15% dei minuti indisponibili" e'
la soglia, e quante partite la superino si sa solo dopo aver costruito il
blocco. Confonderle non da' errore, da' numeri sbagliati e plausibili.

La tabella da leggere e' **la soglia di rottura**: per ogni dimensione del
sottoinsieme, quale shift di lambda servirebbe. Restringere il sottoinsieme
alza il minimo rilevabile, non lo abbassa.

Opzioni utili di `--tune`: `--configs N` (quante configurazioni provare, default
24), `--stride N` (giornate per blocco durante la ricerca, default 3 — solo per
abbassare il costo, il risultato riportato usa sempre stride 1).

---

## 7bis. Esperimenti — fuori dal percorso di produzione

Tutto quello che sta in `src/experiments/` legge i dati veri e scrive **solo**
in `experiments/output/` (gitignorato tranne i `riassunto_*`). Una guardia a
runtime rifiuta qualsiasi scrittura in `data/processed/` o `track_record/`,
quindi un esperimento non puo' sporcare la produzione nemmeno per errore.

**Prima di ogni commit**, e dopo ogni tocco a `market.py`, `baseline.py` o
`gbm.py`:

```bash
python -m tests.test_production_unchanged                # M1 identico bit a bit
python -m tests.test_production_unchanged --sensibilita  # prova che il test scatta
python -m tests.test_production_unchanged --rigenera     # SOLO dopo un cambio voluto
```

Se fallisce, la prima riga del suo output dice se sono cambiate le **quote di
ingresso** (dati) o i numeri a valle (codice): sono due diagnosi diverse.

```bash
python -m src.features.sets                 # stato dei set di feature sul dataset
python -m tests.test_sets                   # BASE congelato, set disgiunti
python -m tests.test_experiments_modelli    # M5Set, M5Colonne, media dei semi
python -m tests.test_forma_venue            # forma per sede: niente leakage
```

**Blocco B, verifiche di robustezza** (~9 min con 8 processi; i risultati non
promuovono il blocco, possono solo declassarlo):

```bash
python -m src.experiments.blocco_b_robustezza --lancia --paralleli 8
python -m src.experiments.blocco_b_robustezza --analizza
python -m src.experiments.blocco_b_robustezza --importanza   # asimmetria su 5 semi
```

**Sulla sola validazione** (2122, 2223) — non consumano confronti sul test:

```bash
python -m src.experiments.collinearita --descrivi
python -m src.experiments.collinearita --misura         # selezione e PCA contro BASE
python -m src.experiments.baseline_mercato              # de-vigging e consenso di book
python -m src.experiments.forma_venue --descrivi
python -m src.experiments.forma_venue --lancia          # 5 semi in parallelo
python -m src.experiments.forma_venue --analizza
```

Un walk-forward gia' fatto non si rifa': `--lancia` salta i semi il cui file
c'e' gia'. Per rifarlo, cancella il parquet in `experiments/output/`.

### Prevedere una giornata con LightGBM (M5), senza toccare il registro

```bash
python -m src.experiments.predici_gbm                      # prima giornata utile
python -m src.experiments.predici_gbm --matchday 5
python -m src.experiments.predici_gbm --semi 0             # un seme solo, piu' veloce
python -m src.experiments.predici_gbm --team Napoli
python -m src.experiments.predici_gbm --as-of 2026-09-11 --matchday 4
```

Addestra M5 (GBM Poisson ancorato al mercato, media di 5 semi, le 52 colonne di
BASE) su tutto lo storico giocato, poi riusa `predict.py` invariato con
`dry_run=True`. Stampa M5 accanto a M1 ordinato per scarto sull'1X2 e scrive
`experiments/output/previsioni_m5_<stagione>_<NN>.csv`. **Non scrive nel
registro**: il track record resta di M1, che e' il modello di produzione.

`--as-of` rifa' una giornata gia' giocata con le quote di allora **e taglia il
training a quella data**, altrimenti il modello si addestrerebbe anche sulla
giornata che dice di prevedere.

Serve che le quote del turno ci siano: `python ingest.py --stage fixtures`.
Se football-data non ha ancora pubblicato la Serie A, il file ha zero righe, il
comando lo dice e non predice niente — non e' un errore, e' il turno non ancora
uscito.

---

## 7ter. API in sola lettura sul track record

**Dove sta.** Monorepo: `backend/api/` contiene l'API, `src/` resta la
pipeline. La dipendenza e' a senso unico — `backend` importa `src.config`,
`src.rounds`, `src.predict`, `src.backtest_log`, `src.evaluate` e
`src.models.baseline`, in sola lettura; `src` non sa che il backend esiste.
Il frontend andra' in una cartella sua accanto a queste due. **Tutti i comandi
si lanciano dalla radice del repository**, dove `src` e `backend` sono
entrambi importabili.

```bash
python -m backend.api --port 8000                          # http://127.0.0.1:8000/docs
python -m backend.api --export-openapi web/openapi.json    # il file per il frontend
python -m tests.test_api                                   # tutti gli endpoint
```

Funziona anche con uvicorn chiamato direttamente — `backend.api:app` e' un'app
costruita all'import, senza fabbrica da invocare:

```bash
uvicorn backend.api:app --port 8000
uvicorn backend.api:app --port 8000 --reload          # ricarica a ogni salvataggio
uvicorn backend.api:app --host 0.0.0.0 --port 8000    # SOLO se sai perche'
```

Le due strade sono la stessa cosa: `python -m backend.api` passa proprio
`"backend.api:app"` a uvicorn. La differenza e' che il modulo stampa anche le
origini CORS attive e l'indirizzo di `/docs`, e accetta `--export-openapi`.
Il default resta `127.0.0.1`: in sviluppo l'unica via d'ingresso e' il tunnel.

**Sola lettura per costruzione**: solo GET, e l'app rifiuta di avviarsi se una
rotta dichiara POST/PUT/DELETE. Le scritture sotto `track_record/` e `data/`
sono bloccate a runtime. Il track record lo scrivono `predict_round` e
`close_round`, mai una richiesta HTTP.

| endpoint | cosa restituisce |
|---|---|
| `GET /api/season/{season}` | RPS di produzione, riferimento 0.1881, hits e denominatore, `sample_significant` |
| `GET /api/rounds/{season}` | una voce per giornata: stato, conteggi, RPS archiviato |
| `GET /api/rounds/{season}/{n}` | le partite di una giornata |
| `GET /api/matches/{season}` | tutte le partite. Filtri: `team`, `status`, `from`, `to` |
| `GET /api/track-record/{season}` | serie per giornata e per partita, calibrazione |
| `GET /api/standings/{season}` | la classifica calcolata dai risultati. Spareggio: punti, **scontri diretti**, differenza reti |
| `GET /api/picks/{season}` | **le selezioni principali**: una per partita, con la soglia dichiarata in `config.QUOTA_MINIMA_SELEZIONE`. Nessun parametro di quota |
| `GET /api/selections/{season}` | esploratore: i mercati dentro una banda. Filtri: `min_odds`/`max_odds` (default 1.30-1.40), `matchday` |
| `GET /api/status` | ultime esecuzioni, giornata corrente, eta' dello snapshot, prossima azione |
| `GET /api/health` | vivo/degradato e data dell'ultimo aggiornamento |

La stagione si scrive `2026-27` o `2627`, la risposta usa sempre la prima.
`status` di una partita vale `predicted`, `resolved` o `invalid` — quest'ultimo
sono le righe scritte dopo il fischio: **hanno il risultato ma non le
metriche**, e `invalid_reason` dice perche'.

**Niente valore atteso, stake o consigli di scommessa.** Quota equa e overround
si', sono descrittivi: l'EV calcolato sulle probabilita' di M1 contro le quote
da cui derivano e' circolare, e misurato vale -5.2% su ogni riga.

### Le selezioni sotto soglia

`GET /api/selections/2627?max_odds=1.20` ricostruisce **tutti i mercati** che
il modello prezza — 1X2, doppia chance, over/under su cinque linee, gol-gol,
no gol, casa segna, fuori segna — e tiene quelli la cui **quota equa** sta
sotto la soglia. Non serve nessun dato nuovo: ogni mercato e' una somma diversa
sulla stessa matrice dei risultati, e i due lambda sono nel registro.

Riusa `report.selezioni()`, la stessa funzione della tabella di
`predict_round` e del report HTML: due implementazioni divergerebbero, e il
primo sintomo sarebbe una dashboard che contraddice il terminale del venerdi'.

Due cose da sapere leggendo la risposta:

- **`book_odds` e' null su tutto tranne l'1X2.** Il registro conserva solo
  quella. Per doppia chance e mercati gol il prezzo vero ce l'ha il tuo book:
  stimarlo con un margine medio sarebbe un numero inventato con l'aria di
  essere misurato.
- **I mercati triviali non escono mai** (`over 0.5`, `under 4.5`, ...): nessun
  book li paga abbastanza da essere una giocata, e sotto 1.20 sarebbero tre
  quarti dell'elenco. Sono elencati in `excluded_markets`, cosi' l'esclusione
  si vede invece di essere silenziosa.

**La banda, non il tetto.** Sotto 1.20 restano quasi solo quasi-certezze che
pagano troppo poco; il default e' **1.30-1.40**, dove probabilita' e prezzo
stanno insieme. `min_odds` e `max_odds` cambiano la banda.

**`best_per_match`**: una selezione per partita, la piu' probabile dentro la
banda (a parita', la quota piu' alta). Non e' salvata nel registro, si
**ricalcola** dai due lambda: congelarla significherebbe non poter piu'
cambiare il criterio sulle giornate gia' chiuse.

**`matches_covered` contro `matches_total`**: una banda stretta lascia partite
senza nessun mercato dentro, e il buco va letto — 1.40-1.50 copre 12 partite su
18, 1.30-1.40 ne copre 17.

Misurato sulle giornate 3 e 4:

| banda | selezioni | vinte | una per partita |
|---|---|---|---|
| 1.20 - 1.30 | 22 su 16 partite | 15/18 | — |
| **1.30 - 1.40** | **36 su 17 partite** | **26/32** | **13/15** |
| 1.40 - 1.50 | 20 su 12 partite | 15/19 | — |

Sotto quota equa 1.20 ci sarebbero 12 selezioni (10 vinte su 11), ma con i
mercati triviali dentro sarebbero 41, di cui 30 fra `over 0.5` e `under 4.5`.

CORS: `AI_NAPLES_CORS_ORIGINS="http://localhost:5173,https://tuo-frontend"`.
Senza variabile valgono i soli localhost di sviluppo, mai `*`.

Le risposte portano `Cache-Control: public, max-age=300` e un ETag derivato
dagli mtime dei file: fra un venerdi' e il martedi' successivo il frontend
riceve 304 e il server non lavora.

### Esporre l'API al frontend

Sviluppo, con il progetto che gira in locale su Windows:

```bash
python -m backend.api --port 8000      # in un terminale
ngrok http 8000                        # in un altro
```

L'URL pubblico di ngrok cambia a ogni riavvio nel piano gratuito: il frontend
deve leggerlo da una variabile d'ambiente, non averlo scritto nel codice.
Tieni `--host 127.0.0.1` (il default) e lascia che il tunnel sia l'unica via
d'ingresso.

Per un deploy stabile, in ordine di parti mobili:

1. **Export statico** — se il frontend non ha bisogno dei filtri, i JSON su
   GitHub Pages: zero server, zero uptime da garantire.
2. **Container** con `track_record/` montato in sola lettura.
3. **VPS** con uvicorn dietro nginx e HTTPS.

---

## 7quater. Scheduler di Windows

```bash
scripts\predict_round.cmd     # venerdi'
scripts\close_round.cmd       # martedi'
```

Registrano tutto in `logs\scheduler_YYYYMMDD.log` e scrivono `logs\last_run.json`,
che e' quello che legge `GET /api/status` — il log resta per gli umani, l'API
non ne parsa la prosa.

Creazione dei task (PowerShell come amministratore):

```powershell
$repo = "D:\Progetti\AI_Naples"
schtasks /create /tn "AI_Naples predict_round" /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 12:00 /f
schtasks /create /tn "AI_Naples predict_round 16" /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 16:00 /f
schtasks /create /tn "AI_Naples predict_round 19" /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 19:00 /f
schtasks /create /tn "AI_Naples close_round"  /tr "$repo\scripts\close_round.cmd"  /sc weekly /d TUE /st 09:00 /f
```

I rilanci sono ridondanti di proposito: i comandi sono idempotenti, una partita
gia' in registro viene saltata.

**Gli orari del venerdi' vanno però guardati in faccia.** football-data
pubblica le quote **entro le 17:00 UK, cioe' le 18:00 italiane**:

| orario | cosa trovera' |
|---|---|
| 12:00 | giornata ancora `futura`, zero quote |
| 16:00 | quasi certamente ancora `futura` |
| 19:00 | **l'unico che lavora davvero** |

E il margine e' stretto: la giornata 5 ha Monza-Sassuolo alle **20:45**. Se il
run delle 19:00 fallisce, quella partita e' persa per sempre dal track record —
una previsione scritta dopo il fischio non e' una previsione. La disposizione
piu' sicura, se vuoi cambiarla, e' **18:15 / 19:15 / 20:00 piu' un run sabato
alle 9:00** per le partite che lo snapshot del venerdi' non copriva.

Due impostazioni che fanno fallire i task in silenzio:

- in Utilita' di pianificazione, proprieta' del task, **"Esegui indipendentemente
  dalla connessione dell'utente"**;
- il PC non deve essere sospeso all'orario del task. Un task che non parte non
  lascia log, e te ne accorgi solo dal buco nel track record.

Verifica manuale, senza aspettare venerdi':

```powershell
schtasks /run /tn "AI_Naples predict_round"
Get-Content logs\scheduler_*.log -Tail 30
Get-Content logs\last_run.json
```

---

## 7quinquies. Frontend — React + TypeScript

```bash
cd frontend
npm install            # solo la prima volta
npm run dev            # http://localhost:5173
npm run build          # typecheck + bundle in frontend/dist
npm run preview        # serve il dist gia' costruito
npm run typecheck      # solo tsc, senza bundle
```

Serve l'API accesa: `python -m backend.api --port 8000` dalla radice del
repository, in un altro terminale.

**L'URL dell'API e' una variabile d'ambiente.** Copia `.env.example` in
`.env.local` e metti `VITE_API_BASE_URL`. Per provare un tunnel senza
ricostruire, si puo' passare `?api=https://...` nella query: viene ricordato in
`localStorage`, perche' l'URL di ngrok cambia a ogni riavvio.

**La porta 5173 non e' casuale**: e' fra le origini CORS che l'API accetta per
default. Se la cambi, aggiorna `AI_NAPLES_CORS_ORIGINS` lato backend, o il
browser blocchera' le chiamate senza che il server se ne accorga.

Due schermate, in `src/pages/`:

| schermata | cosa mostra |
|---|---|
| `/` | cosa fa il modello, l'avvertenza sul non essere uno strumento di scommessa, e tre numeri da `/api/season` |
| `/#/dashboard` | barra di stato, selettore delle 38 giornate, tabella partite con riga espandibile, pannello track record con RPS cumulativo |

Il router e' `HashRouter`: il sito e' statico e puo' finire su qualsiasi host
senza regole di rewrite, e con i path veri ricaricare `/dashboard` darebbe 404.

**Cosa il frontend non fa, e non deve fare.** Nessuna chiamata che non sia una
GET, nessun consiglio di scommessa, nessun valore atteso. Quota equa e
overround si', sono descrittivi. I campi null si mostrano vuoti — mai zero, che
su un RPS significherebbe previsione perfetta — e le percentuali portano sempre
il denominatore: `44% (7 su 16)`, mai `44%`.

---

## 8. Produzione

```bash
python -m src.predict --next                  # prossima giornata con partite future
python -m src.predict --matchday 3            # una giornata precisa
python -m src.predict --team Napoli           # prossima partita del Napoli
python -m src.predict --matchday 3 --dry-run  # mostra senza registrare

# Ricostruzione di una previsione passata: finisce in predictions_backfill.csv
python -m src.predict --as-of 2026-08-27 --next
```

```bash
python -m src.backtest_log             # metriche sulle previsioni risolte
python -m src.backtest_log --pending   # previsioni in attesa di risultato
python -m src.backtest_log --by-season # stagione per stagione
python -m src.backtest_log --backfill  # rilegge le ricostruzioni (NON e' un track record)
```

---

## 9. Test senza rete

```bash
python -m tests.test_form              # medie mobili leakage-safe
python -m tests.test_market            # de-vigging di Shin, lambda impliciti
python -m tests.test_kickoff           # fusi orari e ordine previsione/fischio
python -m tests.test_leakage           # il walk-forward non vede il futuro
python -m tests.test_predictions_log   # append-only e idempotenza del registro
python -m tests.test_api               # i sette endpoint, i null, sola lettura
```

I quattro test centrali coprono i punti in cui un errore **non darebbe
eccezioni**: un fuso sbagliato produce una data valida, un de-vigging rotto
produce tre numeri che sommano a uno, un leakage produce metriche migliori, e
un registro riscritto produce un track record piu' bello. Sono esattamente i
difetti che si auto-premiano.

`tests/make_fixtures.py` genera dati sintetici **sovrascrivendo `data/raw/`**.
Ora rifiuta di partire senza consenso esplicito:

```bash
python -m tests.make_fixtures --overwrite-raw    # DISTRUGGE i dati veri
```

Serviva: prima non aveva argparse, quindi un `--help` non stampava l'aiuto ma
**eseguiva la sovrascrittura**, e da li' `normalize --build` propagava i dati
finti fino a `matches_master`. Dopo averlo usato, rilancia l'ingestion vera.

---

## 10. File prodotti

| file | da | contiene |
|---|---|---|
| `data/raw/matches.parquet` | `--stage matches` | risultati e quote, solo partite giocate |
| `data/raw/fixtures_odds.parquet` | `--stage fixtures` | quote del turno imminente + `downloaded_at` |
| `data/raw/fbref_schedule.parquet` | `--stage schedule` | calendario completo, con la giornata |
| `data/interim/matches_master.parquet` | `normalize --build` | 4580 righe, 222 colonne |
| `data/processed/features_form.parquet` | `features.form` | medie mobili |
| `data/processed/features_market.parquet` | `features.market` | de-vigging + lambda impliciti |
| `data/processed/walk_forward_predictions.parquet` | `evaluate` | previsioni di tutti i modelli sul test |
| `data/processed/gbm_tuning.parquet` | `gbm --tune` | esito della ricerca iperparametri |
| **`track_record/predictions_log.csv`** | `predict` | **il track record. Versionato in git, append-only** |
| `track_record/predictions_log.csv.bak` | `predict` | copia di sicurezza, rifatta prima di ogni scrittura |
| `track_record/predictions_backfill.csv` | `predict --as-of` | ricostruzioni, NON versionate |
| **`track_record/rounds/round_*.csv`** | `close_round` | **giornate chiuse: previsione, risultato ed errore per partita. Versionate: il file E' lo stato** |
| `track_record/rounds/riepilogo.csv` | `close_round` | cumulativo per giornata, rifatto da zero a ogni chiusura |
| `track_record/report.html` | `*_round`, `report` | il report a percorso fisso. NON versionato: si rigenera |
| `data/processed/reports/giornata_*.html` | `*_round`, `report` | archivio del report, uno per giornata |
| **`web/openapi.json`** | `backend.api --export-openapi` | **il contratto per il frontend. Versionato: un test fallisce se diverge dall'app** |
| `logs/scheduler_*.log` | `scripts\*.cmd` | un file al giorno, output completo dei comandi. NON versionato |
| `logs/last_run.json` | `scripts\*.cmd` | esito dell'ultima esecuzione di ciascun comando, letto da `/api/status`. NON versionato |

Tutto `data/` e' in `.gitignore`.

---

## 11. Se il dataset si accorcia

`normalize --build` **rifiuta** di sostituire `matches_master.parquet` con uno
piu' corto del 20%. Il dataset cresce di dieci partite a settimana e non si
accorcia mai: un calo significa che un file in `data/raw/` e' stato
sovrascritto, non che i dati veri sono cambiati.

```
ValueError: il nuovo matches_master.parquet avrebbe 200 righe contro le 4580
attuali: un calo del 96%.
```

Rimedio: rilancia l'ingestion vera (`--stage matches`, `--stage understat`).
I CSV grezzi restano nella cache di soccerdata (`~/soccerdata/data/`), quindi
il ripristino non dipende dalla rete tranne che per la stagione in corso.

Se la riduzione e' voluta (per esempio hai ridotto `config.SEASONS`):

```bash
$env:AI_NAPLES_FORCE_BUILD = "1"; python -m src.normalize --build
```

---

## 12. Cose da non fare

- **Non lanciare `predict` dopo il calcio d'inizio.** L'assert lo blocca, ma il
  punto e' che quella riga di track record e' persa.
- **Non mescolare `predictions_backfill.csv` con `predictions_log.csv`.** Il
  primo e' costruito conoscendo il risultato, il secondo no.
- **Non tarare niente sul test set** (2324, 2425, 2526). La validazione e'
  2122-2223 e sta in `config.VALIDATION_SEASONS`.
- **Non usare le quote di chiusura** (`*C`) come feature: si formano dopo le
  formazioni ufficiali, fuori dall'orizzonte T-24h.
- **Non leggere le differenze di RPS senza il loro intervallo appaiato.** La
  varianza fra stagioni non e' una soglia di rumore.
- **Non lanciare `tests.make_fixtures` senza sapere cosa fa.** Sovrascrive
  `data/raw/` con dati sintetici; ora chiede conferma esplicita.

---

## 13. Note sull'ambiente

- Per cancellare file: `Remove-Item ... -ErrorAction SilentlyContinue`, non `rm -f`.
- Evitare `python -c "..."` con apici annidati: mettere il codice in un file.
- Understat scarica una libreria TLS da GitHub al primo avvio. Dietro proxy
  fallisce: scaricarla dalle release di `bogdanfinn/tls-client`.
- soccerdata usa una cache persistente in `~/soccerdata/data/`: rilanciare uno
  stage gia' completato non riscarica nulla, tranne la stagione in corso che
  viene sempre aggiornata.
