# Audit tecnico — architettura, comportamento, pratiche di codice

Rilevato il 22 settembre 2026 su `main` (9fec3a9), 46 file Python, 13.906 righe.
**Solo diagnosi: nessuna correzione applicata.** Le correzioni si pianificano
dopo, sui tre assi dichiarati (codice/architettura, algoritmo ML, dati).

Ogni voce ha: cosa, dove, perche' e' un problema, e cosa succede se resta.
Le voci verificate leggendo entrambi i lati della chiamata sono marcate
**[verificato]**; quelle che sono un giudizio di progettazione **[giudizio]**.

---

## Riassunto esecutivo

Il progetto e' **insolitamente disciplinato** sul metodo statistico: protocollo
di valutazione congelato prima delle feature, confronti appaiati con bootstrap
a cluster, ipotesi pre-registrate, guardia a runtime contro le scritture in
produzione, test mirati ai difetti che non sollevano eccezioni. Questa parte
non e' da riparare.

I problemi stanno altrove: **l'impalcatura**. Non c'e' packaging, non c'e'
pinning delle dipendenze, non c'e' CI, non c'e' un runner dei test. Il codice
di produzione dipende da uno script alla radice. Un pezzo di stato condiviso
(`features_players.parquet`) e' entrato nel modello senza entrare nella
pipeline che lo ricostruisce, e questo ha gia' rotto una sezione del report.

| severita' | n. | temi |
|---|---|---|
| alta | 5 | parquet giocatori orfano, report rotto, asserts strippabili, zero pinning, root/package invertiti |
| media | 11 | DRY su KEYS, SRP su report.py, doppio registro feature, no CI, trust boundary rete, guardia parziale, except larghi, file handle, escape HTML, zip non stretto, controllo inerte |
| bassa | 6 | dead code, iterrows, perf ewma, check.py, no licenza, log CSV riletto |

Le ultime due voci di severita' media (B13, B14) sono emerse da `ruff` dopo la
diagnosi a mano, ed e' un dato in se': uno strumento automatico ha trovato in
un secondo due difetti che la lettura non aveva visto, in un repository dove
finora nessun linter era mai girato.

---

## Stato delle correzioni

Aggiornato al 22 settembre 2026, branch `refactor/struttura-enterprise`.
**La diagnosi sotto resta scritta al presente storico**: descrive il codice
com'era quando e' stata fatta, e i riferimenti a `src/rounds.py` o
`src/predict.py` vanno letti con il layout di allora.

| voce | stato | dove |
|---|---|---|
| A1 ingest alla radice | **chiuso** | `src/goalmodel/ingest.py` + `cli.py` |
| A2 packaging e pinning | **chiuso** | `pyproject.toml`, `requirements.lock` |
| A6 CI, lint, runner test | **chiuso** | `.github/workflows/ci.yml`, ruff, pytest |
| A8 check.py alla radice | **chiuso** | `scripts/ispeziona_dataset.py` |
| A3 KEYS duplicata | aperto | prossima fase |
| A4 report.py, 4 mestieri | aperto | prossima fase |
| A5 doppio registro feature | aperto | prossima fase |
| A7 accesso ai dati sparso | aperto | prossima fase |
| B1 sezione 3 del report morta | aperto | prossima fase, per prima |
| B2 parquet giocatori orfano | aperto | prossima fase, per prima |
| B3 assert come invariante | aperto | prossima fase |
| B4-B12 | aperti | prossima fase |
| B13, B14 | **nuovi**, vedi sotto | trovati da ruff |
| licenza assente | aperto | **decisione del proprietario**, non del refactoring |

Quello che il riordino ha cambiato e' l'impalcatura, non il comportamento:
nessuna correzione di logica e' stata applicata, e la suite e' passata da
"nessun runner" a 36 passati / 7 saltati su due piattaforme.

---

## A. Architettura

### A1 — `ingest.py` sta alla radice e la produzione lo importa — ALTA [verificato]

`src/rounds.py:131` fa `import ingest`, cioe' un pacchetto importa uno script
della cartella di lavoro. Funziona solo perche' i comandi si lanciano da li'.

- Il pacchetto `src/` non e' installabile ne' importabile da altrove.
- Un `cd` diverso rompe `predict_round` con `ModuleNotFoundError`.
- Impedisce qualsiasi deployment che non sia "clona e lancia dalla radice".

**Da fare:** spostare in `src/ingest.py`, lasciare alla radice al massimo un
wrapper di tre righe.

