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
| A3 KEYS duplicata | **chiuso** | `config.JOIN_KEYS`, una sola definizione |
| A6 CI, lint, runner test | **chiuso** | `.github/workflows/ci.yml`, ruff, pytest |
| A7 accesso ai dati sparso | **chiuso** | `data.py` + `features/registry.py` |
| A8 check.py alla radice | **chiuso** | `scripts/ispeziona_dataset.py` |
| **B2 parquet giocatori orfano** | **chiuso** | `features/registry.py`, un elenco solo |
| **B1 sezione 3 del report morta** | **chiuso** | M4 si addestra su cio' che esiste al momento di predire |
| **B3 assert come invariante** | **chiuso** | `raise` al posto di `assert`, `S101` attivo in CI |
| **A4 report.py, 4 mestieri** | **chiuso** | `sezioni.py` calcola, `pagina.py` impagina, `report.py` orchestra |
| **A5 doppio registro feature** | **chiuso** | `gbm.FUORI_DAL_MODELLO = sets.fuori_dal_modello()` |
| licenza assente | **chiuso** | `LICENSE`, AGPL-3.0-or-later |
| B4-B12 | aperti | igiene, diff piccoli |
| B13, B14 | aperti | trovati da ruff, vedi sotto |

### Come e' stato chiuso B2, e perche' non puo' tornare

Non aggiungendo `players.build` a `ricostruisci()` — quello avrebbe corretto
l'istanza lasciando in piedi la causa, cioe' DUE elenchi scritti a mano che
nessuno teneva allineati. L'elenco ora e' uno, in `features/registry.py`, e
lo scorrono sia chi carica sia chi ricostruisce.

`tests/test_registry.py` lo verifica da fuori, eseguendo davvero
`ricostruisci` con i costruttori sostituiti da registratori. **E' stato
verificato che il test scatta**: reintroducendo il difetto (`BLOCCHI[:-1]`)
fallisce nominando il blocco perduto.

### Una nota sul golden test, che vale piu' della correzione

Alla prima esecuzione con dati veri, `test_produzione_invariata` e' fallito
con scarti di 4.4e-16: uno o due ULP. La diagnosi ha richiesto tre verifiche —
i passi 1-3 del test passavano bit a bit (quindi i DATI erano identici), il
codice numerico di `market.py` e' risultato byte per byte uguale a prima del
riordino, e la dimensione dell'array e' stata esclusa per esperimento (le
stesse 4580 partite dentro un array da 4610 danno scarto 0.000e+00).

Restava la versione delle librerie, ed e' **esattamente cio' che la voce A2
prevedeva**. Il riferimento e' stato rigenerato contro l'ambiente ora bloccato
da `requirements.lock`, e `--sensibilita` conferma che la rete scatta ancora
al singolo bit.

Vale la pena registrarlo perche' e' la dimostrazione pratica che A2 non era
burocrazia: senza il lock, quel fallimento sarebbe stato indistinguibile da
una regressione vera.

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

### A4 — `report.py` fa quattro mestieri in 1309 righe — CHIUSO

Contiene: calcolo delle selezioni, addestramento di M4, lettura del track
record, analisi di potenza, generazione SVG a mano, generazione HTML, e il CSS
come stringa. Violazione netta della responsabilita' singola.

Conseguenza concreta: `report.divergenza()` **addestra un modello** dentro il
modulo di presentazione. Un errore di feature li' dentro si manifesta come
"sezione mancante nel report", non come errore di modellazione.

**Come e' stato chiuso.** Tre file al posto di uno, divisi esattamente su
quel taglio:

| file | righe | cosa fa |
|---|---|---|
| `reporting/sezioni.py` | 513 | calcola: selezioni, cambiamenti, divergenza, track record, fabbisogno. Restituisce DataFrame |
| `reporting/pagina.py` | 756 | impagina: CSS, SVG, tabelle, la pagina intera. Restituisce stringhe |
| `reporting/report.py` | 155 | regia: chiama le sezioni, le fa impaginare, scrive i due file |

