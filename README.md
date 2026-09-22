# ProjectSerieAI

Modello probabilistico per la previsione dei **gol** nelle partite di Serie A
(predisposto per i Big 5 europei). L'1X2 non è un target: è una lettura della
matrice dei risultati esatti.

Il risultato misurato, dichiarato qui perché è il punto del progetto:
**nessun modello statistico batte il mercato dei bookmaker**, e il modello in
produzione è per questo il mercato de-vigato. Il progetto serve a misurare
*quanto* un modello si avvicina alle quote, con quale protocollo, e dove il
mercato è cieco.

> Il progetto **non produce valore atteso positivo**. Misurato su 1140 partite
> fuori campione: 0 puntate su 3420 con EV>0 contro B365, ROI reale -8.18%.
> Non è un sistema di scommesse.

---

## Indice della documentazione

| documento | cosa contiene |
|---|---|
| **questo file** | cos'è, come si installa, com'è fatto, come si contribuisce |
| `COMANDI.md` | **come si lancia qualsiasi cosa**, con tempi misurati |
| `CLAUDE.md` | decisioni prese, risultati misurati, il **perché** di ognuno |
| `Doc/PROGETTO_SERIE_A.md` | progettazione completa e razionale di fondo |
| `Doc/AUDIT_TECNICO.md` | debiti architetturali e difetti noti, con priorita' |

I comandi vivono **solo** in `COMANDI.md`. Due elenchi divergono, e viene
sempre letto quello sbagliato.

---

## Cosa fa

1. **Scarica** risultati, quote, xG/PPDA, calendari e indisponibili da quattro
   fonti (football-data.co.uk, Understat, FBref, WhoScored).
2. **Normalizza e unisce** le fonti su una chiave stabile
   `(league, season, home_team, away_team)` — mai la data.
3. **Costruisce feature** leakage-safe: forma recente (medie mobili
   esponenziali), mercato (quote de-viggate), contesto (riposo, coppe, derby),
   giocatori (minuti indisponibili).
4. **Addestra e valuta** una scala di modelli con walk-forward per giornata,
   riaddestramento da zero, RPS come metrica primaria.
5. **Predice** la giornata in arrivo e **registra** la previsione prima del
   calcio d'inizio, in un log append-only versionato.
6. **Chiude** la giornata dopo che si e' giocata, calcola l'errore reale e
   genera un report HTML statico.

## Come lo fa

### La scala dei modelli

| id | modello | ruolo |
|---|---|---|
| M0 | vince sempre la casa | pavimento banale |
| M0b | frequenze di base | vero pavimento probabilistico |
| **M1 / M1b** | **market-only, quote de-viggate** | **riferimento, e modello di produzione** |
| M2 | GLM Poisson attacco/difesa su finestra mobile | primo modello vero |
| M3 | Dixon-Coles con decadimento temporale e rho stimato | parametrico completo |
| M4 | LightGBM Poisson, due gol separati | test sulle statistiche di gioco |
| M5 | LightGBM **ancorato** al mercato (`init_score=log(λ)`) | test piu' potente disponibile |
| M6 | miscela geometrica mercato/GBM | controllo a un parametro |

Ogni modello espone `fit(train)` / `predict(test)` e restituisce le stesse
colonne, quindi l'harness di valutazione non sa chi sta valutando.

### Il cuore: λ → matrice → mercati

Tutto passa per un punto solo. Un modello produce due λ (gol attesi casa e
trasferta); da li' `models/baseline.score_matrix` costruisce la matrice
P(i gol casa, j gol trasferta) con correzione Dixon-Coles opzionale, e 1X2,
over/under, gol-gol e doppia chance sono **somme diverse sulle stesse celle**.
Restano coerenti fra loro per costruzione: `P(1X)` e' esattamente `P(1)+P(X)`.

### Perche' M1 e' in produzione

Il test set (stagioni 2324-2526, 1140 partite, walk-forward a 114 blocchi) dice:

| modello | RPS | verdetto |
|---|---|---|
| M1b market-only | **0.1881** | soglia da battere |
| M5 ancorato | 0.1882 | indistinguibile |
| M4 con mercato | 0.1905 | peggio |
| M4 senza mercato | 0.1944 | peggio |
| M0b frequenze | 0.2291 | pavimento |

Un modello entra in produzione **solo** se batte il mercato con intervallo di
confidenza che non tocca lo zero, dopo correzione per confronti multipli.
Finora nessuno ci e' riuscito.

---

## Installazione