### A2 — Nessun packaging, dipendenze non fissate — ALTA [verificato]

Non esistono `pyproject.toml`, `setup.py`, lockfile. `requirements.txt` usa
solo `>=`: `lightgbm>=4.0`, `scipy>=1.11`, `pandas>=2.0`.

**Contraddizione diretta con il metodo del progetto.**
`tests/test_production_unchanged.py` verifica che M1 e i λ di mercato restino
identici **bit a bit**, e `experiments/modelli.py` ricostruisce la media dei
semi bit a bit. Entrambi dipendono da versioni esatte di LightGBM, scipy e
numpy, che nessuno fissa. Un `pip install -r requirements.txt` fra sei mesi
installa altre versioni e il golden test fallisce senza che il codice sia
cambiato — e non ci sara' modo di distinguere una regressione vera da un
aggiornamento di libreria.

**Da fare:** `pyproject.toml` + lockfile (`uv.lock` o `requirements.lock`),
versioni esatte per almeno numpy/scipy/pandas/lightgbm.

### A3 — `KEYS` duplicata in 14 file — MEDIA [verificato]

`["league","season","home_team","away_team"]` e' riscritta in
`predict.py:70`, `evaluate.py:67`, `rounds.py:51`, `report.py:59`,
`market.py:73`, `form.py:62`, `context.py:65`, `players.py:72`,
`backtest_log.py:55`, piu' `normalize.py:48` con un nome diverso
(`JOIN_KEYS`), piu' 3 test.

E' la **chiave di join del progetto**, cioe' la regola non negoziabile n.5.
Aggiungere la lega come dimensione (i Big 5 sono nel piano) significa
modificare 14 posti e sperare di non dimenticarne uno; dimenticarne uno non
da' errore, da' un merge che perde righe in silenzio.

**Da fare:** `config.JOIN_KEYS`, importata ovunque. `close_round.py:57` gia' fa
la cosa giusta (`KEYS = rounds.KEYS`).

### A4 — `report.py` fa quattro mestieri in 1309 righe — MEDIA [giudizio]

Contiene: calcolo delle selezioni, addestramento di M4, lettura del track
record, analisi di potenza, generazione SVG a mano, generazione HTML, e il CSS
come stringa. Violazione netta della responsabilita' singola.

Conseguenza concreta: `report.divergenza()` **addestra un modello** dentro il
modulo di presentazione. Un errore di feature li' dentro si manifesta come
"sezione mancante nel report", non come errore di modellazione.