La dipendenza va in una direzione sola — `report` -> `pagina` -> `sezioni` —
e la regola e' verificabile a occhio: una funzione che restituisce una stringa
con dentro un tag sta nel file sbagliato, una che legge un file o addestra un
modello pure.

`quando`, `selezioni`, `SCRITTO`, `DRY_RUN` e `RIGENERATO` restano importabili
da `report.py`: `predict_round` e `close_round` li chiedono a quel modulo e non
c'era ragione di toccare due file di produzione per un rinominamento.

### A5 — Due registri di feature in parallelo — CHIUSO

Cosa entra nel modello lo decidono **due meccanismi diversi**:

- produzione: le costanti `BLOCCHI_SCARTATI` / `BLOCCHI_NON_MISURATI` in
  `models/gbm.py`;
- esperimenti: il registro dichiarativo in `features/sets.py`.

`features/sets.py` lo documenta e spiega perche' non ha convertito la
produzione. Ma il suo stesso docstring dice che il doppio meccanismo **ha gia'
prodotto un effetto non voluto**: quando il blocco giocatori e' stato ammesso,
anche l'M4 del report ha iniziato a vedere quelle colonne senza che nessuno lo
decidesse. E' la causa prima di B1 qui sotto.

**Come e' stato chiuso.** I due `BLOCCHI_*` scritti a mano in `gbm.py` sono
spariti: `FUORI_DAL_MODELLO` e' `sets.fuori_dal_modello()`, che restituisce le
colonne dei set in stato `scartato` o `da misurare`. Lo **stato del set e' la
decisione**, in un posto solo, accanto alla misura che l'ha prodotta.

**Verificato prima di sostituire, non dopo.** Sulle 64 colonne candidate del
dataset vero, l'insieme derivato e quello scritto a mano coincidono
esattamente (le 12 colonne di CONTESTO). Le 48 di FORMA_VENUE entrano
nell'insieme derivato ma non sono nel dataset — `experiments/forma_venue.py`
le costruisce in memoria e non scrive nessun parquet — quindi non cambiano
niente. `test_production_unchanged` resta verde e M1 e' identico bit a bit,
ma non e' quello a dimostrarlo: M1 non usa queste colonne, il confronto
diretto sui due insiemi si'.

`tests/test_sets.py::test_lo_stato_del_set_decide_la_produzione` dichiara
GIOCATORI scartato e pretende che le sue colonne escano davvero da
`form_features`. Con l'elenco scritto a mano quel test non avrebbe potuto
esistere — ed e' esattamente il motivo per cui B1 era possibile.

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

### B1 — La sezione 3 del report sparisce quando esiste il blocco giocatori — CHIUSO

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

**Come e' stato chiuso.** Non allineando i due elenchi — non si possono
allineare: le assenze di una partita che non si e' ancora giocata non
esistono, e mai esisteranno al momento in cui la si predice. Il modello della
sezione si addestra ora sulle sole colonne presenti **anche** nelle partite in
arrivo (`escludi` calcolato dalla differenza fra i due frame). Le altre
restano fuori da entrambi i lati, quindi l'insieme di feature e' lo stesso per
costruzione e il guard-rail non ha piu' niente da intercettare.

**Perche' non l'opposto.** L'alternativa era far costruire a
`predict.build_features()` anche il blocco giocatori: per una riga futura
uscirebbe tutta NaN, e LightGBM manderebbe ogni partita in arrivo sul ramo dei
mancanti proprio sulla feature piu' importante del blocco
(`home_quota_minuti_assenti`, prima su 61). Sezione prodotta, previsione
distorta, nessun errore: peggio della sezione mancante.

`tests/test_divergenza.py` costruisce lo squilibrio a mano — una colonna nello
storico e non nelle partite in arrivo — e fallisce se qualcuno rimette il
modello a scegliere le feature dal solo dataset.

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

