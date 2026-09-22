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
```

Addestra M5 (GBM Poisson ancorato al mercato, media di 5 semi, le 52 colonne di
BASE) su tutto lo storico giocato, poi riusa `predict.py` invariato con
`dry_run=True`. Stampa M5 accanto a M1 ordinato per scarto sull'1X2 e scrive
`experiments/output/previsioni_m5_<stagione>_<NN>.csv`. **Non scrive nel
registro**: il track record resta di M1, che e' il modello di produzione.

`--as-of` rifa' una giornata gia' giocata con le quote di allora **e taglia il
training a quella data**, altrimenti il modello si addestrerebbe anche sulla
giornata che dice di prevedere.

Serve che le quote del turno ci siano: `goalmodel ingest --stage fixtures`.
Se football-data non ha ancora pubblicato la Serie A, il file ha zero righe, il
comando lo dice e non predice niente — non e' un errore, e' il turno non ancora
uscito.

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
