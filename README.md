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
| `docs/PROGETTO_SERIE_A.md` | progettazione completa e razionale di fondo |
| `docs/AUDIT_TECNICO.md` | debiti architetturali e difetti noti, con priorita' |

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
pip install -r pyproject.toml
```

Lo stage `missing` (indisponibili da WhoScored) richiede Selenium e un browser
installato: e' facoltativo, il modello T-24h parte senza.

### Primo avvio

```bash
goalmodel ingest --stage matches       # ~10 s
goalmodel ingest --stage understat     # ~2 min
goalmodel ingest --stage schedule      # ~5 min
goalmodel normalize --report       # diagnosi nomi squadra
goalmodel normalize --build        # costruisce matches_master
goalmodel features-form
goalmodel features-market
goalmodel features-context
goalmodel evaluate                 # tabella modelli + confronti appaiati
```

Dettagli, tempi e casi particolari in `COMANDI.md` sezione 2.

---

## Uso quotidiano: due comandi

L'unita' di lavoro e' la **giornata di campionato**, non la settimana. Ha uno
stato, e lo stato si deduce dai dati — nessun comando chiede il numero e
nessuno sa che giorno e'.

```bash
goalmodel predict-round     # da aperta a predetta
goalmodel close-round       # da giocata a chiusa
goalmodel rounds --status   # dove sta ogni giornata della stagione
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
pyproject.toml            packaging, dipendenze vincolate, config di ruff/pytest/mypy
requirements.lock         ambiente esatto, universale (Linux + Windows)
.github/workflows/ci.yml  test su due sistemi operativi a ogni push
docs/                     COMANDI.md, PROGETTO_SERIE_A.md, AUDIT_TECNICO.md, adr/
src/goalmodel/
  config.py               percorsi, leghe, stagioni, iperparametri, JOIN_KEYS
  cli.py                  entry point unico: `goalmodel <comando>`
  data.py                 punto unico di accesso ai file su disco
  ingest.py               scaricamento multi-fonte (4 fonti, 8 stage)
  whoscored_patch.py      aggancio allo scraper localizzato
  normalize.py            nomi squadra (assegnamento bipartito) + join
  features/
    form.py               medie mobili esponenziali leakage-safe
    market.py             de-vigging (Shin + proporzionale) -> lambda impliciti
    context.py            riposo, congestione, coppe, derby        [SCARTATO]
    players.py            minuti e gol+assist indisponibili        [PROVVISORIO]
    sets.py               registro dei set di feature, con stato
    registry.py           quali blocchi esistono e come si costruiscono
  models/
    baseline.py           M0/M0b/M1/M1b/M2 + score_matrix condivisa
    dixon_coles.py        M3, gradiente analitico
    gbm.py                M4/M5/M6, ricerca iperparametri
  evaluation/
    evaluate.py           RPS, calibrazione, walk-forward, bootstrap a cluster
    power_analysis.py     effetto minimo rilevabile
  prediction/
    predict.py            inferenza + registro append-only
    rounds.py             ciclo di vita della giornata
    predict_round.py      aperta -> predetta
    close_round.py        giocata -> chiusa
    backtest_log.py       rilettura del registro, metriche reali
  reporting/
    report.py             report HTML statico
  experiments/            fuori produzione, guardia a runtime sulle scritture
scripts/                  utilita' di ispezione, fuori dal pacchetto
tests/                    dati sintetici e test di regressione
track_record/             registro previsioni + giornate archiviate (VERSIONATO)
manual/                   mappe e file compilati a mano
data/                     tutti i parquet (GITIGNORATO, si rigenera)
```

### I livelli, e la regola che li tiene separati

Ogni livello puo' importare **solo quelli sotto di se'**:

```
config -> ingest, normalize -> features/ -> models/ -> evaluation/
                                                    -> prediction/ -> reporting/
```

`experiments/` importa tutto e non e' importato da nessuno. La decisione di
restare su un repository solo, e i segnali che la riaprirebbero, stanno in
[docs/adr/0001-monolite-modulare.md](docs/adr/0001-monolite-modulare.md).

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

```bash
pytest                      # tutta la suite: ~1 s senza data/
pytest -m richiede_dati     # solo quelli che vogliono il dataset vero
ruff check .                # lint, le stesse regole della CI
```

I test che hanno bisogno di `data/` sono marcati `richiede_dati` e si
**saltano** con un messaggio quando il dataset non c'e'. E' la differenza fra
"qui non puo' girare" e "e' rotto": in CI, dove `data/` non esiste mai, quella
distinzione e' tutto.

Ogni test resta anche un modulo eseguibile, come prima:

```bash
python -m tests.test_leakage              # il walk-forward non vede il futuro
python -m tests.test_production_unchanged # M1 identico bit a bit
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
ruff check .                                # gli stessi controlli della CI
pytest                                      # deve essere verde
python -m tests.test_production_unchanged   # M1 e le quote non sono cambiati
```

La CI gira su **Linux e Windows** a ogni push: un test che passa solo su una
delle due piattaforme blocca meta' del team, e senza matrice non c'era modo di
accorgersene.

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
5. **Gli esperimenti vivono in `src/goalmodel/experiments/`** e chiamano
   `experiments.proteggi_produzione()` in testa.

### Stile

- Type hints ovunque.
- Docstring in **italiano** che spiegano il **perche'**, non il cosa.
- Ogni modulo eseguibile con un `argparse`, raggiungibile sia come
  `python -m goalmodel.<percorso>` sia come sottocomando di `goalmodel`.
- Parquet per tutti i dati intermedi.
- `logging`, mai `print` (tranne l'impaginazione a terminale dei comandi).
- Niente notebook nel codice di produzione.
- DRY e SOLID: vedi la sezione "Principi di progettazione" di `CLAUDE.md`.

### Ambiente

Due piattaforme, nessuna delle due privilegiata: **Linux** e **Windows con
PowerShell**. Il codice deve funzionare su entrambe e la CI le prova tutte e
due.

- Niente comandi di shell dentro i moduli: `pathlib` e `shutil`.
- Niente percorsi come stringa: sempre `Path`.
- Su PowerShell, per cancellare file `Remove-Item ... -ErrorAction
  SilentlyContinue`, non `rm -f`; evitare `python -c "..."` con apici annidati.

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
  priorita' in [docs/AUDIT_TECNICO.md](docs/AUDIT_TECNICO.md), con lo stato di
  ciascuno.

## Licenza

**GNU AGPL-3.0-or-later** — vedi [LICENSE](LICENSE).

La clausola che distingue la AGPL dalla GPL: chi fa girare una versione
modificata di questo programma **come servizio accessibile in rete** deve
offrirne il sorgente a chi lo usa. E' il motivo della scelta — il progetto
potrebbe diventare un servizio, e la stessa condizione vale per chiunque altro
voglia farlo.

**La licenza copre il codice, non i dati.** Risultati, quote, xG e calendari
arrivano da fonti terze (football-data.co.uk, Understat, FBref, WhoScored) con
i loro termini d'uso, che questa licenza non estende e non puo' estendere. Lo
stesso vale per `track_record/`, che contiene previsioni prodotte da questo
progetto e non dati di terzi.