### B3 — Invarianti di produzione affidate ad `assert` — CHIUSO

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

**Come e' stato chiuso.** `if not cond: raise ValueError(...)` in tutti e
cinque i punti di `predict.py`, nei sei di `market.validate()`, nei tre di
`features/context.py` (riposo, congestione, ordinamento delle date), nei due
di `evaluation/evaluate.py` che garantiscono l'assenza di leakage a ogni
blocco del walk-forward, nei due di modulo di `features/sets.py` e nei due di
`experiments/` che verificano che i semi si possano mediare. In `market.py`
passano da `_esigi()`, che e' un `if/raise` con un nome: sei controlli di
seguito nella stessa funzione sono l'unico posto dove ripetere la forma
costava piu' che nominarla.

**Il vincolo e' ora in CI, non nella disciplina.** `S101` e' uscito dalla
lista del cricchetto in `pyproject.toml`: da adesso `ruff` rifiuta qualsiasi
`assert` nel pacchetto. Le due eccezioni dichiarate sono `baseline._demo()` e
`dixon_coles._check_gradient()`, funzioni che non girano in produzione e in
cui l'assert **e'** il controllo, non una difesa attorno a qualcos'altro.

**E che scattino davvero lo verifica un test.** `ruff` vede la forma, non il
comportamento: `tests/test_invarianti_ottimizzate.py` lancia un sottoprocesso
con `-O` e pretende che tre difese di tre moduli diversi sollevino ancora.
Serve un processo separato perche' il flag si decide all'avvio. Con gli assert
al loro posto lo stesso scenario passa in silenzio — provato.

### B4 — `except Exception` largo in due punti caldi — CHIUSO

- `rounds.passo()` (`rounds.py:97`) con `fatale=False` cattura **qualsiasi**
  eccezione e la riporta come "non e' fatale: si prosegue". Il commento parla
  di 503 da football-data, ma la stessa rete cattura un `TypeError` o un
  `KeyError` in `ingest_fixtures` e lo travesta da problema di rete.
- `report.divergenza()` (`report.py:347`) fa lo stesso per far uscire comunque
  il report. Qui e' difendibile, ma nasconde anche gli errori di
  programmazione.

**Come e' stato chiuso.** I due punti hanno risposte diverse, perche' sono
problemi diversi.

`rounds.passo()` assorbe ora solo `ERRORI_DI_RETE = (ConnectionError,
TimeoutError, OSError)`. Qualsiasi altra eccezione risale **anche con
`fatale=False`**, con un messaggio che dice che non e' la rete: "si prosegue
con i dati gia' presenti" su un `TypeError` e' una frase falsa, e il comando
andrebbe avanti su una premessa sbagliata. `OSError` e' la radice di
`socket.timeout`, `URLError` e `ConnectionError` stessa, quindi l'elenco e'
largo quanto la rete e non di piu'; l'`HTTPError` di `_leggi_csv` arriva gia'
come `ConnectionError`.

`sezioni.divergenza()` resta **largo di proposito**: il report deve uscire
comunque, e una sezione diagnostica mancante e' meglio di nessun report. Il
prezzo era che un errore di programmazione li' dentro somigliava a un problema
di dati; ora `log.exception` mette il traceback nel log e l'avviso del report
nomina il tipo dell'eccezione, cosi' la diagnosi non richiede di indovinare.

`tests/test_rounds.py::test_un_passo_di_rete_assorbe_solo_la_rete` prova
entrambi i versi: tre errori di rete assorbiti, tre bug che risalgono.

### B5 — Confine di fiducia sul CSV remoto — CHIUSO

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