**Da fare:** separare `report_data.py` (tutto cio' che restituisce DataFrame)
da `report_html.py` (tutto cio' che restituisce stringhe).

### A5 — Due registri di feature in parallelo — MEDIA [verificato]

Cosa entra nel modello lo decidono **due meccanismi diversi**:

- produzione: le costanti `BLOCCHI_SCARTATI` / `BLOCCHI_NON_MISURATI` in
  `models/gbm.py`;
- esperimenti: il registro dichiarativo in `features/sets.py`.

`features/sets.py` lo documenta e spiega perche' non ha convertito la
produzione. Ma il suo stesso docstring dice che il doppio meccanismo **ha gia'
prodotto un effetto non voluto**: quando il blocco giocatori e' stato ammesso,
anche l'M4 del report ha iniziato a vedere quelle colonne senza che nessuno lo
decidesse. E' la causa prima di B1 qui sotto.

**Da fare:** convertire `report.py` e `gbm.py` ai set dichiarati. E' un
cambiamento di produzione: va fatto con `test_production_unchanged` a fianco.

### A6 — Nessuna CI, nessun linter, nessun runner dei test — MEDIA [verificato]

14 file di test, tutti `if __name__ == "__main__"`, zero `import pytest`,
`pytest` non e' in `requirements.txt`. Non esiste un comando che li lanci
tutti: `COMANDI.md` ne elenca cinque a mano. Nessun `.github/workflows`,
nessun `ruff`/`black`/`flake8`.

I test **esistono e sono buoni**; non c'e' niente che garantisca che vengano
lanciati.

**Da fare:** rendere i test raccoglibili da pytest (basta rinominare le
funzioni interne in `test_*`, i `main()` possono restare), aggiungere `pytest`
alle dipendenze, una GitHub Action che lancia i test senza rete.

### A7 — Accesso ai dati senza un punto unico — MEDIA [verificato]

`pd.read_parquet(config.INTERIM / "matches_master.parquet")` compare in
`normalize.py`, `evaluate.py`, `predict.py` (due volte), `rounds.py`,
`ingest.py`, `check.py`. Ogni chiamante ripete percorso, gestione dell'assenza
e conversione delle date.

`predict.run()` lo legge **due volte nella stessa funzione**
(`load_played()` piu' `played_all`, righe 649-650).

**Da fare:** `data.load_master()` / `load_features()` in un modulo solo.

### A8 — `check.py` alla radice — BASSA [verificato]

Script di esplorazione, 24 righe, percorso relativo hardcoded
(`"data/interim/..."` invece di `config.INTERIM`), nessun argparse, stampa con
`print`. Viola tre regole di stile del progetto ed e' versionato alla radice
accanto a `ingest.py`, dove sembra un entry point.

**Da fare:** spostarlo in `notebooks/` o cancellarlo.

---

## B. Comportamento e correttezza

### B1 — La sezione 3 del report sparisce quando esiste il blocco giocatori — ALTA [verificato]

Percorso completo:

1. `report.divergenza()` (`report.py:322`) addestra M4 su
   `evaluate.load_dataset()`.
2. `load_dataset()` (`evaluate.py:124-134`) unisce **anche**
   `features_players.parquet`, se il file esiste.
3. `gbm.form_features()` prende tutte le colonne numeriche non escluse, e
   `BLOCCHI_NON_MISURATI` e' **vuoto** (`gbm.py:139`): le colonne giocatori
   entrano fra le feature del modello.
4. Il report predice su `preds`, che arriva da
   `predict.build_features()` (`predict.py:308`), la quale costruisce **solo**
   form, market e context — **mai players**.
5. `report.py:341` trova le feature mancanti e salta la sezione con un avviso.

**Effetto:** la sezione "divergenza modello/mercato" non viene piu' prodotta,
e il motivo appare come una riga di diagnostica e non come un errore. Chi
genera il report vede una sezione in meno e nessuna spiegazione ovvia.

Non e' silenzioso-sbagliato — il guard-rail funziona — ma e' una funzionalita'
persa per accoppiamento implicito.

### B2 — `features_players.parquet` non viene mai ricostruito — ALTA [verificato]

`rounds.ricostruisci()` (`rounds.py:146-165`) rifa' `matches_master`, form,
market e context. **Players no.** `grep` conferma che `players` non compare in
`rounds.py`, `predict.py`, `predict_round.py`, `close_round.py`.

Quindi, a ogni giornata: `matches_master` cresce, le altre feature si
riallineano, e `features_players.parquet` resta congelato all'ultima
costruzione manuale — mentre `load_dataset()` continua a unirlo e a darlo in
pasto ai modelli.

**Effetto:** ogni misura o riaddestramento fatto dopo qualche giornata usa un
blocco giocatori vecchio senza che niente lo segnali. Il merge e'
`validate="one_to_one"` e `how="left"`, quindi le righe nuove ricevono NaN:
LightGBM li tratta nativamente e non protesta.

**Da fare:** o aggiungere `players.build` a `ricostruisci()`, o dichiarare
esplicitamente che il blocco e' fuori dal ciclo (e allora toglierlo da
`load_dataset` di default).

### B3 — Invarianti di produzione affidate ad `assert` — ALTA [verificato]

`src/predict.py` usa `assert` per cinque controlli, fra cui i due che il
progetto stesso chiama non negoziabili:

- `predict.py:663` — il timestamp deve precedere il calcio d'inizio;
- `predict.py:302` — nessuna colonna post-partita sulle righe future;
- `predict.py:323` — nessuna partita gia' nello storico fra quelle da predire;
- `predict.py:528` — il registro non si e' accorciato.

`python -O` **rimuove tutti gli assert**. Lanciare la produzione con `-O`
(o `PYTHONOPTIMIZE=1`, che puo' arrivare dall'ambiente) toglierebbe
esattamente le difese che il progetto dichiara indispensabili, e la prima ad
andarsene sarebbe il controllo sul fischio d'inizio — che, come documentato in
`CLAUDE.md`, ha **gia' fallito una volta** per un errore di fuso.

Stesso schema in `features/market.py:355-378` (`validate()`), dove l'assert e'
l'unica cosa che ferma un de-vigging rotto.

**Da fare:** `if not cond: raise ValueError(...)` per tutto cio' che protegge
il registro o il de-vigging. Gli assert nei `_demo()` vanno benissimo dove
sono.

### B4 — `except Exception` largo in due punti caldi — MEDIA [verificato]

- `rounds.passo()` (`rounds.py:97`) con `fatale=False` cattura **qualsiasi**
  eccezione e la riporta come "non e' fatale: si prosegue". Il commento parla
  di 503 da football-data, ma la stessa rete cattura un `TypeError` o un
  `KeyError` in `ingest_fixtures` e lo travesta da problema di rete.
- `report.divergenza()` (`report.py:347`) fa lo stesso per far uscire comunque
  il report. Qui e' difendibile, ma nasconde anche gli errori di
  programmazione.

**Da fare:** restringere a `(ConnectionError, HTTPError, TimeoutError, OSError)`
nel passo di rete; nel report, loggare il traceback nella diagnostica.

### B5 — Confine di fiducia sul CSV remoto — MEDIA [verificato]

`ingest._leggi_csv()` (`ingest.py:103-138`) scarica via `urllib` con
User-Agent falsificato, legge l'intero corpo in memoria e lo passa a
`pd.read_csv` con `encoding="latin-1"`.

Cosa manca:
- nessun tetto alla dimensione della risposta (`resp.read()` senza limite);
- nessun controllo di `Content-Type`;
- `latin-1` non fallisce mai sulla decodifica, quindi un HTML di errore o una
  pagina di captcha viene letto come dati validi fino al controllo colonne.

C'e' un controllo delle colonne attese subito dopo, ed e' la difesa giusta;
manca tutto quello che viene prima.

**Da fare:** `resp.read(MAX_BYTES)`, verifica del content-type, e il controllo
colonne com'e'.

### B6 — La guardia degli esperimenti copre meno di quanto sembri — MEDIA [verificato]

`experiments.proteggi_produzione()` sostituisce `DataFrame.to_parquet` e
`DataFrame.to_csv`. Non copre: `to_feather`, `to_pickle`, `to_hdf`,
`to_excel`, `np.save`, `Path.write_text/write_bytes`, `open(..., "w")`,
`pyarrow.parquet.write_table`, `shutil.copy` — quest'ultima usata proprio da
`predict.append_log`.

L'idea e' giusta e il docstring e' onesto sul perche' esiste. Il rischio e'
che si legga come "gli esperimenti non possono scrivere in produzione",
mentre e' "non possono scrivere in produzione **con due metodi pandas**".

**Da fare:** o estendere l'elenco, o dichiarare nel docstring esattamente cosa
copre. La seconda costa una riga.

### B7 — `_esc()` incompleta e non applicata ovunque — MEDIA [verificato]

`report.py:105` escapa `&`, `<`, `>`; non escapa `"` ne' `'`. Le
interpolazioni in attributo non ci passano mai, quindi oggi non c'e' un
percorso sfruttabile, ma:

`report.py:894` interpola `{book}` **senza escape**. Il valore viene da
`odds_source`, derivata da nomi di colonna di un CSV di terzi.

Il report e' un file locale aperto col doppio clic, quindi l'impatto e' basso.
Resta una funzione di escaping incompleta usata a macchia di leopardo, che e'
la premessa standard di una XSS il giorno in cui il report viene servito.

**Da fare:** usare `html.escape(s, quote=True)` della stdlib, e applicarla a
tutte le interpolazioni di valori.

### B8 — File handle senza context manager — MEDIA [verificato]

`experiments/blocco_b_robustezza.py:94` e `experiments/forma_venue.py:158`
aprono file di log con `fh = open(...)` e li chiudono a mano in un ciclo
successivo. Un'eccezione fra apertura e chiusura li lascia aperti; con 15 run
in parallelo e' poca cosa, ma e' la classe di errore che i context manager
esistono per eliminare.

### B9 — `gbm.tune()`: variabile morta e conteggio fuorviante — BASSA [verificato]

`gbm.py:574`: `configs = sample_configs(n_configs, seed=seed)` viene calcolata
dopo che `jobs` e' gia' costruito, e serve solo a `len(configs)` nel log della
riga 576. Ignora lo spazio esteso della variante ancorata: quel log dice
"24 configurazioni x 3 varianti" anche se lo spazio ancorato ne avesse un
numero diverso.

**Da fare:** cancellare la riga, loggare `len(jobs)`.

### B10 — Estrazione delle quote riga per riga con fallback silenzioso — BASSA [verificato]

`predict.predict_fixtures` (`predict.py:427-437`) costruisce le tre colonne di
quota con `out.iterrows()` e nomi di colonna composti a stringa
(`f"{r['mkt_1x2_source']}{suffisso}"`), con `np.nan` se la colonna non c'e'.

Se football-data rinominasse una colonna, la quota registrata diventerebbe
`NaN` **senza alcun errore**, e ci si accorgerebbe del problema mesi dopo,
rileggendo il track record — che e' proprio l'uso per cui quelle quote si
salvano.

**Da fare:** vettorializzare con un `lookup`, e loggare quante righe hanno
perso la quota.

### B11 — `form.ewma_by_team` e' un ciclo `iloc` per riga — BASSA [verificato]

`form.py:170` fa `row = long.iloc[pos]` dentro il ciclo interno: una Series
nuova per ognuna delle ~9.000 righe, poi accesso per etichetta a 16 colonne.

Il docstring dice "meno di un secondo", ed e' plausibile. Ma la funzione gira
**a ogni previsione**, perche' `predict.build_features()` ricostruisce le
feature da zero su storico + partite future. Estrarre le colonne in array
numpy prima del ciclo darebbe lo stesso controllo esatto con un ordine di
grandezza in meno di lavoro. Da fare solo se il tempo di `predict_round`
diventa un fastidio.

### B12 — Il registro viene riletto per intero a ogni controllo — BASSA [verificato]

`predict.already_logged()` legge tutto `predictions_log.csv` a ogni chiamata
(tre volte per `predict_round`), e `append_log` conta le righe del file due
volte con un ciclo Python. Il file oggi pesa 3 KB: e' irrilevante, e
riscriverlo ora sarebbe ottimizzazione prematura. Annotato perche' la
struttura e' append-only per sempre e quel file non verra' mai potato.

### B13 — `zip()` senza `strict`: tronca in silenzio — MEDIA [verificato]

Diciannove occorrenze, trovate da `ruff` (regola `B905`) e non dall'ispezione
a mano. `zip(a, b)` senza `strict=True` si ferma alla sequenza piu' corta
**senza dire niente**.

Dove morde davvero: in `models/gbm.py` si zippano colonne di lambda di mercato
con nomi di colonna di gol, e in `evaluation/` si zippano modelli con le loro
previsioni. Se una delle due sequenze perdesse un elemento — una colonna
rinominata, un modello che non predice — il risultato sarebbe piu' corto e
nessuno lo saprebbe.

E' esattamente la classe di difetto che il progetto teme di piu': non solleva,
produce numeri plausibili. Che nessuna delle diciannove sia oggi sbagliata non
toglie che nessuna sia protetta.

**Da fare:** `strict=True` ovunque le due sequenze debbano avere la stessa
lunghezza per costruzione, che e' il caso in tutti e diciannove.

### B14 — Un controllo che non controlla, dentro la rete di sicurezza — MEDIA [verificato]

`tests/test_production_unchanged.py`, in `verifica()`:

```python
mancanti = corrente["mkt_lambda_home"].isna() & rif["mkt_lambda_home"].notna()
```

`mancanti` viene calcolata e **mai usata**. Nessun `assert`, nessun `if`,
nessuna stampa. Trovata da `ruff` (`F841`), non a occhio.

Cosa avrebbe dovuto fare: segnalare le righe dove il run corrente ha perso un
lambda di mercato che il riferimento aveva — cioe' una regressione del
de-vigging che non cambia i valori ma li fa sparire. E' un controllo sensato,
scritto e lasciato scollegato.

**Perche' pesa piu' del solito.** Questo file e' la rete di sicurezza della
produzione: e' il test che si lancia prima di ogni commit e che autorizza a
dire "M1 non e' cambiato". Un controllo silenziosamente inerte qui e' lo stesso
difetto che il progetto insegue nel modello — qualcosa che sembra misurare e
non misura.

Altre due `F841` (`keys` in `evaluation/power_analysis.py`, `q` in
`features/players.py`) vanno guardate con la stessa domanda: era un controllo
mai collegato, o solo un residuo?

**Da fare:** decidere, per ciascuna, se collegare il controllo o cancellare la
riga. Non lasciarle come sono.

---

## C. Pratiche di codice

### C1 — DRY

- `KEYS` in 14 posti (A3).
- `pd.read_parquet(matches_master)` in 6 posti (A7).
- Il blocco di `cluster` per il bootstrap e' ricopiato identico in
  `evaluate.paired_comparison` e `evaluate.paired_pairs`
  (`evaluate.py:436-441` e `470-475`).
- `logging.basicConfig(level=..., format=...)` ripetuto in **ogni** modulo: e'
  una configurazione globale, chiamarla in 20 punti significa che vince chi
  importa per primo.
- Il pattern `drop(columns=[c for c in X.columns if c == "date"])` compare
  quattro volte in `evaluate.load_dataset` e `predict.build_features`.

### C2 — SOLID

| principio | stato |
|---|---|
| **S**ingle responsibility | **violato** in `report.py` (A4) e in parte in `predict.py`, che fa calendario + quote + feature + previsione + registro + stampa |
| **O**pen/closed | **rispettato bene**: `MarketAnchoredGBM` estende `PoissonGBM`, `M5Set` estende ancora, senza modificare nulla a monte |
| **L**iskov | **rispettato**: ogni `Model` e' sostituibile, `walk_forward` non sa chi sta valutando |
| **I**nterface segregation | **rispettato**: l'interfaccia e' `fit`/`predict`, due metodi |
| **D**ependency inversion | **violato in parte**: i moduli dipendono da `pd.read_parquet` su percorsi concreti invece che da un'astrazione di caricamento (A7); `rounds` dipende da `ingest`, che e' un dettaglio di infrastruttura (A1) |

Il punto centrale: le **astrazioni dei modelli sono ottime**, le **astrazioni
dei dati non esistono**.

### C3 — Sicurezza

Verificato: **nessun segreto, nessuna credenziale, nessun token** nel
repository. Nessun `eval`, `exec`, `pickle`, `os.system`, `shell=True`. Le
chiamate `subprocess.Popen` usano la forma a lista con `sys.executable` e
argomenti da costanti interne: corrette.

Aperti: B5 (confine di fiducia sul CSV remoto), B7 (escaping HTML), B6
(guardia parziale), e l'assenza di un controllo d'integrita' su qualsiasi
file scaricato.

Fuori dal codice, da sapere: lo stage `missing` guida un browser reale su
WhoScored con Selenium (~15 ore, una richiesta per partita). E' scraping con i
rischi di termini d'uso e rate limiting che comporta; il progetto lo documenta
ma non lo limita.

### C4 — Quello che e' fatto bene e non va toccato

Va scritto, perche' un audit che elenca solo i difetti fa riscrivere le cose
giuste:

- **I docstring spiegano il perche'.** Sono la parte piu' preziosa del
  repository: registrano errori gia' commessi (il fuso di due ore, il segno di
  rho, `init_score` non riaggiunto da LightGBM) in modo che non si ripetano.
- **I test puntano ai difetti che non sollevano eccezioni.** Leakage, fusi
  orari, de-vigging, idempotenza del registro. E' la scelta giusta.
- **Il ciclo di valutazione e' stato costruito prima delle feature**, di
  proposito.
- **Lo stato della giornata e' un file**, non una colonna che puo' divergere.
- **La guardia a runtime sugli esperimenti** esiste, anche se parziale (B6).
- **L'isolamento della produzione per sottoclassi** invece che per modifiche
  in loco.

---

## Ordine di intervento proposto

Ordinato per rapporto fra rischio rimosso e diff prodotto.

| # | voce | perche' prima |
|---|---|---|
| 1 | B2 + B1 | c'e' una funzionalita' rotta adesso e un parquet che invecchia in silenzio |
| 2 | B3 | difese dichiarate non negoziabili che `-O` cancella |
| 3 | A2 | senza pinning i golden test bit-a-bit non significano niente |
| 4 | A1 | sblocca packaging, CI e qualsiasi esecuzione fuori dalla radice |
| 5 | A6 | serve prima di qualsiasi refactoring: e' la rete di sicurezza |
| 6 | A3 + A7 | prerequisito per allargare ai Big 5 senza perdere righe |
| 7 | A4 + A5 | il refactoring grosso, da fare con la CI gia' verde |
| 8 | B4-B8 | igiene, diff piccoli, nessuna dipendenza fra loro |

I punti 1-5 sono prerequisiti dei tre assi di lavoro dichiarati: senza
packaging e CI non si spezza un monorepo, senza il registro delle feature
unificato non si tocca l'algoritmo, e senza un punto unico di accesso ai dati
non si riscrive la pipeline.
