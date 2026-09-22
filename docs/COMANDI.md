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

## 0. Come si invocano i comandi

Dopo `pip install -e ".[dev]"` il comando `goalmodel` e' sulla PATH e funziona
**da qualsiasi directory**, identico su Linux e su Windows:

```bash
goalmodel --help              # l'elenco dei comandi
goalmodel <comando> --help    # le opzioni di uno
```

Ogni comando resta raggiungibile anche come modulo, che e' la forma da usare
per gli esperimenti (non sono comandi di produzione e non stanno nel CLI):

```bash
python -m goalmodel.prediction.predict_round        # = goalmodel predict-round
python -m goalmodel.experiments.predici_gbm         # solo cosi'
```

Ambiente, una volta sola:

```bash
pip install -r requirements.lock      # l'ambiente esatto, riproducibile
pip install -e ".[dev]" --no-deps     # il pacchetto in modalita' sviluppo
```

**Installare dal lock e non da `pyproject.toml`**: il progetto verifica alcune
uscite bit a bit, e una versione diversa di LightGBM o scipy fa fallire quei
test senza che il codice sia cambiato.

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
goalmodel predict-round     # da aperta a predetta
goalmodel close-round       # da giocata a chiusa
goalmodel rounds --status   # dove sta ogni giornata della stagione
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

`goalmodel predict-round` accetta anche `--no-open`, per non aprire il
report nel browser.

### `predict_round` — da aperta a predetta

```bash
goalmodel predict-round
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
goalmodel close-round
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
goalmodel rounds --status
goalmodel rounds --status --season 2526
```

Una riga per giornata: date, partite, quote, previsioni, risultati, stato e RPS
se chiusa. `>` marca la giornata che `predict_round` predirebbe adesso, `*`
quella che `close_round` chiuderebbe.

### `weekly` non esiste piu'

`python -m goalmodel.prediction.weekly` stampa i due comandi nuovi ed esce con codice 2. Faceva
due cose con precondizioni opposte — predire vuole le quote e nessun risultato,
chiudere vuole tutti i risultati — quindi una delle due era sempre fuori tempo.

### Il report su file

Entrambi i comandi scrivono un report HTML, e lo si puo' rigenerare da solo:

```bash
goalmodel report               # rigenera track_record/report.html
goalmodel report --open        # lo rigenera e lo apre nel browser
goalmodel report --no-diverge  # salta la sezione 3, che addestra M4
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

`goalmodel report` **non tocca mai il registro**: le previsioni si scrivono
una volta sola, da `goalmodel predict-round`, prima del calcio d'inizio. Il
report lo dichiara in fondo, cosi' un file rigenerato non si confonde con quello
del comando vero.

Il report e' una **vista**: il dato e' `predictions_log.csv`. Rigenerarlo non
cambia nulla, cancellarlo nemmeno. Per questo `report.html` sta in
`track_record/` ma **non** e' versionato.

### Se serve fare i passi a mano

```bash
goalmodel ingest --stage matches      # risultati appena giocati
goalmodel ingest --stage understat
goalmodel ingest --stage schedule
goalmodel ingest --stage fixtures     # quote del turno imminente
goalmodel ingest --stage cups         # calendario Champions/Europa/Conference
goalmodel ingest --stage missing      # infortunati WhoScored: LENTO, ~11s/partita, browser
goalmodel ingest --stage player_stats # minuti e gol per giocatore: LENTO, ~13s/partita
goalmodel normalize --build
goalmodel features-form
goalmodel features-market
goalmodel predict --next          # SCRIVE nel registro
goalmodel backtest
```

**`predict` e' l'unico passaggio non recuperabile.** Verifica con un assert che
il timestamp UTC preceda il calcio d'inizio: se il registro non viene scritto
in tempo, quella giornata e' persa per sempre ai fini del track record.
Ricostruirla dopo con `--as-of` finisce in un file separato che **non** e' un
track record.

---

## 2. Primo avvio, da zero

```bash
python -m pip install -r pyproject.toml

goalmodel ingest --stage matches
goalmodel ingest --stage understat
goalmodel ingest --stage schedule
goalmodel ingest --stage fixtures