Richiede Python 3.10+ (usa `X | Y` nelle annotazioni di tipo).

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows / PowerShell — ambiente di sviluppo
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
```

Lo stage `missing` (indisponibili da WhoScored) richiede Selenium e un browser
installato: e' facoltativo, il modello T-24h parte senza.

### Primo avvio

```bash
python ingest.py --stage matches       # ~10 s
python ingest.py --stage understat     # ~2 min
python ingest.py --stage schedule      # ~5 min
python -m src.normalize --report       # diagnosi nomi squadra
python -m src.normalize --build        # costruisce matches_master
python -m src.features.form
python -m src.features.market
python -m src.features.context
python -m src.evaluate                 # tabella modelli + confronti appaiati
```

Dettagli, tempi e casi particolari in `COMANDI.md` sezione 2.

---

## Uso quotidiano: due comandi

L'unita' di lavoro e' la **giornata di campionato**, non la settimana. Ha uno
stato, e lo stato si deduce dai dati — nessun comando chiede il numero e
nessuno sa che giorno e'.

```bash
python -m src.predict_round     # da aperta a predetta
python -m src.close_round       # da giocata a chiusa
python -m src.rounds --status   # dove sta ogni giornata della stagione
```

| stato | condizione |
|---|---|
| futura | nessuna quota disponibile |
| aperta | quote presenti, partite non giocate |
| predetta | previsioni registrate |
| giocata | tutti i risultati disponibili |
| chiusa | errore calcolato, giornata archiviata |

Entrambi sono **idempotenti**: rilanciarli non sporca niente, e se non c'e'
nulla da fare escono con codice 0 nominando la giornata e il suo stato.

---

## Architettura

```
ingest.py                 scaricamento multi-fonte (4 fonti, 8 stage)
src/
  config.py               percorsi, leghe, stagioni, iperparametri — unica fonte
  normalize.py            nomi squadra (assegnamento bipartito) + join → matches_master
  features/
    form.py               medie mobili esponenziali leakage-safe
    market.py             de-vigging (Shin + proporzionale) → λ impliciti
    context.py            riposo, congestione, coppe, derby        [SCARTATO]
    players.py            minuti e gol+assist indisponibili        [PROVVISORIO]
    sets.py               registro dei set di feature, con stato
  models/
    baseline.py           M0/M0b/M1/M1b/M2 + score_matrix condivisa
    dixon_coles.py        M3, gradiente analitico
    gbm.py                M4/M5/M6, ricerca iperparametri
  evaluate.py             RPS, calibrazione, walk-forward, bootstrap a cluster
  predict.py              inferenza + registro append-only
  rounds.py               ciclo di vita della giornata
  predict_round.py        aperta → predetta
  close_round.py          giocata → chiusa
  backtest_log.py         rilettura del registro, metriche reali
  report.py               report HTML statico
  power_analysis.py       effetto minimo rilevabile
  experiments/            fuori produzione, guardia a runtime sulle scritture