**Come e' stato chiuso.** Tre controlli prima che `read_csv` veda i byte, piu'
il controllo colonne che c'era gia':

  - **schema**: gia' verificato (il ramo locale intercetta tutto cio' che non
    e' http/https). Conta perche' `urlopen` apre anche `file://`, quindi senza
    quel ramo un percorso che arriva da configurazione verrebbe letto come URL;
  - **dimensione**: `resp.read(MAX_BYTES_CSV + 1)` con tetto a 20 MB, tre
    ordini di grandezza sopra il CSV piu' grosso atteso. Il `+ 1` serve a
    distinguere "grande quanto il tetto" da "troncato al tetto";
  - **content-type**: dev'essere testo. E' il controllo che mancava di piu',
    perche' `latin-1` non fallisce **mai** sulla decodifica e una pagina di
    errore HTML entrerebbe come dati validi.

`tests/test_confine_rete.py` finge il server e verifica che una pagina HTML e
una risposta oltre il tetto vengano fermate, che un CSV vero passi, e che un
percorso locale non tocchi `urlopen`. `S310` e' uscito dal cricchetto.

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

### B7 — `_esc()` incompleta e non applicata ovunque — CHIUSO

`report.py:105` escapa `&`, `<`, `>`; non escapa `"` ne' `'`. Le
interpolazioni in attributo non ci passano mai, quindi oggi non c'e' un
percorso sfruttabile, ma:

`report.py:894` interpola `{book}` **senza escape**. Il valore viene da
`odds_source`, derivata da nomi di colonna di un CSV di terzi.

Il report e' un file locale aperto col doppio clic, quindi l'impatto e' basso.
Resta una funzione di escaping incompleta usata a macchia di leopardo, che e'
la premessa standard di una XSS il giorno in cui il report viene servito.

**Come e' stato chiuso.** `_esc` e' `html.escape(s, quote=True)`, e le due
interpolazioni che non ci passavano — `{squadra}` e `{book}` — ora ci passano.
`{book}` era l'unica alimentata da dati di terzi: viene da `odds_source`, che
deriva dai nomi di colonna di un CSV scaricato.

Il resto delle interpolazioni nude e' stato riguardato una per una: sono
frammenti HTML costruiti dal modulo stesso (celle, grafici SVG) e interi
calcolati in loco. Escaparli romperebbe la pagina. Gli avvisi della
diagnostica, che invece possono contenere nomi di colonna e di squadra,
passavano gia' da `_esc`.

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

### B13 — `zip()` senza `strict`: tronca in silenzio — CHIUSO

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

**Come e' stato chiuso.** `strict=True` su tutte e diciannove, e `B905`
tolto dal cricchetto: da adesso un `zip` senza `strict` non passa la CI.

**Una delle diciannove non doveva averlo, e si e' vista subito.**
`zip(n, n[1:])` in `tests/test_leakage.py` e' l'idioma a coppie, dove la
seconda sequenza e' piu' corta di proposito: `strict=True` la faceva
fallire al primo lancio. Sostituita con `itertools.pairwise`, che e' la forma
giusta e dice da sola cosa intende. Le altre diciotto erano tutte, come
previsto, sequenze di pari lunghezza per costruzione.

### B14 — Un controllo che non controlla, dentro la rete di sicurezza — CHIUSO

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

**Come e' stato chiuso.** Decise una per una, come chiedeva la voce.

`mancanti` era un controllo vero, e ora e' il passo **2b** di `verifica()`.
Il motivo per cui serve e' preciso: `_uguali` mette NaN e NaN d'accordo,
quindi un de-vigging che smette di produrre un valore dove il riferimento
ce l'aveva **passa i confronti sotto come identico**. Non cambia i numeri,
li fa sparire, ed e' l'unico buco della rete che gli altri sei passi non
coprono. Verificato che scatta: annullando due `mkt_lambda_home` il passo 2b
fallisce e `verifica()` torna 1.

`keys` in `power_analysis.py` e `q` in `players.py` erano residui — il codice
sotto usa altre variabili — e sono stati cancellati.

`F841` e' uscito dal cricchetto.

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