goalmodel normalize --report --apply   # costruisce team_name_map.json
goalmodel normalize --build

goalmodel features-form
goalmodel features-market

goalmodel evaluate                     # ~10 minuti, riproduce la tabella
```

Gli stage lenti (`lineups`, `player_stats`, `missing`) non servono a niente di
quanto e' stato costruito finora: saltali.

---

## 3. Ingestion

| comando | tempo | scrive |
|---|---|---|
| `goalmodel ingest --stage matches` | ~10s | `data/raw/matches.parquet` |
| `goalmodel ingest --stage understat` | ~2 min | `understat_schedule`, `understat_team_match` |
| `goalmodel ingest --stage fixtures` | ~5s | `data/raw/fixtures_odds.parquet` |
| `goalmodel ingest --stage schedule` | ~5 min | `data/raw/fbref_schedule.parquet` |
| `goalmodel ingest --stage elo` | ~1 min | `clubelo_history` — opzionale, servizio spesso giu' |

Quando football-data e' irraggiungibile (risponde 503 su tutto il dominio piu'
spesso di quanto dovrebbe), scarica `fixtures.csv` dal browser e poi:

```bash
goalmodel ingest --stage fixtures --fixtures-file C:\percorso\fixtures.csv
```

**Stage lenti**, ore, rate-limited, interrompibili grazie alla cache:

```bash
goalmodel ingest --stage lineups
goalmodel ingest --stage player_stats
goalmodel ingest --stage missing        # richiede Chrome/Chromium
```

---

## 4. Normalizzazione

```bash
goalmodel normalize --report           # diagnosi dei nomi squadra
goalmodel normalize --report --apply   # scrive la mappa automatica
goalmodel normalize --build            # costruisce matches_master.parquet
```

`--build` fallisce se il join scende sotto la soglia: e' voluto, significa che
mancano voci in `manual/team_name_map.json`.

---

## 5. Feature

```bash
goalmodel features-form                # medie mobili leakage-safe
goalmodel features-market              # de-vigging Shin + proporzionale
goalmodel features-market --coverage   # copertura quote, book per stagione
goalmodel features-context             # blocco A: riposo, congestione, derby
goalmodel features-players             # blocco B: peso delle assenze
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
goalmodel evaluate                # ~10 min — tabella + confronto appaiato
goalmodel evaluate --calibration  # curve di calibrazione ed ECE
goalmodel evaluate --bias         # favourite-longshot, stagione per stagione
goalmodel evaluate --no-save      # non riscrive walk_forward_predictions
```

Il default riaddestra 10 modelli su 114 giornate: e' il grosso dei 10 minuti.

---

## 7. Modelli: controlli e taratura

**La taratura gira SEMPRE sulla validazione (2122, 2223), mai sul test.**
Se ritari qualcosa, aggiorna il valore in `src/goalmodel/config.py` e rifai il punto 6.

```bash
# Controlli di correttezza (secondi)
goalmodel baseline --demo        # matrice risultati, segno di rho, gol-gol
goalmodel dixon-coles --check    # gradiente analitico vs differenza centrata

# Taratura
goalmodel dixon-coles --tune               # 28s  -> config.DC_HALFLIFE
goalmodel gbm --tune --workers 8           # ~11 min, tutte le varianti
goalmodel gbm --tune --variants ancorato   # ~2 min, solo M5
goalmodel gbm --blend                      # 61s  -> config.BLEND_WEIGHT

# Diagnostica
goalmodel gbm --importance       # 4s — cosa usa M4 senza mercato

# Misura di un blocco di feature, col protocollo fissato (~40 min)
goalmodel evaluate --blocco contesto
goalmodel evaluate --blocco giocatori

# Il blocco spiega il contenuto o solo la disponibilita' del dato? (~10 min)
goalmodel evaluate --blocco-nan giocatori

# Potenza: quale effetto questo test set puo' vedere? (~40s)
goalmodel power
goalmodel power --quota-partite 0.15 --shift-lambda 0.20
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