tests/                    dati sintetici e test di regressione
track_record/             registro previsioni + giornate archiviate (VERSIONATO)
manual/                   mappe e file compilati a mano
data/                     tutti i parquet (GITIGNORATO, si rigenera)
```

### Flusso dei dati

```
fonti → data/raw/*.parquet → normalize → data/interim/matches_master.parquet
                                              ↓
                          data/processed/features_{form,market,context,players}.parquet
                                              ↓
                       evaluate.load_dataset() ──→ walk-forward ──→ metriche
                                              ↓
                       predict.build_features() ─→ previsione ──→ track_record/
```

### Pattern riconosciuti

- **Strategy** sui modelli: interfaccia `Model.fit/predict` uniforme, l'harness
  e' agnostico.
- **Template method**: `MarketAnchoredGBM` sottoclassa `PoissonGBM` e
  sovrascrive solo `fit`/`predict`; gli esperimenti sottoclassano ancora senza
  toccare la produzione.
- **Pipeline a stage idempotenti**: ogni fase legge parquet, scrive parquet, si
  rilancia senza effetti collaterali.
- **State machine derivata dai dati**: lo stato di una giornata non e' una
  colonna, e' l'esistenza di un file — non puo' divergere dai fatti.
- **Registry**: `features/sets.py` mappa ogni colonna a un set con uno stato.
- **Guardia a runtime**: `experiments.proteggi_produzione()` sostituisce
  `to_parquet`/`to_csv` e rifiuta le scritture in produzione.
- **Fail-fast selettivo**: i passi di rete non sono fatali (i siti cadono), i
  passi locali si' (su dati incoerenti ogni previsione sarebbe sbagliata in
  silenzio).

---

## Le regole non negoziabili

Chiunque tocchi il codice deve conoscerle. La versione lunga, con il perche',
sta in `CLAUDE.md`.

1. **Nessun leakage.** Ogni feature dev'essere calcolabile prima del calcio
   d'inizio. Le statistiche della partita stessa servono solo a costruire
   aggregati storici.
2. **Mai `train_test_split` casuale.** Split temporale, walk-forward per
   giornata, taglio sulla **data** e non sulla giornata.
3. **RPS come metrica primaria**, non accuracy.
4. **Le finestre mobili non si azzerano al confine di stagione**: regressione
   verso la media di lega del 30%.
5. **Chiave di join** `(league, season, home_team, away_team)`, mai la data.
6. **Coerenza sulle quote**: apertura in backtest e in produzione, sempre.
7. **Una differenza conta solo se il suo intervallo non contiene lo zero**,
   con bootstrap a cluster sulla giornata.
8. **Un blocco di feature non misurato non entra nel modello.**

---

## Test

Non c'e' un runner: ogni test e' un modulo eseguibile.

```bash
python -m tests.test_form                 # medie mobili leakage-safe
python -m tests.test_market               # de-vigging, lambda impliciti
python -m tests.test_kickoff              # fusi orari, ordine previsione/fischio
python -m tests.test_leakage              # il walk-forward non vede il futuro
python -m tests.test_predictions_log      # append-only e idempotenza
python -m tests.test_production_unchanged # M1 identico bit a bit
python -m tests.test_sets                 # BASE non e' cambiato
```

Coprono i punti in cui un errore **non darebbe eccezioni**: un fuso sbagliato
produce una data valida, un de-vigging rotto produce tre numeri che sommano a
uno, un leakage produce metriche migliori. Sono i difetti che si auto-premiano.

`tests/make_fixtures.py` **sovrascrive `data/raw/`**: richiede
`--overwrite-raw` esplicito.

---

## Contribuire

### Prima di ogni commit

```bash
python -m tests.test_production_unchanged   # M1 e le quote non sono cambiati
python -m tests.test_leakage
python -m tests.test_sets
```

`test_production_unchanged` fissa su 101 partite gia' giocate le quote di
ingresso, le fonti scelte, i λ di mercato e tutti i mercati di `all_markets`, e
verifica che restino identici **bit a bit**. Va rigenerato (`--rigenera`) solo
dopo un cambiamento di produzione voluto e dichiarato.

### Regole di contribuzione

1. **La produzione non si tocca per un esperimento.** `predict_round`,
   `close_round`, `report` e M1 devono funzionare identici. Se un esperimento
   richiede di cambiare un modulo condiviso, lo si **duplica o sottoclassa**.
2. **Un blocco di feature nuovo si misura prima di entrare.** Si rifa' M5, si
   fa il confronto appaiato con bootstrap a cluster, e il blocco entra solo se
   l'intervallo sta tutto sotto zero. Se non passa, si **rimuove**: con ~3400
   righe di training la diluizione fa danno davvero.
3. **Si dichiara la specifica prima di guardare i risultati.** Una specifica
   scelta dopo non e' un risultato: e' un'ipotesi, e va in "Ipotesi
   pre-registrate" di `CLAUDE.md`.
4. **Un comando nuovo si documenta in `COMANDI.md`**, non qui e non in
   `CLAUDE.md`.
5. **Gli esperimenti vivono in `src/experiments/`** e chiamano
   `experiments.proteggi_produzione()` in testa.

### Stile

- Type hints ovunque.
- Docstring in **italiano** che spiegano il **perche'**, non il cosa.
- Ogni modulo eseguibile con `python -m src.<modulo>` e un `argparse`.
- Parquet per tutti i dati intermedi.
- `logging`, mai `print` (tranne l'impaginazione a terminale dei comandi).
- Niente notebook nel codice di produzione.
- DRY e SOLID: vedi la sezione "Principi di progettazione" di `CLAUDE.md`.

### Ambiente

Si sviluppa su **Windows con PowerShell**. Per cancellare file usare
`Remove-Item ... -ErrorAction SilentlyContinue`, non `rm -f`. Evitare
`python -c "..."` con apici annidati.

---

## Stato e limiti noti

- Perimetro attuale: **solo Serie A**, 13 stagioni, 4580 partite. I Big 5 sono
  gia' configurabili da `config.LEAGUES` e valgono ~2.2x di potenza statistica.
- Blocco contesto: **misurato e scartato** (il mercato lo ha gia' prezzato).
- Blocco giocatori: **provvisorio**, non sopravvive alla correzione per
  confronti multipli.
- ClubElo era irraggiungibile all'ultima ingestion: lo stage `elo` e'
  facoltativo.
- I debiti architetturali e i difetti noti sono elencati e ordinati per
  priorita' in `Doc/AUDIT_TECNICO.md`.

## Licenza

Non specificata. I dati provengono da fonti terze con i loro termini d'uso.