Tutto quello che sta in `src/goalmodel/experiments/` legge i dati veri e scrive **solo**
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
goalmodel features-sets                 # stato dei set di feature sul dataset
python -m tests.test_sets                   # BASE congelato, set disgiunti
python -m tests.test_experiments_modelli    # M5Set, M5Colonne, media dei semi
python -m tests.test_forma_venue            # forma per sede: niente leakage
```

**Blocco B, verifiche di robustezza** (~9 min con 8 processi; i risultati non
promuovono il blocco, possono solo declassarlo):

```bash
python -m goalmodel.experiments.blocco_b_robustezza --lancia --paralleli 8
python -m goalmodel.experiments.blocco_b_robustezza --analizza
python -m goalmodel.experiments.blocco_b_robustezza --importanza   # asimmetria su 5 semi
```

**Sulla sola validazione** (2122, 2223) — non consumano confronti sul test:

```bash
python -m goalmodel.experiments.collinearita --descrivi
python -m goalmodel.experiments.collinearita --misura         # selezione e PCA contro BASE
python -m goalmodel.experiments.baseline_mercato              # de-vigging e consenso di book
python -m goalmodel.experiments.forma_venue --descrivi
python -m goalmodel.experiments.forma_venue --lancia          # 5 semi in parallelo
python -m goalmodel.experiments.forma_venue --analizza
```

Un walk-forward gia' fatto non si rifa': `--lancia` salta i semi il cui file
c'e' gia'. Per rifarlo, cancella il parquet in `experiments/output/`.

### Prevedere una giornata con LightGBM (M5), senza toccare il registro

```bash
python -m goalmodel.experiments.predici_gbm                      # prima giornata utile
python -m goalmodel.experiments.predici_gbm --matchday 5
python -m goalmodel.experiments.predici_gbm --semi 0             # un seme solo, piu' veloce
python -m goalmodel.experiments.predici_gbm --team Napoli
python -m goalmodel.experiments.predici_gbm --as-of 2026-09-11 --matchday 4
python -m goalmodel.experiments.predici_gbm --verifica           # a risultati usciti
python -m goalmodel.experiments.predici_gbm --verifica --matchday 4
```

Addestra M5 (GBM Poisson ancorato al mercato, media di 5 semi, le 52 colonne di
BASE) su tutto lo storico giocato, poi riusa `predict.py` invariato con
`dry_run=True`. Stampa M5 accanto a M1 ordinato per scarto sull'1X2 e scrive
`experiments/output/previsioni_m5_<stagione>_<NN>.csv`. **Non scrive nel
registro**: il track record resta di M1, che e' il modello di produzione.

`--as-of` rifa' una giornata gia' giocata con le quote di allora **e taglia il
training a quella data**, altrimenti il modello si addestrerebbe anche sulla
giornata che dice di prevedere.

**`--verifica` e' il lunedi'.** Rilegge i file salvati, li aggancia ai risultati
sulla quadrupla e stampa, sulle STESSE partite, RPS ed esiti dei due modelli con
la differenza appaiata. Per questo il file salvato porta `season`, `matchday`,
le due squadre, il calcio d'inizio e `timestamp_prediction`: senza la chiave non
si aggancia, e senza l'istante una previsione a risultati usciti non si
distingue da un commento.

I file scritti prima che il formato avesse la chiave vengono **saltati con un
avviso**, non fusi: fonderli darebbe righe che il merge non aggancia e che
resterebbero per sempre "non ancora giocate". Si riscrivono con `--as-of`.

Niente intervalli di confidenza su dieci partite: sul test set la differenza fra
i due e' +0.00011 con IC [-0.00056, +0.00077], e un bootstrap su una giornata
sarebbe un ornamento. Questo conto dice cosa e' successo, non promuove niente.

Serve che le quote del turno ci siano: `goalmodel ingest --stage fixtures`.
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

### Far vedere il sito a qualcuno — UN SOLO TUNNEL, sulla 5173

```bash
python -m backend.api --port 8000      # terminale 1, resta su 127.0.0.1
cd frontend && npm run dev             # terminale 2
ngrok http 5173                        # terminale 3 — e' questo l'URL da dare
```

Il tunnel va sul **frontend**, non sull'API. Chi apre il link chiede
`/api/...` alla stessa origine della pagina, il server di sviluppo gira quelle
richieste all'API locale (`server.proxy` in `vite.config.ts`), e l'API resta
raggiungibile solo da questo computer.

**Perche' non due tunnel.** Sarebbe la strada ovvia — uno sulla 5173 e uno
sulla 8000, con `?api=` a dire al frontend dove chiamare — e costa due URL da
tenere allineati a ogni riavvio, il dominio del frontend da aggiungere in
`AI_NAPLES_CORS_ORIGINS`, e nel piano gratuito di ngrok un secondo agente che
non c'e'. Con il proxy non esiste una seconda origine, quindi il CORS non entra
nemmeno in gioco.

Tre cose che facevano fallire questo giro, tutte verificate:

- **Vite ascoltava solo su `[::1]`.** `netstat` mostrava `[::1]:5173` in
  ascolto e niente su `127.0.0.1:5173`: dal browser di casa il sito si apriva,
  ma un tunnel che si collega all'IPv4 di loopback prendeva "connection
  refused". Ora `server.host: true`, quindi il server di sviluppo e'
  raggiungibile anche dalla rete locale finche' resta acceso.
- **Vite rifiuta gli Host che non conosce** ("Blocked request. This host is not
  allowed"), e un tunnel ne porta sempre uno nuovo: in `allowedHosts` ci sono i
  domini dei servizi di tunneling, non `true` — che accetterebbe qualunque
  Host, DNS rebinding compreso.
- **`127.0.0.1:8000` fuori da localhost e' il computer di chi guarda.** Se la
  pagina non arriva da localhost, `client.ts` chiama la propria origine invece
  dell'indirizzo locale: li' quell'indirizzo non e' "quasi giusto", e' l'API di
  qualcun altro.

Per esporre invece la sola API (un altro frontend, una prova con `curl`):
`ngrok http 8000`, e il dominio del frontend va in `AI_NAPLES_CORS_ORIGINS`.
L'URL pubblico cambia a ogni riavvio nel piano gratuito, quindi il frontend lo
legge da `?api=` o da una variabile d'ambiente, mai dal codice.

Per un deploy stabile, in ordine di parti mobili:

1. **Export statico** — se il frontend non ha bisogno dei filtri, i JSON su
   GitHub Pages: zero server, zero uptime da garantire.
2. **Container** con `track_record/` montato in sola lettura.
3. **VPS** con uvicorn dietro nginx e HTTPS.

---

## 7quater. Scheduler di Windows

```bash
scripts\predict_round.cmd     # venerdi' — M1 nel registro, poi M5 in coda
scripts\close_round.cmd       # martedi'
scripts\predict_m5.cmd        # solo M5, se serve lanciarlo a parte
```

**`predict_round.cmd` fa due cose, in quest'ordine**: scrive le previsioni di
M1 nel registro, poi lancia M5 che salva la sua opinione sulla stessa giornata
in `experiments/output/`. Concatenati e non due task separati per due motivi:
M5 non fa ingestion propria e deve leggere il dataset che `predict_round` ha
appena ricostruito; e ogni trigger che fa partire l'uno — compresi i recuperi
che Windows esegue in ritardo al risveglio — copre anche l'altro, senza una
seconda serie di orari da tenere allineata.

**L'uscita di M5 viene scartata di proposito.** E' diagnostica: se il GBM non
si addestra, il registro e' stato scritto lo stesso, e il task non deve
riportare una giornata fallita per qualcosa che il registro non lo tocca
nemmeno. Per lo stesso motivo M5 non compare in `last_run.json`: `/api/status`
parla del track record, e un'entrata li' farebbe pensare che sia stato toccato.

Registrano tutto in `logs\scheduler_YYYYMMDD.log` e scrivono `logs\last_run.json`,
che e' quello che legge `GET /api/status` — il log resta per gli umani, l'API
non ne parsa la prosa.

**I task sono registrati su questa macchina dal 18 settembre 2026.** Questi sono
i comandi con cui ricrearli altrove, o qui dopo una reinstallazione:

```powershell
$repo = "D:\Progetti\AI_Naples"
schtasks /create /tn "AI_Naples predict_round 1815"   /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 18:15 /f
schtasks /create /tn "AI_Naples predict_round 1915"   /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 19:15 /f
schtasks /create /tn "AI_Naples predict_round 2000"   /tr "$repo\scripts\predict_round.cmd" /sc weekly /d FRI /st 20:00 /f
schtasks /create /tn "AI_Naples predict_round sabato" /tr "$repo\scripts\predict_round.cmd" /sc weekly /d SAT /st 09:00 /f
schtasks /create /tn "AI_Naples close_round"          /tr "$repo\scripts\close_round.cmd"   /sc weekly /d TUE /st 09:00 /f
```

**Le impostazioni predefinite di `schtasks` fanno fallire i task in silenzio, e
vanno cambiate subito dopo averli creati.** Di default Windows non avvia un task
se il portatile e' a batteria, e lo ferma se ci passa mentre gira: su un
portatile staccato dalla presa non parte niente e non resta traccia.

```powershell
foreach ($n in (Get-ScheduledTask -TaskName "AI_Naples*").TaskName) {
  Set-ScheduledTask -TaskName $n -Settings (New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew)
}
```

- `-StartWhenAvailable` recupera un orario mancato (PC spento alle 18:15, acceso
  alle 21): il run parte appena puo'. Le partite gia' cominciate restano fuori
  da sole, se ne occupa l'assert sul calcio d'inizio.
- `-WakeToRun` sveglia il PC sospeso. Funziona solo se i timer di riattivazione
  non sono disabilitati nelle opzioni di risparmio energia.
- `-MultipleInstances IgnoreNew`: se il run delle 18:15 sta ancora girando alle
  19:15, il secondo non parte invece di sovrapporsi.
- Serve comunque che il PC sia **acceso**: un task che non parte non lascia log,
  e te ne accorgi solo dal buco nel track record.

I rilanci sono ridondanti di proposito: i comandi sono idempotenti, una partita
gia' in registro viene saltata.

**Perche' quegli orari.** football-data pubblica le quote **entro le 17:00 UK,
cioe' le 18:00 italiane**, e il margine fino al primo fischio e' stretto: la
giornata 5 ha Monza-Sassuolo alle **20:45**.

| orario | cosa trova |
|---|---|
| ven 18:15 | le quote appena pubblicate, nel caso normale |
| ven 19:15 | rete di sicurezza se alle 18 non erano ancora uscite |
| ven 20:00 | ultima occasione prima del fischio delle 20:45 |
| sab 09:00 | le partite che lo snapshot del venerdi' non copriva |

Una previsione scritta dopo il fischio non e' una previsione: se tutti i run del
venerdi' saltano, la partita del venerdi' sera e' persa per sempre dal track
record, mentre le altre nove restano recuperabili il sabato.

Verifica manuale, senza aspettare venerdi':

```powershell
schtasks /run /tn "AI_Naples predict_round 1815"
Get-ScheduledTask -TaskName "AI_Naples*" | Get-ScheduledTaskInfo |
  Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult
Get-Content logs\scheduler_*.log -Tail 30
Get-Content logs\last_run.json
```

`LastTaskResult` e' un codice Windows, non l'uscita del comando: `267011`
(0x41303) vuol dire "mai eseguito", `267009` (0x41301) "in esecuzione adesso",
`0` finito bene. L'esito vero del comando sta in `last_run.json`.

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
goalmodel predict --next                  # prossima giornata con partite future
goalmodel predict --matchday 3            # una giornata precisa
goalmodel predict --team Napoli           # prossima partita del Napoli
goalmodel predict --matchday 3 --dry-run  # mostra senza registrare

# Ricostruzione di una previsione passata: finisce in predictions_backfill.csv
goalmodel predict --as-of 2026-08-27 --next
```

```bash
goalmodel backtest             # metriche sulle previsioni risolte
goalmodel backtest --pending   # previsioni in attesa di risultato
goalmodel backtest --by-season # stagione per stagione
goalmodel backtest --backfill  # rilegge le ricostruzioni (NON e' un track record)
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
$env:AI_NAPLES_FORCE_BUILD = "1"; goalmodel normalize --build
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
