# CLAUDE.md

Contesto operativo per Claude Code.

| documento | cosa contiene |
|---|---|
| `README.md` | cos'e' il progetto, installazione, architettura, contribuzione |
| `docs/PROGETTO_SERIE_A.md` | progettazione completa e razionale di fondo |
| `docs/AUDIT_TECNICO.md` | debiti architetturali e difetti noti, con stato |
| `docs/adr/` | decisioni di architettura, una per file |
| **questo file** | decisioni prese, risultati misurati, il **perche'** |
| **`docs/COMANDI.md`** | come si lancia qualsiasi cosa: il **come** |

I comandi vivono solo in `docs/COMANDI.md`. Quando ne cambia uno si aggiorna
li', non qui: due elenchi divergono e viene sempre letto quello sbagliato.

## Dove sta il codice

Il pacchetto e' **`goalmodel`**, installabile, sotto `src/goalmodel/`. Ogni
livello puo' importare **solo quelli sotto di se'**:

```
config
  schema, data             il contratto dei file e il loro unico punto di lettura
  ingest, normalize        acquisizione e unificazione delle fonti
    risultati              la catena di fonti per il risultato vero
    features/              forma, mercato, contesto, giocatori, dataset
      models/              M0..M6, dalla lambda alla matrice dei risultati
        evaluation/        RPS, calibrazione, walk-forward, potenza, taratura
          prediction/      previsione, registro, ciclo della giornata
            reporting/     sezioni (calcolo) -> pagina (HTML) -> report (regia)

experiments/               importa tutto, non e' importato da nessuno
```

Fuori dal pacchetto, e di proposito:

```
backend/                   API HTTP in sola lettura. Importa goalmodel, mai
                           il contrario. Dipendenze nell'extra `api`
frontend/                  React + Vite. Consuma web/openapi.json
```

`backend` e' un secondo pacchetto installabile, non un sottomodulo di
`goalmodel`: ha dipendenze proprie (FastAPI, uvicorn) e un ciclo di rilascio
proprio. Chi lavora al modello non deve installare un web server per lanciare
un walk-forward.

**Un import all'indietro e' una decisione di architettura, non una comodita'.**
**Al 22 settembre 2026 non ce n'e' nessuno**, e i due che c'erano sono stati
sciolti spostando il codice al livello giusto, non aggiungendo eccezioni:

- la **taratura** (iperparametri, peso della miscela, half-life) e' in
  `evaluation/taratura.py`. Tarare e' misurare, e il modello e' l'oggetto
  della misura, non chi la fa;
- l'**assemblaggio del dataset** (`load_dataset`, `add_matchday`) e' in
  `features/dataset.py`. Unire i blocchi non e' valutare.

Entrambi vivevano in `models/`, e importavano `evaluation` **dentro le
funzioni** per non creare un ciclo: un import all'indietro nascosto in una
funzione resta un import all'indietro, e' solo piu' difficile da vedere.

I comandi non sono cambiati: `goalmodel gbm --tune`, `--importance`,
`goalmodel dixon-coles --tune` e `--check` fanno quello che facevano. E'
`cli.py` a mandarli altrove, e per questo accetta anche la forma
`modulo:funzione`. Verificato: `--importance` riproduce le stesse quote di
guadagno pubblicate piu' sotto, a partire da `goals_for` al 4.7%.

I comandi passano dal CLI: `goalmodel <comando>`, che funziona da qualsiasi
directory e allo stesso modo su Linux e Windows. `python -m goalmodel.<modulo>`
resta valido. Un modulo eseguibile nuovo si registra in `src/goalmodel/cli.py`,
una riga.

Perche' un repository solo e cosa lo spezzerebbe:
`docs/adr/0001-monolite-modulare.md`.

## Obiettivo

Modello probabilistico per la previsione dei **gol** nelle partite dei Big 5
campionati europei. L'1X2 e' un **output derivato** dalla matrice dei risultati
esatti, mai un target diretto. Caso d'uso finale: filtrare le predizioni sulle
partite del Napoli in Serie A.

## Decisioni bloccate — non riaprirle senza discuterne

| Decisione | Scelta |
|---|---|
| Target | Gol casa e gol trasferta (Poisson). Nessun classificatore multiclasse |
| Orizzonte | **Solo T-24h**. Niente feature che dipendono dalle formazioni ufficiali |
| Quote bookmaker | Incluse come feature. Il modello market-only resta come benchmark |
| Training set | Big 5 dal 2014/15 (limite Understat) |
| Serie B | Solo priori per neopromosse. Non ha xG/PPDA, non e' un training set |
| Reti neurali | Escluse |
| Riaddestramento | Da zero a ogni giornata. Nessun apprendimento incrementale |

## Regole non negoziabili

1. **Nessun leakage.** Ogni feature dev'essere calcolabile con la sola
   informazione disponibile prima del calcio d'inizio. Le statistiche della
   partita stessa (tiri, xG, PPDA, cartellini) servono solo a costruire
   aggregati storici, mai come feature della stessa riga.

2. **Mai `train_test_split` casuale.** Split temporale, walk-forward per
   giornata.

3. **RPS come metrica primaria**, non accuracy. L'accuracy si riporta solo per
   comunicare, mai per selezionare modelli.

4. **Le finestre mobili non si azzerano al confine di stagione.** Ordinamento
   per squadra e per data, continuo. Al cambio stagione si applica una
   regressione verso la media di lega del 30% (`config.SEASON_REGRESSION`).

5. **Chiave di join**: `(league, season, home_team, away_team)`. Mai la data —
   in un campionato all'italiana la quadrupla e' univoca, e questo evita fusi
   orari e rinvii. La data resta come controllo di coerenza.

   **Verificata, e con un'eccezione nota (22 settembre 2026).** Su
   `matches_master` la quadrupla e' univoca: 4610 righe, zero duplicati, ora
   controllato a ogni lettura da `schema.py`. Su `fbref_schedule` **non lo e'**:
   Spezia-Hellas Verona 2022/23 compare due volte, la partita di campionato del
   5 marzo (0-0, giornata 25) e lo **spareggio salvezza** dell'11 giugno (1-3).
   Uno spareggio non e' "un campionato all'italiana", quindi la regola non lo
   copriva e nessuno se n'era accorto.

   Si riconosce perche' ha `week` nullo — non appartiene a nessuna giornata — e
   tutti i consumatori lo escludono cosi'. Due di loro non lo facevano:
   `evaluate.attach_matchday` e `backtest_log` si affidavano a
   `drop_duplicates`, che tiene la prima riga **nell'ordine del parquet**.
   Funzionavano per come il file era scritto, non per una decisione. Corretti.

6. **Coerenza sulle quote**: chiusura o apertura, ma la stessa scelta nel
   backtest e in produzione.

## Stato attuale — verificato sui dati reali (2 settembre 2026)

### Dataset costruito

`data/interim/matches_master.parquet`: **4580 righe, 222 colonne**.
Serie A, stagioni 1415-2627. Dodici stagioni complete a 380 partite piu' 20
partite della 2026/27 in corso (prime due giornate). Copertura del join con
Understat: **100%**.

`data/processed/features_form.parquet`: 4580 righe, 58 colonne.
380 righe marcate `is_burn_in` (stagione 1415, priva di media di riferimento).

### Nomi di colonna reali — usare questi, non inventarne

Da football-data.co.uk:
```
FTHG FTAG FTR HTHG HTAG HTR      risultati
HS AS HST AST HF AF HC AC        statistiche partita (POST-partita!)
HY AY HR AR                      cartellini (POST-partita!)
HxG AxG                          xG di football-data, solo stagioni recenti
```

Da Understat:
```
home_xg / away_xg
home_np_xg / away_np_xg                        xG esclusi i rigori
home_np_xg_difference / away_np_xg_difference
home_ppda / away_ppda
home_deep_completions / away_deep_completions
home_points / away_points
home_expected_points / away_expected_points
home_goals / away_goals
date_understat                                 data secondo Understat
```

Quote disponibili: `B365H/D/A`, `BWH/D/A`, `IWH/D/A`, `PSH/D/A` (Pinnacle),
`WHH/D/A`, `VCH/D/A`, piu' varianti `C` per le quote di chiusura e
`Max`/`Avg` per massimo e media di mercato.

### Fatti noti sui dati

- **Copertura quote, verificata stagione per stagione**
  (`goalmodel features-market --coverage`):
  - `B365` in apertura e' l'**unico** book con terzina 1X2 completa al 100%
    su tutte e 13 le stagioni. E' il riferimento scelto.
  - **Pinnacle e' morto**: `PS` copre il 52% della 2025/26 e lo 0% della
    2026/27. Ottimo nello storico, inutilizzabile in produzione. Vale anche
    per `VC` (finito nel 2024/25) e `IW` (crollato al 47% nel 2023/24).
  - Le colonne `Bb*` (Betbrain) non sono "morte": sono il **vecchio nome**
    degli aggregati di mercato, 100% dal 2014/15 al 2018/19 e finiti li'.
    `Max`/`Avg` prendono il loro posto dal 2019/20. Le due famiglie non si
    sovrappongono mai. I `Bb*` sono l'unica fonte over/under pre-2019/20.
  - Le quote di **chiusura** (`*C`) partono dal 2019/20 per B365.
- **La chiusura non e' utilizzabile come feature**: si forma pochi minuti
  prima del fischio d'inizio e incorpora le formazioni ufficiali, fuori
  dall'orizzonte T-24h. Il drift apertura-chiusura ha deviazione standard di
  ~3 punti di probabilita': non e' rumore, e' proprio l'informazione che a
  T-24h non si ha. Si calcola solo come diagnostica.
- **La distorsione favorito-sfavorito del mercato NON esiste nella forma
  generale.** Verificata fuori campione (`goalmodel evaluate --bias`):
  impilando i tre esiti, l'ECE del mercato vale 0.010 su tutto lo storico e
  0.022 sul test, nessun bin fuori dall'intervallo di confidenza, e la
  pendenza di calibrazione e' 1.06-1.09, cioe' *sopra* 1 — il segno opposto
  a quello della distorsione classica.
  Sopravvive invece un effetto piu' stretto e specifico: **il mercato
  sopravvaluta la squadra di casa quando NON e' favorita**. Nella meta' bassa
  di `p_home` mancano all'appello 4 punti percentuali di vittorie casalinghe,
  con z = -2.33 in training e -2.25 sul test — stesso segno e stessa
  grandezza fuori campione. Nella meta' alta l'effetto sparisce (z = +0.21 e
  -0.65), quindi non e' il fattore campo sopravvalutato in generale.
  E' al limite della significativita' ed emerso dopo aver guardato piu'
  viste: **non farne una feature**. Va trattato come residuo da spiegare col
  modello, non come segnale da codificare a mano.
- **I FUSI ORARI DELLE DUE FONTI SONO DIVERSI, e non e' un dettaglio.**
  `fbref_schedule.time` e' l'orario **locale dello stadio** (ora italiana per
  la Serie A); football-data usa l'ora del **Regno Unito**. Verificato su 2681
  partite con orario su entrambe: la differenza e' esattamente 1 ora in **ogni
  mese dell'anno**, inverno compreso — quindi non e' UTC contro UK, che
  varierebbe con l'ora legale. Convertendo con `config.LEAGUE_TIMEZONE` i due
  orari coincidono al minuto sul 99.5% delle partite.

  Perche' importa: leggere l'orario di fbref come UTC sposta il calcio
  d'inizio di **due ore in avanti** d'estate, e l'assert che dovrebbe
  impedire una previsione tardiva la lascia passare. E' successo: due righe
  del registro sono state scritte a partita gia' iniziata (Inter-Napoli +1
  minuto, Roma-Atalanta +44 minuti) e l'assert non ha protestato.
  `backtest_log.flag_post_kickoff` ora le riconosce e le esclude.
- **WhoScored risponde in ITALIANO, e questo rompe soccerdata in due punti.**
  Il sito localizza le pagine sulla geolocalizzazione di chi chiama: da un IP
  italiano la regione e' `Italia` (non `Italy`) e il link al calendario e'
  `Partite` (non `Fixtures`). Nessuno dei due errori dice la verita':
  - `KeyError: "None of [Index(['ITA-Serie A'])] are in the [index]"` sembra
    una lega non supportata. La lega c'e', si chiama in un'altra lingua.
    Rimedio: `config.WHOSCORED_LEAGUE_OVERRIDE`, che sovrascrive **solo** il
    campo WhoScored del dizionario leghe. Scrivere in
    `~/soccerdata/config/league_dict.json` NON va bene: soccerdata fa
    `dict.update` al primo livello e si porterebbe via anche le mappe di
    FBref, Understat e MatchHistory.
  - `IndexError: list index out of range` a `whoscored.py:290` e' il link
    "Fixtures" che non esiste. Rimedio: `src/goalmodel/whoscored_patch.py`, che cerca
    l'href invece del testo — gli URL restano in inglese in ogni lingua.
    **Attenzione alle maiuscole**: l'href e' `/fixtures` minuscolo e XPath 1.0
    non ha `lower-case()`, quindi serve `translate()`. Cercare `/Fixtures` non
    trova niente e sembra in tutto che la patch non funzioni.

  Il resto dello scraper usa id e classi CSS, che non sono tradotti: il
  problema e' circoscritto. Nel risultato, `reason` e' in inglese
  (`injured`, `suspended`, `injured doubtful`) perche' viene da un attributo;
  `status` e' tradotto (`Indisponibile`, `In dubbio`). **Usare `reason`**, che
  e' strutturale, non `status`, che cambia con la lingua del sito.
- **Costo misurato di WhoScored: ~11.6 secondi per partita**, una richiesta
  Selenium ciascuna, piu' ~110 secondi per il calendario di ogni stagione.
  Su 4940 partite fanno **circa 15 ore**. La cache e' persistente e il lavoro
  e' riprendibile. Copertura verificata anche sulle stagioni vecchie: 1516 e
  1920 rispondono entrambe.
- **football-data pubblica l'ORARIO solo dal 2019/20.** Prima mette la stessa
  ora finta su tutte e 380 le partite della stagione: oggi `12:00`, in passato
  `00:00`. Si riconosce dal fatto che la stagione ha **una sola ora distinta**
  — dal 1920 in poi ce ne sono fra 6 e 13.

  Perche' e' scritto qui: `test_kickoff` scartava le righe a mezzanotte, e
  quando il segnaposto e' diventato `12:00` quelle 1900 righe sono rientrate
  nel confronto facendo crollare l'accordo fra le fonti dal 99.5% al 58.3%.
  Il test accusava il fuso orario, che era giusto. Ora il criterio e'
  strutturale — si scarta la stagione con una sola ora distinta, qualunque
  essa sia — e i due orari coincidono al minuto sul **99.48% di 2691 partite**
  dal 2019/20.

  Non tocca la produzione: `predict.kickoff` e `backtest_log.flag_post_kickoff`
  leggono l'orario da `fbref_schedule`, non da `matches_master`.
- **Non esiste la colonna `referee`.** Era una feature debole, si rinuncia.
- **ClubElo era irraggiungibile** al momento dell'ingestion (502 su tutte le
  squadre). Lo stage `elo` e' opzionale: l'Elo proprio, calcolato dai
  risultati, non dipende da servizi esterni. Riprovare piu' avanti.
- **Una riga ha date discordanti fra le fonti ed e' corretta cosi'**:
  Udinese-Roma 2023/24, iniziata il 14 aprile 2024 e sospesa al 71' per il
  malore di Ndicka, conclusa il 25 aprile. football-data registra la data di
  conclusione, Understat quella di inizio. Non e' un bug, e conferma perche'
  la chiave di join non deve essere la data.
- `fbref_schedule.parquet` ha 4941 righe contro le 4580 di `matches`, perche'
  include le partite di 2026/27 non ancora giocate. E' li' che stanno le
  partite da predire.

### Moduli scritti e verificati

- `src/goalmodel/ingest.py` — download multi-fonte. Legge leghe e stagioni da `src/goalmodel/config.py`.
  Fail-fast dopo 3 errori consecutivi su ClubElo.
  Lo stage `fixtures` scarica le quote del turno imminente: filtra su `Div`
  (`config.FOOTBALL_DATA_DIV`), deduce la stagione dalla data (stacco a
  luglio, non da `CURRENT_SEASON` che andrebbe aggiornato a mano ogni agosto),
  applica `team_name_map.json` e **avvisa nominando le squadre non
  riconosciute** — un nome non mappato non darebbe errore, farebbe sparire la
  partita dalla previsione in silenzio
- `src/goalmodel/config.py` — percorsi, leghe, stagioni, iperparametri
- `src/goalmodel/schema.py` — il contratto dei file su disco, verificato da
  `data.py` a ogni lettura. **Non** e' l'elenco delle 222 colonne: sono solo
  quelle la cui assenza o il cui tipo sbagliato produce un errore SILENZIOSO —
  chiavi, date, risultati. I tre modi in cui e' gia' successo: un tipo che
  cambia (`season` intero contro stringa azzera il merge senza sollevare), una
  colonna che sparisce (merge `how="left"` -> NaN -> LightGBM non protesta),
  una chiave che si duplica (ogni merge senza `validate=` moltiplica righe e
  le metriche restano plausibili). `tests/test_schema.py` verifica che ognuno
  dei casi venga davvero fermato
- `src/goalmodel/normalize.py` — mapping nomi squadra tramite **assegnamento bipartito**
  (`scipy.optimize.linear_sum_assignment`), non fuzzy matching greedy. Risolve
  per esclusione i casi che difflib non trova, come Internazionale -> Inter.
  `--report --apply` scrive la mappa automaticamente sopra confidenza 0.75
- `src/goalmodel/features/form.py` — medie mobili esponenziali leakage-safe, continue
  attraverso le stagioni con attenuazione del 30% al confine
- `src/goalmodel/features/market.py` — de-vigging proporzionale e di Shin su 1X2 e
  over/under 2.5, riferimento `B365` in **apertura**. Produce anche il
  benchmark market-only sulla scala dei gol (`mkt_lambda_home/away`,
  ottenuti invertendo un Poisson indipendente su totale e supremazia).
  `data/processed/features_market.parquet`: 4580 righe, 35 colonne, 100% di
  copertura su tutte le stagioni. Verificato contro `brentq` scalare
  (scarto 9e-16) e contro i risultati veri: over 2.5 atteso 0.515 contro
  0.520 reale, gol totali 2.75 contro 2.72, gol casa 1.51 contro 1.48.
  Usare `FEATURES_T24`, mai `FEATURES_CLOSING`
- `src/goalmodel/evaluation/evaluate.py` — RPS, log loss, Brier, accuratezza, curve di calibrazione
  ed ECE, harness di walk-forward per giornata. **Il ciclo di valutazione e'
  stato costruito prima delle altre feature**, di proposito: ogni feature
  successiva si misura sullo stesso test set invece di accumularsi non
  validata
- `src/goalmodel/models/baseline.py` — M0/M0b/M1/M1b/M2 piu' la funzione condivisa
  `score_matrix` (lambda -> matrice dei risultati esatti -> 1X2 e over/under)
  con correzione Dixon-Coles opzionale, spenta di default. `valid_rho_floor`
  da il rho minimo ammissibile: la correzione DC non e' valida per ogni rho,
  serve rho > -1/max(lam, mu) o escono probabilita' negative
- `src/goalmodel/models/dixon_coles.py` — M3, decadimento temporale esponenziale e rho
  stimato **insieme** ad attacchi e difese, non fissato a priori. Gradiente
  analitico (verificato a 1.8e-8 relativo contro differenza finita centrata:
  con la differenza in avanti di `approx_fprime` l'errore di troncamento e'
  1e-4 e sembra un bug del gradiente). Un fit costa 25-70 ms
- `src/goalmodel/models/gbm.py` — M4 (LightGBM Poisson a due gol separati, con e senza
  feature di mercato), **M5** (`MarketAnchoredGBM`, ancorato al mercato via
  `init_score = log(mkt_lambda)`: stima il residuo, non il livello) e **M6**
  (`LogBlend`, media geometrica fra i lambda del mercato e quelli di M4).
  Il numero di alberi NON e' un iperparametro di griglia, lo decide l'arresto
  anticipato su un ritaglio della CODA DEL TRAINING — informazione gia'
  disponibile a T-24h, quindi non e' leakage. Ricerca casuale in parallelo su
  8 processi, ~2-7 minuti.
  **Attenzione a `predict()` con init_score**: LightGBM restituisce
  `exp(somma degli alberi)` e NON riaggiunge l'init_score. Va sommato a mano
  in scala logaritmica, come fa `MarketAnchoredGBM.predict`. Sbagliarlo non
  solleva nessun errore: produce semplicemente il modello sbagliato
- `src/goalmodel/prediction/predict.py` — inferenza settimanale. Modello **parametrico**, con M1
  (market-only) come predefinito: e' l'unica scelta coerente con il test set,
  che dice che nessun modello statistico batte il mercato. Quando un M7 lo
  battera' con intervallo netto, qui cambia un argomento.
  Le feature delle partite non giocate si ottengono **accodando la riga futura
  allo storico** e rilanciando i moduli di feature senza modificarne la
  logica: `form.py` scrive lo stato prima di ogni partita e salta i valori
  nulli, quindi una riga futura riceve lo stato dopo l'ultima partita giocata
  e non corrompe nulla. Tre protezioni, tutte verificate scattare:
  `assert_no_post_match` (nessuna colonna post-partita valorizzata sulle righe
  future), il controllo di duplicazione storico/futuro, e l'assert che il
  timestamp UTC preceda il calcio d'inizio
- `src/goalmodel/prediction/backtest_log.py` — rilegge `predictions_log.csv`, aggancia i risultati
  veri sulla quadrupla e calcola RPS e calibrazione **solo** sulle previsioni
  scritte prima del calcio d'inizio. Quando ci sono piu' righe per la stessa
  partita usa la PRIMA per timestamp: il registro e' append-only e la piu'
  recente sarebbe anche la piu' informata. Ricalcola anche l'RPS delle quote
  registrate, che e' il motivo per cui vanno salvate: a mesi di distanza
  distingue un errore del modello da un prezzo cambiato
- `src/goalmodel/features/context.py` — **blocco A**: riposo, congestione su finestra
  (d-14, d) aperta da entrambi i lati, infrasettimanale, derby da
  `manual/derbies.csv` con coppia NON ordinata. Solo calendario, nessuna
  ingestion nuova. **Le coppe europee non ci sono e non ci possono essere**:
  `fbref_schedule` contiene la sola Serie A, quindi una partita di Champions
  del martedi' non compare da nessuna parte. Le due scorciatoie (dedurre chi
  gioca in Europa dalla classifica dell'anno prima; usare l'orario di calcio
  d'inizio come indizio) sono peggio del buco — la prima e' una funzione dei
  risultati passati, cioe' proprio cio' che il piano esclude
- `src/goalmodel/reporting/` — tre file, non uno: `sezioni.py` calcola e
  restituisce DataFrame, `pagina.py` impagina e restituisce stringhe,
  `report.py` fa solo da regia e scrive i due file. La separazione e' la
  chiusura di A4: finche' stavano insieme, `divergenza()` addestrava un
  modello dentro il modulo di presentazione e un errore di feature si
  manifestava come sezione mancante.
  Il report settimanale in HTML statico: CSS dentro il file,
  grafici in SVG generato a mano, nessun CDN e nessun framework. Cinque
  sezioni: giornata in arrivo, cosa e' cambiato rispetto all'ultima previsione
  di quelle squadre, divergenza fra M4-senza-mercato e M1 (**diagnostica, non
  segnale di scommessa**), track record con la linea del backtest e la stima di
  quante previsioni servono ancora, stato del sistema. Lo chiamano in coda i
  due comandi di giornata, ma gira anche da solo con `goalmodel report
  --open` — e in
  quel caso **non tocca il registro**, e il report lo dichiara.
  La sezione 3 addestra M4 e controlla che non sia degenerato: con le feature
  di forma nulle LightGBM si ferma a un albero e prevede la stessa cosa per
  tutte le partite, senza sollevare niente
- `src/goalmodel/prediction/rounds.py` — il ciclo di vita della giornata e la tabella di stato. E'
  qui che si decide su quale giornata agire, guardando quote, registro e
  risultati: nessun comando chiede il numero e nessuno sa che giorno e'.
  Lo stato CHIUSA e' l'esistenza del file di archivio, non una colonna che
  potrebbe divergere dai fatti
- `src/goalmodel/prediction/predict_round.py` — porta una giornata da aperta a predetta. Gestisce la
  giornata coperta a meta' (lo snapshot copre solo il turno imminente):
  registra quelle che puo', nomina quelle che restano, e al lancio dopo
  completa senza duplicare
- `src/goalmodel/prediction/close_round.py` — porta una giornata da giocata a chiusa. Non chiude una
  giornata incompleta nemmeno se glielo si chiede con `--round`: un RPS
  archiviato su nove partite su dieci non sarebbe piu' correggibile. Scrive
  l'errore per singola partita e rifa' il cumulativo da zero dai file di
  giornata, che sono la fonte
- `tests/make_fixtures.py`, `tests/test_form.py`, `tests/test_kickoff.py`,
  `tests/test_market.py`, `tests/test_leakage.py`,
  `tests/test_predictions_log.py`, `tests/test_report.py`,
  `tests/test_rounds.py`, `tests/test_context.py` — dati sintetici e test di
  regressione. `test_context` ha gia' trovato un confine sbagliato: la
  finestra della congestione includeva la partita di esattamente 14 giorni
  prima, cioe' un'unita' in piu' in ogni turno regolare, senza nessun sintomo. **Nessun test scrive nella cartella dati vera**:
  `test_form` passava `save=True` per errore e sovrascriveva
  `features_form.parquet` con 90 righe sintetiche, senza alcun errore —
  il merge di `load_dataset` riempiva di NaN tutte le feature di forma e M4
  degenerava in silenzio in un modello costante

### Protocollo di valutazione — fissato, non cambiarlo per far vincere un modello

- Test set: **2324, 2425, 2526** (`config.TEST_SEASONS`). 1140 partite.
- Burn-in **1415** mai in addestramento (`config.BURN_IN_SEASONS`).
- Walk-forward per giornata, riaddestramento da zero, 114 blocchi.
- **Il taglio del training e' la data, non la giornata**: `date < prima data
  della giornata`. Con il taglio per giornata una partita rinviata finirebbe
  nel training di se stessa. Verificato a ogni blocco da un assert.
- La giornata arriva da `fbref_schedule.week`. Non si ricostruisce dalle date:
  provato con assegnamento goloso, coincide solo nell'85% dei casi perche' un
  rinvio sfasa tutto il resto della stagione.

### Risultati sul test set (walk-forward, 1140 partite)

| modello | RPS | skill_closed | delta mercato | IC 95% | verdetto |
|---|---|---|---|---|---|
| M1b market-only diretto | **0.1881** | 1.000 | — | riferimento | — |
| M1 market-only via lambda | 0.1882 | 0.999 | +0.00004 | [-0.00055, +0.00061] | indistinguibile |
| M5 GBM ancorato al mercato | 0.1882 | 0.997 | +0.00011 | [-0.00056, +0.00077] | indistinguibile |
| M6 miscela log w=0.15 | 0.1886 | 0.990 | +0.00043 | [-0.00022, +0.00107] | indistinguibile |
| M4 GBM con mercato | 0.1905 | 0.941 | +0.00242 | [+0.00070, +0.00411] | peggio |
| M4 GBM senza mercato | 0.1944 | 0.846 | +0.00629 | [+0.00342, +0.00923] | peggio |
| M3 Dixon-Coles hl=240g | 0.1949 | 0.834 | +0.00681 | [+0.00395, +0.00956] | peggio |
| M2 GLM Poisson | 0.1969 | 0.786 | +0.00877 | [+0.00559, +0.01191] | peggio |
| M0b frequenze di base | 0.2291 | 0.000 | +0.04095 | [+0.03449, +0.04741] | peggio |
| M0 sempre casa | 0.4583 | -5.599 | +0.27020 | [+0.24722, +0.29411] | peggio |

`skill_closed` = quota della distanza fra il pavimento M0b e il mercato,
coperta dal modello. Comunica molto piu' del valore assoluto: 0.1944 non dice
niente da solo, "l'85% della strada verso il mercato" si'.

**Soglia da battere: RPS 0.1881. Nessun modello la batte.**

### Cosa dicono i confronti appaiati — leggerli, non leggere gli RPS

| confronto | differenza | IC 95% | conclusione |
|---|---|---|---|
| M4 senza mercato − M3 | -0.00052 | [-0.00401, +0.00282] | indistinguibili |
| M4 senza mercato − M2 | -0.00248 | [-0.00645, +0.00148] | indistinguibili |
| M3 − M2 | -0.00196 | [-0.00401, +0.00020] | indistinguibili |
| M4 con mercato − M4 senza | -0.00388 | [-0.00605, -0.00179] | il mercato aggiunge |
| **M5 ancorato − mercato** | **+0.00011** | **[-0.00056, +0.00077]** | **indistinguibili** |
| M6 miscela − mercato | +0.00043 | [-0.00022, +0.00107] | indistinguibili |
| M5 ancorato − M4 con mercato | -0.00230 | [-0.00384, -0.00071] | ancorare e' meglio |

Quattro conclusioni che l'ordinamento per RPS da solo non autorizzerebbe:

1. **M2, M3 e M4-senza-mercato sono indistinguibili fra loro.** L'ordine in
   tabella (0.1944, 0.1949, 0.1969) e' rumore: tutti gli intervalli
   contengono lo zero. Decadimento temporale, rho e gradient boosting su xG
   e PPDA arrivano tutti allo stesso posto. Non spendere altro tempo a
   scegliere fra questi tre finche' non arrivano feature nuove.
2. **Il mercato aggiunge informazione al GBM** (-0.0039, intervallo netto).
3. **Ancorare il modello al mercato batte il darglielo come feature**
   (-0.0023, intervallo netto). E' la conferma che M4-con-mercato era
   sotto-potenziato: fra cinquanta colonne le quote si diluiscono, come
   punto di partenza no. Se un giorno servira' un modello che parte dal
   mercato, la forma giusta e' M5, non M4.
4. **Ma nemmeno M5 aggiunge niente al mercato** (vedi sopra, risultato
   acquisito). Con le feature di oggi il modo migliore di usare il mercato
   resta non toccarlo.

## Il modello NON produce valore atteso positivo — misurato, non dedotto

Domanda ricorrente, risposta con i numeri del test set (1140 partite fuori
campione), da `scratchpad/ev.py` e `safe.py`:

| dove si punta | EV medio | puntate con EV>0 | ROI reale |
|---|---|---|---|
| quote B365 di apertura | **-6.19%** | **0 su 3420** | -8.18% |
| miglior quota di mercato (`Max`) | -1.40% | 31.7% | -4.01% |
| B365 di chiusura (fuori orizzonte) | -6.07% | 18.1% | -8.70% |

**Zero puntate su 3420 hanno EV positivo contro B365, ed e' aritmetica, non
sfortuna.** M1 *e'* la quota B365 col margine tolto: se p = pi/1.055 e la
quota e' 1/pi, allora EV = p*(1/pi) - 1 = -5.2% identico su ogni riga.

Contro le quote `Max` il 31.7% delle puntate sembra a EV positivo, ma il ROI
realizzato peggiora in modo monotono al crescere dell'EV stimato: -7.4% sopra
lo zero, -19.0% sopra il 2%, **-47.3% sopra il 5%** (127 puntate, intervallo
che non tocca lo zero). Un book molto piu' generoso di B365 non e' un'occasione:
e' un book che sa qualcosa, o un errore che non si riesce a colpire.

**Closing line value negativo**: puntando sull'esito piu' probabile secondo il
modello, la chiusura gli da' ragione solo nel 45.5% dei casi e muove la linea
di -0.0034 in media. Un modello con vantaggio reale batte la chiusura; questo
la insegue, come ci si aspetta da un modello che *e'* la linea di apertura.

### "Ma una giocata sicura a quota bassa?"

Probabilita' alta abbassa la varianza, non il margine. Misurato:

| strategia | vinte | ROI | IC 95% |
|---|---|---|---|
| esito piu' probabile | 53.9% | -1.83% | — |
| solo se p > 70% | 77.0% | -0.09% | ±9.77% |
| solo se p > 80% | 72.2% | -15.06% | ±25.06% |
| doppia chance piu' sicura | **80.6%** | **-2.88%** | ±2.82% |

L'ultima riga e' la piu' istruttiva: si vince quattro volte su cinque e si
perde comunque, con un intervallo che **esclude lo zero**. Nessuna soglia di
sicurezza produce profitto, perche' il margine del book (5.19%) e' identico su
tutti i mercati derivati dalle stesse quote.

`predict_round` mostra le selezioni piu' probabili con la **quota equa** accanto
(`models.baseline.all_markets` e `fair_odds`): serve a confrontare con quella
del book, non a promettere un vantaggio che non c'e'.

## RISULTATO ACQUISITO — non riaprirlo senza dati nuovi

**Le statistiche aggregate di gioco non aggiungono informazione alle quote.**
Chiuso con un test ben potenziato, non per stanchezza.

### Come e' stato chiuso

La prima versione del test (M4 con le quote fra cinquanta feature) era
sotto-potenziata: il modello spendeva capacita' a ricostruire il mercato dai
suoi ingressi prima di poterlo correggere. M5 elimina il problema.

**M5** e' un LightGBM Poisson con `init_score = log(mkt_lambda)`: il mercato
non e' una feature, e' il punto di partenza esatto, e ogni albero puo'
occuparsi solo del residuo. Le feature sono solo quelle non di mercato.

La proprieta' che rende il test valido: **se non c'e' segnale, M5 degenera nel
mercato.** Verificato direttamente — azzerando le feature, l'arresto anticipato
si ferma a 1 albero e lo scarto logaritmico dal mercato e' **0.00000**.

Risultato: **M5 e' indistinguibile dal mercato**, +0.00011 con intervallo
[-0.00056, +0.00077]. Non lo batte. E non e' nemmeno peggiore, il che esclude
la spiegazione alternativa (regolarizzazione troppo debole, modello che
aggiunge rumore): il test ha davvero deciso.

**M6**, la miscela geometrica fra i lambda del mercato e quelli di M4 senza
mercato, e' il controllo povero: un solo parametro libero, impossibile
sovradattare. Il peso ottimo sulla validazione e' 0.15 con una curva a U dal
minimo interno, ma il guadagno di 0.00011 non sopravvive sul test:
+0.00043, intervallo [-0.00022, +0.00107]. Indistinguibile anche lui.

Due modi opposti di combinare — uno flessibilissimo, uno rigidissimo — danno
la stessa risposta. Non e' un problema di forma del modello.

### Cosa usa davvero M4 senza mercato — la diagnosi

`goalmodel gbm --importance`, guadagno medio su 3 stagioni x 2 lati,
aggregato per statistica sommando le viste casa/fuori/differenza:

```
deep_for       13.2%      xpts_against    7.4%      goals_for   4.7%  <- nono posto
xpts_for       11.8%      sot_for         6.5%      sot_against 4.4%
ppda_against    8.6%      np_xg_against   6.5%      shots_for   4.1%
shots_against   7.7%      deep_against    5.5%      np_xg_for   4.0%
```

**Il GBM non sta reinventando la forza di squadra dai gol**: `goals_for` e'
nono, al 4.7%. In cima ci sono i punti attesi, i deep completions, il PPDA e
l'xG non da rigore — cioe' esattamente i segnali avanzati.

Con una precisazione che conta: `xpts` (punti attesi) e' esso stesso derivato
dall'xG, ed e' a tutti gli effetti una misura sintetica di forza. Quindi la
risposta non e' netta come le due diagnosi alternative suggerivano: il modello
usa segnali avanzati, ma li usa **per costruire una misura di forza**, che e'
poi la stessa cosa che Dixon-Coles stima dai risultati. Ecco perche' M2, M3 e
M4 finiscono indistinguibili: tre strade diverse per la stessa quantita'.

L'informazione c'e' — M4 senza mercato copre l'84.6% della distanza fra il
pavimento e il mercato, e i dati di gioco da soli ricostruiscono gran parte di
cio' che le quote sanno. Ma e' la **stessa** informazione che le quote gia'
contengono, prezzata meglio.

### Conseguenza operativa

**Non cercare altre statistiche aggregate di gioco.** Non aggiungere altre
medie mobili, altre finestre, altre viste degli stessi eventi: il test dice
che quella direzione e' esaurita. Cercare dove il mercato e' strutturalmente
cieco o lento — il layer giocatori e gli infortuni.

Cosa riaprirebbe la questione: dati nuovi di natura diversa (giocatori,
formazioni probabili, infortuni), non un modello nuovo sugli stessi dati.

### Come si decide se una differenza e' reale — confronto appaiato

**Non guardare la varianza fra stagioni.** Una versione precedente di questo
documento ne ricavava una soglia di rumore di 0.005: e' sbagliato. La varianza
fra stagioni (da 0.178 a 0.202 per il solo mercato) misura quanto le stagioni
siano diverse fra loro in difficolta', non l'incertezza sulla differenza fra
due modelli. Due modelli valutati sulle **stesse** partite condividono quella
difficolta', e la differenza riga per riga la elimina.

Il metro corretto e' in `evaluate.paired_comparison`:
1. RPS di ogni singola partita, per ogni modello;
2. differenza appaiata riga per riga contro il riferimento;
3. bootstrap con **cluster sulla giornata** (114 cluster, non 1140 partite:
   le dieci partite di una giornata sono predette dallo stesso addestramento),
   intervallo al 95%.

Il cluster non e' un dettaglio: ignorarlo restringe l'intervallo del **38%**.
La copertura empirica dell'intervallo, verificata per simulazione, e' 96.5%
contro il 95% nominale.

**Una differenza conta solo se il suo intervallo non contiene lo zero.**

### Ipotesi aperte — da verificare, non da assumere

- **L'half-life di M3 e' lunga: 240 giorni.** Tarata sulla validazione, con
  minimo interno alla griglia e curva piatta fra 180 e 540. Otto mesi di
  memoria sono molti per una squadra di calcio, e l'ipotesi e' che siano
  lunghi **perche' mancano le feature che catturano il cambiamento**:
  infortuni, mercato di gennaio, cambi di allenatore. Senza quelle, l'unico
  modo che il modello ha di non sbagliare dopo uno shock e' non credere
  troppo al recente, cioe' allungare la memoria.

  **Predizione falsificabile**: quando arrivera' il layer giocatori e le
  feature di contesto, l'half-life ottima deve **accorciarsi**. Se si ritara
  e resta sui 240 giorni, quelle feature non stanno funzionando — e va
  indagato quello, non accettato il risultato. Ritarare sempre con
  `goalmodel dixon-coles --tune` dopo ogni nuovo blocco di feature.

### Potenza: cosa questo test set puo' vedere — rifatto due volte

`goalmodel power`. Tre errori corretti, e ognuno cambiava la
conclusione. Il terzo la ribalta.

**1. Soglia e quota non sono la stessa cosa.** "Oltre il 15% dei minuti
indisponibili" e' la **soglia** che definisce il sottoinsieme; la **quota** e'
la frazione di partite che quella soglia seleziona, e finche' il blocco non
esiste non si conosce. Il modulo usava lo stesso numero per entrambe:
risultati plausibili e sbagliati. Ora la quota e' un parametro che si fa
variare, e per il contesto e' misurata (11% derby, 11% infrasettimanali).

**2. La correlazione dentro la giornata e' ~0, ed e' misurata, non assunta.**
ANOVA a una via sulle differenze appaiate raggruppate per giornata; la formula
del design effect — `Var(media di k) = sigma^2 (1+(k-1)rho)/k` — riproduce la
sd osservata entro il 7%. **Questo smentisce quanto era scritto prima**: "i
Big 5 non aggiungono potenza, aggiungono solo partite dentro gli stessi 114
cluster" vale solo se rho ~ 1. Con rho ~ 0 allargare il cluster abbassa
davvero la varianza della sua media, e le due definizioni di cluster — che
sembravano distare un fattore sqrt(5) — danno in pratica lo stesso numero.

**3. La coppia di riferimento era sbagliata, e questo cambiava tutto.**
Il rumore veniva misurato su M5 ancorato **contro il mercato**. Ma un blocco
di feature non si decide contro il mercato: si decide sulla differenza fra due
modelli **annidati**, lo stesso M5 con e senza le colonne nuove. I due
condividono quasi tutto, quindi la loro differenza e' molto meno rumorosa.

| coppia | sd/partita | sd/cluster | MDE (Serie A, tutte) |
|---|---|---|---|
| M5 − mercato (livello) | 0.01102 | 0.00354 | 0.00093 |
| **M5+blocco − M5 (blocco)** | **0.00280** | **0.00083** | **0.00023** |

Il confronto annidato e' **4.3 volte** meno rumoroso. Usare la coppia
sbagliata gonfiava il minimo rilevabile dello stesso fattore e faceva
dichiarare non misurabile un test che invece lo era.

### Quello che il test set vede davvero

Effetto atteso da uno shift di 0.10 gol su una partita del sottoinsieme:
0.00060 RPS.

| perimetro | cluster | MDE sul 20% | vede? |
|---|---|---|---|
| Serie A | giornata | 0.00052 | **si** |
| Big 5 | giornata di campionato | 0.00023 | si |
| Big 5 | settimana di calendario | 0.00024 | si |

Quota minima perche' un effetto da 0.10 gol si veda: **16% delle partite in
Serie A**, 4% con i Big 5. Sotto quella quota il sottoinsieme e' troppo
piccolo, e restringerlo ancora **alza** il minimo rilevabile invece di
abbassarlo — le partite diminuiscono. Restringere paga solo se l'effetto per
partita cresce piu' in fretta del rumore.

**Conseguenza operativa**: la Serie A da sola basta a misurare un blocco che
tocchi almeno il 16% delle partite con uno shift da 0.10 gol. I Big 5 restano
la leva piu' grande (2.2x) e servono per i sottoinsiemi piccoli — il derby
all'11% non arriva alla soglia in Serie A.

### Blocco A — contesto — MISURATO DUE VOLTE E SCARTATO

`goalmodel evaluate --blocco contesto`, 8 settembre 2026. Nove colonne:
riposo, congestione su finestra (d-14, d), infrasettimanale, derby.

| modello | RPS | skill_closed | delta mercato | IC 95% |
|---|---|---|---|---|
| M1b market-only | 0.1881 | 1.000 | riferimento | — |
| M5 ancorato, **senza** contesto | 0.1882 | 0.997 | +0.00011 | [-0.00056, +0.00077] |
| M5 ancorato, **con** contesto | 0.1883 | 0.995 | +0.00020 | [-0.00052, +0.00089] |
| M0b frequenze di base | 0.2291 | 0.000 | +0.04095 | [+0.03449, +0.04741] |

**Il confronto che decide** — l'unico in cui il blocco e' l'unica cosa
cambiata:

| confronto | differenza | IC 95% | conclusione |
|---|---|---|---|
| **M5+contesto − M5** | **+0.00008** | **[-0.00007, +0.00024]** | **indistinguibili** |

L'intervallo contiene lo zero. **E non e' un test debole**: il minimo
rilevabile di quel confronto e' 0.00022, sotto i 0.00060 attesi da uno shift
di 0.10 gol. Il blocco e' stato misurato con potenza sufficiente e non c'e'.

**Cosa usa il modello, e perche' non basta.** Importanza per guadagno di M5
ancorato (`--importance --anchored`), le nove colonne del blocco:

```
diff_rest_days     3.30%   rango 5/61      is_derby           0.77%   53/61
home_rest_days     1.71%        24/61      away_rest_days     0.66%   55/61
away_matches_14d   1.09%        39/61      home_matches_14d   0.36%   58/61
derby_intensity    0.31%        59/61      is_midweek         0.00%   60/61
                                           diff_matches_14d   0.00%   61/61
```

Totale 8.2% del guadagno con 9 colonne su 61, contro il 14.8% che avrebbero
se contassero come le altre. **Il differenziale di riposo e' la quinta feature
su sessantuno** — il modello lo usa parecchio — e nonostante questo la
previsione non migliora. `is_midweek` e `diff_matches_14d` non ricevono
nemmeno uno split: guadagno esattamente zero.

La lettura e' la stessa del risultato acquisito sulle statistiche di gioco:
non che riposo e derby non contino per il calcio, ma che **il mercato li ha
gia' prezzati**. Le quote di apertura escono quando il calendario e' noto da
mesi: chi le fa sa benissimo chi ha giocato mercoledi'.

**Rimosso, non tenuto.** Le nove colonne sono in `gbm.BLOCCHI_SCARTATI`:
restano nel dataset per poterle rimisurare su un perimetro piu' largo, ma non
entrano nel modello. Tenerle "male che vada non fanno danno" sarebbe sbagliato
con 3400 righe di training, e la diluizione e' gia' stata osservata su
M4-con-mercato.

### Seconda misura: con le coppe europee — il blocco completo

La prima misura era **monca**, e va detto chiaro: riposo e congestione di solo
campionato sono quasi uguali per tutti, perche' le date delle giornate non
cambiano quando una squadra gioca in Europa. Il meccanismo — giocare il
martedi' in Champions e la domenica in campionato — era proprio la parte che
mancava.

`goalmodel ingest --stage cups` scarica Champions, Europa e Conference League
da FBref: **solo calendario, una richiesta per competizione e stagione, niente
browser**.

**ATTENZIONE, LE 4140 PARTITE ERANO DUE COPPE SU TRE.** Il numero scritto qui
in origine — 4140 — e' esattamente Champions (1766) piu' Europa (2374): la
Conference League **non e' mai stata scaricata**. `ingest_cups` metteva il
`try` per competizione invece che per stagione, e `KeyError: '1415'` (la
Conference non esiste prima del 2021/22) si portava via l'intera coppa,
comprese le stagioni in cui esisteva. Corretto il 22 settembre 2026, con
isolamento per (coppa, stagione) e un test che lo verifica.

**Cosa ne segue per il blocco A.** Il 23.6% di partite con una coppa nei 14
giorni precedenti e' calcolato SENZA la Conference, e le stagioni scoperte —
dal 2021/22 — sono proprio quelle che si sovrappongono al test set. Roma,
Fiorentina, Lazio e Atalanta ci hanno giocato. Il blocco e' stato misurato
nullo e scartato, quindi la conclusione non cambia; ma se un giorno si
riapre, va rimisurato sui dati completi, non su questi. Le tre coppe sono chiavi nuove in `LEAGUE_DICT`,
registrate a runtime da `ingest.registra_coppe` — vedi `config.FBREF_CUPS`, e i
nomi devono essere quelli esatti della pagina `fbref.com/en/comps/`.

`features/context.py` ora conta le coppe dentro riposo e congestione, e
aggiunge `home/away/diff_cup_14d`. Aggancio verificato: 12 squadre italiane
in Europa, Roma 131 presenze, Juventus 120, Napoli 108; Napoli e Inter
2023/24 hanno 15 partite con una coppa nei 14 giorni prima, l'Empoli zero.
**Il 23.6% delle partite di Serie A ha una coppa nei 14 giorni precedenti.**

| misura | differenza | IC 95% | semiampiezza |
|---|---|---|---|
| senza coppe | +0.00008 | [-0.00007, +0.00024] | 0.00016 |
| **con coppe** | **-0.00004** | **[-0.00014, +0.00007]** | **0.00010** |

Il verdetto non cambia e l'intervallo si **stringe**: minimo rilevabile
0.00015, contro i 0.00060 attesi da uno shift di 0.10 gol. E' un null ben
potenziato, ora sul blocco completo.

**L'importanza dice la cosa piu' interessante.** Con le coppe dentro:

```
home_rest_days   1.48%  27/64      is_derby         0.64%  50/64
diff_rest_days   1.43%  28/64      derby_intensity  0.27%  58/64
away_matches_14d 0.49%  54/64      home_cup_14d     0.05%  61/64
away_rest_days   0.43%  57/64      diff_cup_14d     0.01%  63/64
home_matches_14d 0.16%  59/64      away_cup_14d     0.00%  64/64
```

Le colonne di coppa sono le **ultime tre** su sessantaquattro: il modello non
ci trova niente. E `diff_rest_days` **scende dal 5o al 28o posto** quando il
meccanismo vero entra nel dataset — la sua importanza nella prima misura era
struttura spuria, non segnale. Totale del blocco: 5.0% del guadagno con il
18.8% delle colonne, ancora piu' sotto la sua quota di prima (8.2% contro
14.8%).

**Ora la domanda e' chiusa davvero.** Non "il contesto non conta per il
calcio", ma: il mercato lo ha gia' prezzato. Le quote di apertura escono
quando il calendario, coppe comprese, e' noto da mesi.

### Blocco B — giocatori e infortuni — PROVVISORIO

`goalmodel evaluate --blocco giocatori`, 11 settembre 2026. Nove colonne
da `src/goalmodel/features/players.py`: quota di minuti indisponibili, quota di
gol+assist indisponibili, numero di assenti, per lato e in differenza.

**E' il primo blocco che supera la regola.** E va letto con la stessa
prudenza con cui sono stati letti i risultati nulli.

| modello | RPS | skill_closed | delta mercato | IC 95% |
|---|---|---|---|---|
| M5 ancorato, **con** giocatori | **0.1880** | 1.0038 | -0.00015 | [-0.00088, +0.00055] |
| M1b market-only | 0.1881 | 1.000 | riferimento | — |
| M5 ancorato, **senza** giocatori | 0.1882 | 0.997 | +0.00011 | [-0.00056, +0.00077] |
| M0b frequenze di base | 0.2291 | 0.000 | +0.04095 | [+0.03449, +0.04741] |

**Il confronto che decide:**

| confronto | differenza | IC 95% | conclusione |
|---|---|---|---|
| **M5+giocatori − M5** | **-0.00027** | **[-0.00054, -0.00001]** | **il blocco aggiunge** |

**Tre cose da non confondere.**

1. **Il blocco migliora M5, non porta M5 sopra il mercato.** Contro M1b resta
   indistinguibile: -0.00015 con IC [-0.00088, +0.00055]. `skill_closed`
   1.0038 e' sopra 1, ma l'intervallo dice che quel sorpasso non e'
   distinguibile dal rumore. **La soglia 0.1881 non e' stata battuta.**
2. **L'intervallo tocca lo zero.** L'estremo superiore e' -0.00001, e il
   2.25% dei ricampionamenti non migliora. Ha superato la regola, ma per un
   pelo: e' un risultato da rileggere quando il test set sara' cresciuto, non
   un fatto acquisito come i risultati nulli sulle statistiche di gioco.
3. **Non sopravvive ai confronti multipli.** Tre test di blocco (contesto
   senza coppe, contesto con coppe, giocatori), Bonferroni a una coda: soglia
   **0.0083**, e il blocco sta a **p = 0.0225**, 2.7 volte troppo grande.
   Holm si ferma al primo passo. Il conto e' m = 3 e non 4: la quota di
   minuti NON pesata era nel modulo dalla prima stesura, scritta prima di
   qualsiasi misura con la motivazione del portiere — non e' stata aggiunta
   dopo aver visto fallire le colonne pesate. Con m = 4 la soglia scende a
   0.00625 e la conclusione non cambia.

### La diagnostica sui NaN: il modello usa il contenuto

`goalmodel evaluate --blocco-nan giocatori`. Il blocco ha NaN su tutto
cio' che precede il 2021/22, e LightGBM puo' splittare su "so / non so"
guadagnando — perche' quella separazione coincide con il tempo, non con gli
infortuni. Si riaddestra sulle sole stagioni coperte, dove di NaN non ce ne
sono:

```
tutto il training (con NaN)          9.8%  del guadagno
solo stagioni coperte (senza NaN)   15.8%  del guadagno
```

L'importanza **sale**, non crolla: il modello legge il contenuto. Ed e'
coerente — su un training dove la feature c'e' sempre, serve di piu'.

### Cosa fa il lavoro, e la sorpresa

Importanza per guadagno dentro M5, le nove colonne:

```
home_quota_minuti_assenti  5.17%   rango  1/61   <- prima feature su 61
diff_quota_minuti_assenti  1.67%         23/61
diff_n_assenti             1.09%         40/61
diff_quota_ga_assente      0.51%         54/61
home_quota_ga_assente      0.39%         57/61
home_n_assenti             0.34%         58/61
away_quota_minuti_assenti  0.25%         59/61
away_n_assenti             0.24%         60/61
away_quota_ga_assente      0.17%         61/61
```

**La quota di minuti della squadra di casa e' la prima feature su
sessantuno**, e le colonne pesate per gol+assist — quelle che il piano
chiedeva — stanno tutte in fondo. La decisione di affiancare la quota di
minuti NON pesata si e' rivelata quella che regge il blocco: con il solo peso
per produzione, il blocco non avrebbe avuto niente da dire.

**L'asimmetria casa/trasferta e' grande e non e' spiegata.** 5.17% contro
0.25%: le assenze della squadra di casa contano venti volte quelle della
squadra in trasferta. Puo' essere reale — chi gioca in casa attacca di piu' e
quindi perde di piu' a mancargli un titolare — oppure un artefatto. **Va
indagata prima di costruirci sopra**: se fosse un artefatto, il blocco
poggerebbe su una colonna sola e su un caso.

### Verifiche di robustezza — 15 settembre 2026

`python -m goalmodel.experiments.blocco_b_robustezza`, 15 walk-forward (3 varianti x
5 semi). Riassunto completo e versionato in
`experiments/output/riassunto_bloccoB_robustezza.md`. **Regola scritta prima
dei risultati: queste verifiche possono solo declassare, mai promuovere.**

**Determinismo**: il seme 0 rifatto coincide con la misura originale, scarto
0.00e+00 su 1140 partite.

**Semi** (M5+GIOCATORI − M5): -0.00027, -0.00026, -0.00016, -0.00022,
-0.00032. **Il segno e' stabile, la significativita' no**: cinque stime su
cinque negative, ma intervallo sotto zero solo in due semi su cinque.

**Simmetria** — tolte `home_` e `away_quota_minuti_assenti`, resta la sola
differenza: -0.00024, -0.00032, -0.00020, -0.00032, -0.00022, **intervallo
sotto zero in cinque semi su cinque**. Media dei semi: simmetrico − completo
= -0.00001, IC [-0.00013, +0.00011]. **Il guadagno non evapora: l'asimmetria
20:1 era una rappresentazione.** Con due colonne quasi gemelle LightGBM ne
sceglie una secondo il campionamento delle colonne (`colsample_bytree` 0.6),
e l'importanza si concentra li'.

**Confronti multipli, conclusione esplicita**:
- misura pre-registrata (seme 0): p = 0.0225, **non passa** Bonferroni (0.0083);
- stessa ipotesi con meno varianza (media 5 semi): p = 0.0159, **non passa**;
- variante simmetrica mediata: p = 0.0021 passerebbe, **ma non e' la specifica
  pre-registrata** e non la puo' sostituire dopo aver visto i dati.

**Importanza mediata su cinque semi** (`--importanza`): quota di guadagno
di `home_quota_minuti_assenti` contro la gemella in trasferta, per seme:
20.7:1, 10.0:1, 6.8:1, 10.8:1, 10.4:1 — media **10.8:1**. Il 20:1 della
diagnosi originale era il seme piu' estremo dei cinque. Il verso non si
inverte mai (la colonna di casa resta fra le prime 15, quella in trasferta
oltre la 45a), quindi una preferenza per il lato casa c'e'; ma la simmetrica
che rende uguale mostra che non porta informazione in piu' della differenza.

**Esito: GIOCATORI resta PROVVISORIO.** Non scartato — simmetria tenuta,
segno stabile. Non confermato — non sopravvive alla correzione.

**Criterio di promozione non raggiunto**: media dei semi con giocatori −
mercato = -0.00011, IC [-0.00081, +0.00057]. La produzione resta su M1.

### Limiti dichiarati

- **Il peso e' gol+assist, non xG+xA.** Le statistiche giocatore-partita di
  FBref per la Serie A non hanno colonne attese (verificato su 60325 righe).
  Gol+assist e' molto piu' rumoroso: un attaccante che non ha ancora segnato
  pesa zero. Vista l'importanza quasi nulla di quelle colonne, sostituirle
  con l'xG di Understat (`--stage shots`) e' la prima cosa da provare se si
  vuole spremere altro da qui.
- **Chi e' fuori da agosto pesa zero.** Misurato: l'88.9% degli assenti
  compare in rosa prima o poi; il 10.3% che non compare mai sono lungodegenti
  veri (Deulofeu 38 partite di fila, Rog 38, Soumaoro 36). E' una scelta — la
  feature misura il contributo perso di RECENTE — e la variante da provare e'
  pesarli con i minuti della stagione precedente.
- **Copertura parziale**: 39.1% delle partite in tutto il dataset, ma **91%
  sul test set** (1037 su 1140). Le stagioni precedenti al 2021/22 restano a
  NaN, che LightGBM tratta nativamente.
- **L'orizzonte poggia su un assunto non verificato**: che la lista degli
  indisponibili di WhoScored sia quella pubblicata PRIMA della partita e non
  aggiornata a posteriori. Se cosi' non fosse, il blocco andrebbe buttato, non
  corretto.

### La predizione falsificabile sull'half-life NON e' testabile cosi'

Era scritto: "quando arrivera' il layer giocatori, l'half-life ottima di M3
deve accorciarsi". Non si puo' verificare, e vale la pena dirlo invece di
lanciare un comando che non risponde: **M3 non ha feature**. E' un modello
parametrico sui soli risultati, e la sua half-life non puo' cambiare perche'
si sono aggiunte colonne a M5. La predizione era mal posta.

La forma corretta sarebbe: un modello che usa le assenze dovrebbe preferire
una memoria piu' corta di uno che non le usa. Per porla servirebbe un
parametro di memoria dentro M5, che oggi non esiste.

### Il piano dei blocchi — uno alla volta, ciascuno misurato

**Obiettivo dichiarato**: trovare il limite del ML su questo problema
aggiungendo blocchi di informazione uno per volta e misurando ciascuno.

**Regola generale, non negoziabile.** Ogni blocco si misura **rifacendo M5**,
il GBM ancorato al mercato: e' il test piu' potente disponibile, l'ancoraggio
elimina il lavoro di ricostruire il mercato dagli ingressi, e se non c'e'
segnale M5 degenera esattamente nel mercato (verificato: azzerando le feature
si ferma a 1 albero, scarto logaritmico 0.00000). Confronto appaiato con
bootstrap a cluster sulla giornata. **Un blocco si tiene solo se l'intervallo
sta tutto sotto zero.**

**Non accumulare blocchi non misurati.** Con ~3400 righe di training la
diluizione e' reale ed e' gia' stata osservata: M4 con il mercato fra
cinquanta feature perde contro M5 ancorato di -0.0023 con intervallo netto,
esattamente perche' le quote si diluivano. Se un blocco non supera
l'intervallo va **rimosso**, non tenuto "male che vada non fa danno": con
questa numerosita' fa danno.

**Al termine di ogni blocco si riporta**: RPS, `skill_closed`, differenza
appaiata contro il mercato con intervallo, e l'importanza delle feature nuove.

#### Fase 0 — potenza — FATTA

Vedi le due sezioni sopra. In sintesi: la Serie A da sola basta a misurare un
blocco che tocchi almeno il **16% delle partite** con uno shift da 0.10 gol.
I Big 5 valgono 2.2x e servono per i sottoinsiemi piu' piccoli. La coppia di
riferimento giusta e' quella **annidata** (M5 con e senza il blocco), non M5
contro il mercato: sbagliarla gonfia il minimo rilevabile di 4.3 volte.

#### Blocco A — contesto — FATTO, SCARTATO

Vedi la sezione dedicata sopra. `+0.00008` con IC `[-0.00007, +0.00024]`:
indistinguibile, con potenza sufficiente. Le nove colonne sono in
`gbm.BLOCCHI_SCARTATI` e non entrano nel modello.

`manual/derbies.csv` e' stato compilato durante il blocco (55 coppie, 11% delle
partite) e **va rivisto**: non e' una fonte, e' un'opinione plausibile.

#### Blocco B — giocatori e infortuni — FATTO, PROVVISORIO

Vedi la sezione dedicata sopra: -0.00027 con IC [-0.00054, -0.00001]. Supera
la regola, ma l'intervallo tocca lo zero e il modello resta indistinguibile
dal mercato. Le nove colonne sono entrate nel modello; `BLOCCHI_NON_MISURATI`
e' tornato vuoto.

Quello che resterebbe da fare qui, in ordine di resa attesa: capire
l'asimmetria casa/trasferta (20x, non spiegata), sostituire gol+assist con
l'xG di Understat, pesare i lungodegenti con la stagione precedente.

#### Come era stato pianificato

`ingest --stage missing`, `--stage lineups`, `--stage player_stats`. Feature:
quota di minuti stagionali assenti **pesata per xG+xA per 90**; indice di
turnover rispetto alla partita precedente.

**Sottoinsieme di misura dichiarato PRIMA di guardare i risultati**: partite
con oltre il **15% dei minuti pesati indisponibili**. La quota di partite che
questa soglia seleziona e' ignota finche' il blocco non esiste: appena lo si
sa, va sostituita nel calcolo di potenza al posto del 20% assunto. Serve che
superi il **16%**, altrimenti in Serie A l'effetto non si vede e conviene
allargare ai Big 5 prima di misurare.

**Perche' ha senso provarlo dopo che A e' fallito.** Il contesto e' noto al
mercato da mesi — il calendario si pubblica in estate. Un infortunio a due
giorni dalla partita no: e' l'unico posto dove il mercato puo' essere
strutturalmente lento, ed e' per questo che era il candidato migliore fin
dall'inizio.

Verificare anche se **l'half-life ottima di M3 si accorcia dai 240 giorni**:
la predizione falsificabile e' gia' scritta sopra, e non dipende dalla potenza
sull'RPS. Ritarare con `goalmodel dixon-coles --tune`.

#### Blocco D — forma per sede — MISURATO IN VALIDAZIONE E SCARTATO, A COSTO ZERO

**La prima stesura di questa specifica era sbagliata, e il perche' vale piu'
della specifica.** Aveva letto `FORM_HALFLIFE_VENUE` come "finestra piu'
lunga" e proponeva le stesse colonne di BASE con half-life 20 sul test set:
cioe' esattamente "altre medie mobili, altre finestre", che il risultato
acquisito sopra ha gia' chiuso. Un test confermativo speso su un nullo atteso
alza la soglia per tutti i risultati futuri e non compra niente.

**Cosa significa davvero `FORM_HALFLIFE_VENUE = 10`**: forma **condizionata
alla sede**. Oggi il dataset ha la forma generale della squadra di casa e di
quella in trasferta; nessuna colonna dice come una squadra rende
*specificamente giocando in casa*. Nel modello il vantaggio casalingo e'
uniforme — lo stesso per chi in casa e' una fortezza e per chi non ci vince
mai. L'half-life e' piu' lunga di quella di BASE (6) perche' restringendo alla
sede i campioni si dimezzano.

**Colonne, 48**: le stesse 8 statistiche x 2 versi di BASE, per la squadra di
casa sulle sue sole partite in casa (`home_<stat>_<verso>_ewm_sede`), per
quella in trasferta sulle sole in trasferta, piu' la differenza. Half-life 10,
non tarata. Medie di lega della regressione di fine stagione calcolate **per
sede**: in casa si segna di piu', e tirare la forma casalinga verso la media
di tutte le partite la sporcherebbe. Costruite in memoria da
`src/goalmodel/experiments/forma_venue.py`, che riusa le funzioni di `form.py` senza
toccarle e senza scrivere nessun parquet.

**Non e' una copia di BASE**: in validazione la correlazione fra una colonna
per sede e la sua gemella generale sta fra **0.75 e 0.89**.

**Dove si misura: solo validazione (2122, 2223), come collinearita' e
baseline. m resta 3.** Stima dichiarata prima: media dei 5 semi.
- intervallo che contiene lo zero, o differenza >= 0 -> **set scartato, e la
  questione e' chiusa a costo zero**;
- intervallo tutto sotto zero -> **ipotesi pre-registrata** con la specifica
  congelata com'e', da testare quando il test set sara' cresciuto o sui Big 5.
  Non diventa un blocco tenuto e non promuove niente: la validazione non e' il
  test.

`tests/test_forma_venue.py` verifica su dati sintetici la cosa che un errore
qui non farebbe mai gridare: la forma casalinga non deve vedere le trasferte
(una squadra che segna 5 gol fuori casa resta a 3 nella sua colonna di casa) e
nessuna riga deve vedere se stessa.

**Esito, 16 settembre 2026** (`--lancia`, `--analizza`; 759 partite di
validazione, 76 cluster):

| stima | differenza | IC 95% |
|---|---|---|
| **media dei 5 semi** | **+0.00001** | **[-0.00035, +0.00039]** |
| semi singoli | +0.00005, -0.00008, -0.00007, +0.00008, +0.00008 | tutti a cavallo dello zero |

**Nullo, e il set e' SCARTATO** secondo la regola scritta prima. Non entra fra
le ipotesi pre-registrate e non costa niente: nessun confronto sul test
consumato, **m resta 3**.

**La diagnosi e' la parte che vale.** Il modello quelle colonne le usa eccome:
**il 50.5% del guadagno con il 48% delle colonne**, cioe' esattamente la loro
quota — non vengono ignorate, sostituiscono le gemelle di BASE. Con
correlazioni 0.75-0.89 sono un altro modo di dire la stessa cosa, non
informazione nuova. E' lo stesso schema gia' visto due volte: `diff_rest_days`
quinta su 61 nel blocco A, `home_quota_minuti_assenti` prima su 61 nel blocco
B. **Un'importanza alta non e' un miglioramento**: dice dove il modello
guarda, non se indovina di piu'.

**Limite onesto**: la validazione e' meta' del test set, e la semiampiezza
dell'intervallo e' 0.00037. Esclude un effetto dell'ordine dei 0.00060 attesi
da uno shift di 0.10 gol, **non** un effetto piccolo come quello del blocco B
(0.00027). Se un giorno arrivano i Big 5, la specifica e' congelata in
`sets.py` e si puo' rimisurare senza riscrivere niente.

#### Blocco C — valore delle rose

Scraper Transfermarkt, HTML statico. Valore dell'XI disponibile e della rosa.
**Solo se il blocco B ha dato segnale**: se le assenze non contano, il valore
di mercato che le pesa non contera'.

#### Cosa NON aggiungere, e perche'

- **Posizione in classifica e partite giocate.** Sono funzioni dei risultati
  passati, gia' catturate meglio da Elo, dai coefficienti Dixon-Coles e dalle
  medie mobili — in forma continua invece che ordinale. Aggiungerle porta
  collinearita', non informazione.
- **Arbitri.** La colonna `referee` non e' nel dataset (verificato).
  Servirebbe una fonte nuova ed e' il blocco con l'effetto atteso piu' basso:
  rimandato, o incluso gratis se emerge da un'altra ingestion.
- **Altre statistiche aggregate di gioco.** Chiuso, vedi il risultato
  acquisito sopra: sono la stessa informazione delle quote, prezzata peggio.

#### Fuori dal piano

`src/goalmodel/features/team_strength.py` — Elo proprio calcolato dai risultati. Scende
di priorita': M2, M3 e M4 sono gia' indistinguibili fra loro, e un quarto modo
di misurare la forza della squadra non cambierebbe il quadro.

## Isolamento degli esperimenti — la produzione non si tocca

**Vincolo**: `predict_round`, `close_round`, `report` e M1 devono funzionare
esattamente come prima durante tutto il lavoro sperimentale. Se un esperimento
richiede di cambiare un modulo condiviso, lo si duplica o lo si sottoclassa.

**`tests/test_production_unchanged.py` — va lanciato prima di ogni commit.**
Fissa su 101 partite gia' giocate le quote di ingresso, le fonti scelte da
`pick_odds`, i lambda di `market.py`, le probabilita' di M1 e tutti i mercati
di `all_markets`, e verifica che restino identici **bit a bit**. Il campione
include di proposito le righe dei ripieghi (BW, BbAv, Avg, P). Distingue
"e' cambiato il codice" da "sono cambiati i dati": le quote di ingresso si
confrontano per prime. `--sensibilita` prova che la rete scatta: un solo bit
spostato con `np.nextafter` fa fallire il ramo giusto. `--rigenera` solo dopo
un cambiamento di produzione voluto e dichiarato.

**Registro dei set — `src/goalmodel/features/sets.py`.** Ogni colonna appartiene a un set
con uno stato: `BASE` (congelato, 52 colonne scritte per esteso),
`CONTESTO` (scartato), `GIOCATORI` (provvisorio), `FORMA_VENUE` (da misurare).
I modelli sperimentali dichiarano i set (`M5Set(sets=["BASE", "GIOCATORI"])`);
una colonna non registrata resta fuori per default. `tests/test_sets.py`
fallisce se BASE cambia di una colonna o se il dataset contiene colonne che
nessun set riconosce.

**Il registro decide anche la produzione, dal 22 settembre 2026 (A5).** Le
costanti `BLOCCHI_*` scritte a mano in `models/gbm.py` non esistono piu':
`FUORI_DAL_MODELLO` e' `sets.fuori_dal_modello()`, cioe' le colonne dei set in
stato `scartato` o `da misurare`. **Per cambiare cosa vede il modello di
produzione si cambia lo stato di un set in `sets.py`**, non una costante
altrove. Verificato prima di sostituire: sulle 64 colonne candidate del
dataset vero i due insiemi coincidevano esattamente, quindi nessun modello e'
cambiato.

Era proprio questo doppio meccanismo ad aver prodotto B1: quando il blocco
giocatori e' stato ammesso nel registro, anche l'M4 della sezione di
divergenza ha iniziato a vedere quelle colonne senza che nessuno lo
decidesse.

**Esperimenti — `src/goalmodel/experiments/`.** Leggono tutto, scrivono solo in
`experiments/output/`, gitignorato tranne i `riassunto_*`.
`experiments.proteggi_produzione()` sostituisce `to_parquet` e `to_csv` nel
processo dell'esperimento e rifiuta qualsiasi scrittura in `data/processed/` o
`track_record/`: una convenzione regge finche' qualcuno non copia una riga da
`evaluate.py`, la guardia no. I modelli (`experiments/modelli.py`) sottoclassano
`MarketAnchoredGBM` senza modificarlo: `M5Set` (set dichiarati), `M5Colonne`
(colonne derivate), `M5MediaSemi` (media dei log-lambda di piu' semi,
ricostruibile esatta a posteriori — verificato bit a bit).

**Criterio di promozione in produzione.** Un set sperimentale entra in
`predict_round` solo se **batte il mercato** con intervallo che non tocca lo
zero, **dopo correzione per confronti multipli**. Finche' non succede, la
produzione resta su M1. Nessuna promozione perche' "sembra meglio".

### Prevedere una giornata con M5 senza promuoverlo

`src/goalmodel/experiments/predici_gbm.py`. Riusa `predict.py` invariato — stesso
calendario, stesse quote, stesse feature, stesse protezioni sul calcio
d'inizio — passandogli `M5MediaSemi(["BASE"])` e `dry_run=True`. Stampa M5
accanto a M1 con lo scarto per partita e non tocca il registro.

**Perche' non e' una scorciatoia verso la produzione.** M5 sul test set e'
indistinguibile dal mercato: il criterio di promozione non e' soddisfatto e
`predict_round` resta su M1. Questo comando serve a vedere **dove** M5 si
scosta, come la sezione 3 del report ma con il modello giusto (il report usa
M4 senza mercato, che parte da zero e diverge ovunque).

**Quanto si scosta, misurato sulla giornata 4 della 2026/27** (`--as-of
2026-09-11`, training tagliato alla stessa data): scarto massimo sull'1X2
**0.013**, mediano **0.005**. Lecce-Monza e Genoa-Frosinone i due estremi,
Atalanta-Cagliari praticamente identico (0.001). E' la conferma pratica di
cio' che il test set dice in forma statistica: **ancorato al mercato e senza
segnale nuovo, M5 resta incollato al mercato.**

**Il blocco GIOCATORI non entra in questa previsione**, ed e' voluto: per una
partita futura le assenze non sono nel dataset (servirebbe `ingest --stage
missing` sul turno in arrivo), quindi un M5 con quelle colonne girerebbe con
NaN dove in addestramento aveva dati.

### Seed averaging — `M5MediaSemi`, 15 settembre 2026

Media geometrica dei lambda di cinque LightGBM con semi diversi, modello nuovo
accanto a M5 (che non e' stato toccato). Non e' un test di ipotesi: e' una
riduzione di varianza della stima, e ricostruibile esatta dai cinque modelli
singoli (verificato bit a bit). Effetto misurato sul blocco giocatori:
l'intervallo della differenza si stringe da ±0.00027 (seme singolo) a
±0.00022, e la stima si assesta a -0.00025, al centro dei cinque semi. **Per
le misure future di un blocco si usa la media dei semi come stima, dichiarata
prima**, cosi' un risultato non dipende dal seme che capita.

### Collinearita' — nessuna riduzione, BASE resta com'e'

`python -m goalmodel.experiments.collinearita --misura`, **in validazione**
(2021/22, 2022/23, 759 partite), tre semi. Soglie dichiarate prima: selezione
per correlazione assoluta sopra **0.95** (golosa, nell'ordine di BASE), PCA per
blocco di statistica fino al **95%** della varianza. Statistiche stimate solo
sulle stagioni precedenti alla validazione, senza burn-in e senza la stagione
in corso. La selezione toglie esattamente le gemelle strutturali (52 -> 45);
la PCA scende a 31 componenti.

| seme | selezione45 − BASE | pca31 − BASE |
|---|---|---|
| 0 | +0.00009 [-0.00010, +0.00029] | +0.00008 [-0.00035, +0.00050] |
| 1 | -0.00008 [-0.00029, +0.00012] | -0.00005 [-0.00048, +0.00039] |
| 2 | +0.00009 [-0.00012, +0.00030] | +0.00006 [-0.00031, +0.00044] |

**Nessuna delle due si distingue da BASE, e il segno oscilla fra i semi.**
La collinearita' non sta costando niente di misurabile a M5: LightGBM la
assorbe. BASE resta congelato com'e'. Nessun confronto sul test e' stato
consumato.

### Baseline di mercato — B365 con Shin resta, 15 settembre 2026

`python -m goalmodel.experiments.baseline_mercato`, **in validazione** (2021/22,
2022/23): anche scegliere la baseline guardando il test sarebbe una ricerca di
specifica sul test. Funzioni nuove in `market.py` (`devig_power`,
`devig_odds_ratio`, `consenso`, `market_block_alternativo`), fuori dal percorso
di produzione, con non regressione verificata.

**Vincolo sui book**: solo book presenti nello snapshot di produzione. B365
(100% ovunque), BW (buco nel 2024/25, 63%), `Avg` (100% dal 2019/20). Betfair,
BetVictor, Paddy Power e Sky Bet coprono solo le ultime stagioni; Pinnacle e'
morto; `Max` non e' un prezzo de-viggabile.

| candidata − produzione | differenza | IC 95% |
|---|---|---|
| B365 proporzionale | +0.00004 | [-0.00033, +0.00039] |
| B365 potenza | +0.00003 | [-0.00013, +0.00019] |
| B365 odds ratio | +0.00001 | [-0.00005, +0.00006] |
| consenso B365+BW+Avg, Shin | -0.00012 | [-0.00042, +0.00018] |
| consenso, proporzionale | -0.00007 | [-0.00059, +0.00044] |
| consenso, potenza | -0.00010 | [-0.00040, +0.00020] |

**Tutte indistinguibili dalla produzione.** Il metodo di de-vigging quasi non
conta (odds ratio contro Shin: intervallo largo appena ±0.00005); il consenso
fra piu' book ha la stima puntuale migliore ma non si distingue. Con i book
disponibili il venerdi', non esiste una baseline dimostrabilmente piu' forte:
**i confronti fatti finora contro B365/Shin non erano contro un avversario
debole.**

## Ipotesi PRE-REGISTRATE — congelate qui, da testare su dati che ancora non esistono

**A cosa serve questa sezione.** Una specifica scelta dopo aver visto i dati
non e' un risultato, ma non e' nemmeno niente: e' un'ipotesi, e diventa
testabile il giorno in cui arrivano dati nuovi. Scriverla qui, con la data e il
numero osservato, e' cio' che la rende pre-registrata rispetto a quei dati.
**Finche' resta qui non conta come risultato, non promuove niente e non entra
in produzione.** Chi la testera' deve usare la specifica come sta scritta, su
dati mai visti prima — test set cresciuto, o Big 5 — e contarla nel proprio m.

### 1. Blocco B in forma simmetrica — registrata il 15 settembre 2026

**Specifica congelata**: `M5Set(sets=["BASE", "GIOCATORI"], togli=(
"home_quota_minuti_assenti", "away_quota_minuti_assenti"))` contro
`M5Set(sets=["BASE"])` — cioe' il blocco giocatori senza le due colonne
separate di minuti, con la sola `diff_quota_minuti_assenti` al loro posto.
Stima: media di 5 semi (0-4) sui log-lambda. Confronto appaiato, bootstrap a
cluster sulla giornata.

**Osservato oggi** su test 2324-2526 (1140 partite): **-0.00026, IC
[-0.00044, -0.00008], p = 0.0021**; per seme, intervallo sotto zero in 5 su 5.

**Perche' non e' un risultato.** La variante e' nata dal test di simmetria,
cioe' dopo aver visto che l'asimmetria 20:1 andava spiegata. Su questi dati
non la si puo' promuovere a specifica principale: sarebbe scegliere la
formulazione dopo il risultato. Su dati nuovi lo e' a pieno titolo.

**Predizione che la falsifica**: su dati nuovi la differenza deve restare
negativa e dello stesso ordine (fra -0.0002 e -0.0003). Se cambia segno o si
dimezza, il guadagno del blocco B era rumore di questo test set.

### 2. Forma per sede (FORMA_VENUE) — NON entrata: nulla in validazione

Misurata il 16 settembre 2026 e scartata (+0.00001, IC [-0.00035, +0.00039]),
vedi la sezione del blocco D. Resta qui come esempio del funzionamento:
l'elenco si popola solo quando la validazione mostra qualcosa, e in questo caso
la questione si e' chiusa senza spendere un confronto.

## Il ciclo di vita della giornata — due comandi, nessun giorno della settimana

L'unita' di lavoro e' la **giornata di campionato**, non la settimana. Ha uno
stato, e lo stato si deduce dai dati:

| stato | condizione | chi la fa avanzare |
|---|---|---|
| **futura** | nessuna quota disponibile | il tempo |
| **aperta** | quote presenti, partite non ancora giocate | `predict_round` |
| **predetta** | previsioni registrate, tutte o in parte | `predict_round` |
| **giocata** | tutti i risultati disponibili | il tempo |
| **chiusa** | risultati agganciati, errore calcolato, archiviata | `close_round` |

```bash
goalmodel predict-round     # da aperta a predetta
goalmodel close-round       # da giocata a chiusa
goalmodel rounds --status   # dove sta ogni giornata della stagione
```

Opzioni comuni: `--round N` per forzare una giornata invece di dedurla,
`--dry-run`, `--skip-ingest`.

**Perche' non piu' un comando solo.** `weekly` faceva due cose con
precondizioni opposte: predire vuole le quote e nessun risultato, chiudere
vuole tutti i risultati. Una delle due era sempre fuori tempo. E "sabato
mattina" individua la giornata giusta solo finche' il calendario e' regolare —
un infrasettimanale, un rinvio, una partita spostata per la coppa, e non piu'.
`src/goalmodel/prediction/weekly.py` resta come rimando: stampa i due comandi ed esce con codice 2.

**Nessun giorno della settimana compare nel codice.** Nota informativa, non un
vincolo: football-data pubblica le quote il **venerdi' entro le 17:00 UK** per
il weekend e il **martedi' entro le 13:00** per gli infrasettimanali. Lanciare
`predict_round` prima di quei momenti trovera' la giornata ancora `futura`, e
lo dira' esplicitamente. Non e' un errore ed esce con codice 0.

### Il track record si scrive solo da processi locali — regola, non preferenza

**Le uniche cose che scrivono nel registro sono `predict_round` e
`close_round`, lanciati sulla macchina.** Nessuna richiesta HTTP, mai. L'API in
`backend/api/` e' una **vista**: espone solo GET, e un test fallisce all'avvio
se una rotta dichiara un metodo diverso da GET/HEAD/OPTIONS.

**Struttura del monorepo**: `src/` e' la pipeline (produzione), `backend/`
l'API che la legge, `frontend/` la dashboard (React + TypeScript, Vite). La
dipendenza e' a senso unico — `frontend` -> `backend` -> `src`, mai il
contrario — ed e' il motivo per cui spostare l'API fuori da `src/` non ha
richiesto di toccare un solo modulo di produzione.

Il frontend parla con l'API **solo via HTTP e solo in GET**: non importa nulla
di Python e non conosce i percorsi dei file. I tipi in `frontend/src/api/types.ts`
ricalcano `web/openapi.json`, che e' il contratto e va riesportato quando gli
schemi cambiano (`tests/test_api.py` fallisce se diverge).

Il motivo e' lo stesso per cui il registro e' append-only con backup e con due
difese sul calcio d'inizio: **una previsione vale solo se e' stata scritta
prima del fischio**, da un processo che non sapeva il risultato. Un endpoint di
scrittura e' esattamente il modo di perdere quella garanzia — chiunque abbia
l'URL potrebbe aggiungere una riga a partita finita, e a mesi di distanza
nessuno saprebbe distinguerla dalle altre.

Se un giorno servisse far partire una previsione da remoto, **non si aggiunge
un POST**: si fa girare il comando locale (scheduler, o a mano). La
disponibilita' dell'API non e' un problema del track record; la sua integrita'
si'.

Garanzie verificate, non promesse: `backend/api/__init__.py` sostituisce
`to_csv`/`to_parquet` e rifiuta ogni scrittura sotto `track_record/` e `data/`
— lo stesso idioma di `src/experiments/` — e `tests/test_api.py` verifica che
nessuna rotta di scrittura esista e che `lightgbm` non finisca in
`sys.modules`: **l'API non esegue mai modelli.**

### Le selezioni si mostrano, il valore atteso no — regola

Il progetto **mostra le selezioni** su tutti i mercati (`report.selezioni`,
tabella di `predict_round`, sezione del report, `GET /api/selections`), con
probabilita' e **quota equa** accanto. Serve a scegliere cosa giocare sapendo
quanto e' probabile e quanto pagherebbe a valore atteso zero.

**Quello che non si espone mai, da nessuna parte: valore atteso, puntata
consigliata, stake, "value".** Non e' prudenza, e' aritmetica misurata: M1 *e'*
la linea di apertura del book con il margine tolto, quindi l'EV calcolato sulle
sue probabilita' contro quelle stesse quote e' **-5.2% su ogni riga**, e zero
giocate su 3420 risultano positive. Un numero del genere in interfaccia
sarebbe circolare e falso insieme.

**Filtrare per quota sposta la varianza, non il margine.** Vale per la soglia
minima della linea del modello (1.30 dal 22 settembre 2026, prima 1.50: la
tabella della scelta e' accanto a `config.QUOTA_MINIMA_SELEZIONE`) come per la
banda della dashboard (1.20): il margine
del book e' identico su tutti i mercati derivati dalle stesse quote. Misurato:
la doppia chance piu' sicura vince l'80.6% delle volte e rende -2.9%, con
intervallo che esclude lo zero.

**La linea principale e' quella di M1, e non si ritaglia a richiesta.**
`GET /api/picks` non accetta parametri di quota: usa
`config.QUOTA_MINIMA_SELEZIONE` e restituisce le stesse selezioni che
`predict_round` stampa il venerdi' e che il report pubblica. La banda
regolabile (`/api/selections`) e' una lettura **secondaria**, dichiarata tale
anche in pagina. Se una soglia scelta nell'interfaccia potesse ridefinire le
selezioni del modello, il track record misurerebbe una cosa e la dashboard ne
mostrerebbe un'altra — ed e' il modo piu' rapido di rendere insensato tutto il
resto. `tests/test_api.py` verifica che nessun parametro le sposti.

**La "selezione migliore" si ricalcola, non si salva.** Una per partita, la
piu' probabile dentro la banda di quota richiesta. Il registro conserva i due
lambda, e da quelli ogni mercato si ricostruisce esatto: congelare la scelta
significherebbe non poter piu' cambiare il criterio sulle giornate gia' chiuse
— ed e' proprio il criterio la cosa che si vorra' ritoccare. Vale anche per
`predict_round`: il registro resta la distribuzione, mai la giocata.

**La quota del book esiste solo per l'1X2**, ed e' l'unica che il registro
conserva. Per doppia chance, over/under e mercati gol resta vuota: stimarla
applicando un margine medio sarebbe inventare un numero con l'aria di essere
misurato — vale per il report come per l'API.

### `predict_round` — da aperta a predetta

Aggiorna dati e quote, ricostruisce dataset e feature, individua **da solo** la
prima giornata con partite ancora predicibili — con le quote, non in registro,
e con il calcio d'inizio ancora davanti — riaddestra il modello su tutto lo
storico, predice **tutte** le partite di quella giornata (nessun filtro per
squadra: piu' righe nel registro significano stime piu' precise), scrive in
modo idempotente, genera il report e lo apre.

**Giornata coperta a meta'.** Il file delle quote e' una finestra sul turno
imminente: puo' contenerne sei su dieci perche' le altre si giocano piu'
avanti. Si predicono e si registrano le sei, e si dice quali restano scoperte.
Al lancio successivo si completano le mancanti senza duplicare niente. La
giornata resta **predetta in parte** finche' non sono coperte tutte — e se le
mancanti hanno gia' iniziato ci resta per sempre, perche' una previsione
scritta dopo il fischio non e' una previsione. Il comando lo dice invece di
tacere.

### `close_round` — da giocata a chiusa

Aggiorna i risultati, ricostruisce il dataset, individua da solo la prima
giornata predetta e **interamente** giocata, aggancia i risultati veri e la
archivia in `track_record/rounds/round_<stagione>_<NN>.csv`, con previsione,
risultato ed errore di ogni singola partita. Poi rifa' il riepilogo cumulativo
in `track_record/rounds/riepilogo.csv` e rigenera il report.

**I risultati arrivano da una catena di fonti** (`src/risultati.py`, dal 21
settembre 2026): football-data per primo, poi Understat, poi FBref. Il motivo
e' stato misurato: football-data aggiorna il file della stagione un paio di
volte a settimana, e il lunedi' sera della giornata 5 non aveva ancora nessuno
dei dieci risultati, mentre Understat — scaricato dallo stesso comando alla
stessa ora — li aveva tutti. Il ripiego scatta **subito**, appena la fonte
principale manca (scelta esplicita). Il file di giornata registra la
provenienza in `fonte_risultato`.

Quanto ci si puo' fidare: su 4600 partite in entrambe le fonti, football-data e
Understat coincidono nel **99.98%**. L'unica differenza e' Sassuolo-Pescara
2016/17 — 2-1 in campo, 0-3 a tavolino — perche' football-data registra il
risultato **ufficiale** e Understat quello del campo. Per questo
`close_round` chiama `riconcilia()` all'avvio: se una giornata chiusa con un
risultato di ripiego diverge da quello ufficiale pubblicato dopo, lo
**avvisa**, ma non riscrive l'archivio. La catena tocca solo i risultati:
`matches_master`, il dataset del modello, resta costruito da football-data.
Verificato con `tests/test_risultati.py` e con l'impronta di produzione
invariata.

**Una giornata incompleta non si chiude.** Se anche una sola partita non ha il
risultato — posticipo, rinvio — la giornata resta aperta e si riprova al lancio
successivo. Vale anche con `--round` esplicito: chiuderla significherebbe
archiviare un RPS calcolato su nove partite su dieci, e al lancio dopo
risulterebbe gia' chiusa, quindi nessuno tornerebbe a correggerlo.

**Lo stato "chiusa" e' un file, non una colonna.** Una giornata e' chiusa se e
solo se esiste il suo CSV. Non c'e' uno stato scritto da qualche parte che
possa divergere dai fatti, e per questo `track_record/rounds/` e' **versionato**:
in un clone senza quei file tutte le giornate tornerebbero da chiudere.

**`normalize --build` non e' facoltativo nemmeno per chiudere.** `ingest
--stage matches` aggiorna solo `data/raw/matches.parquet`: finche' non si
ricostruisce `matches_master`, i risultati appena scaricati non esistono per il
resto del progetto e l'aggancio non troverebbe niente.

**Il track record non torna indietro sul modello.** Riaddestrare sui risultati
nuovi e' automatico e legittimo — il modello ha piu' dati. Aggiustare il
modello dopo aver visto come e' andato no: `close_round` non tocca niente che
riguardi il modello, scrive soltanto cosa e' successo.

### Cosa vale per entrambi

**Sono idempotenti e lanciabili in qualsiasi momento.** Rilanciarli non sporca
niente: una partita gia' registrata viene saltata e dichiarato, una giornata
gia' chiusa non viene riscritta. Se non c'e' niente da fare escono con codice
**0** e un messaggio che nomina la giornata e il suo stato — non in silenzio,
che non distinguerebbe "tutto a posto" da "non ho trovato i dati".

**Finiscono con due righe**, `FATTO` e `IN SOSPESO`, leggibili senza scorrere
il log.

**Se le quote cambiano, la riga vecchia non si aggiorna.** Falsificherebbe il
track record a posteriori con informazione che al momento della previsione non
c'era. Per una seconda opinione si cambia `model_version`: la chiave di
deduplicazione e' (partita, modello), quindi la riga nuova entra e la vecchia
resta. Garantito da `tests/test_predictions_log.py`.

**Quali passi sono fatali.** I passi di rete no: football-data risponde 503
piu' spesso di quanto dovrebbe, e restare senza previsione perche' un sito era
giu' dieci minuti sarebbe il modo peggiore di fallire — si prosegue con i dati
presenti, e la vecchiaia dello snapshot viene comunque segnalata. I passi
locali (dataset, feature, previsione) sono fatali: su dati incoerenti qualsiasi
previsione sarebbe sbagliata in silenzio.

**Il report va su file, non solo a schermo.** Lo scrive `src/goalmodel/reporting/report.py`, in
coda a entrambi i comandi: `track_record/report.html` a percorso fisso, piu'
una copia d'archivio in
`data/processed/reports/giornata_<stagione>_<NN>.html`. Una per giornata e non
una sola sovrascritta: riaprire il report di tre turni fa e' esattamente cio'
che serve per capire come sono andate le previsioni. Resta comunque una
**vista** — il dato e' il registro, il report si rigenera con
`goalmodel report` e per questo non e' versionato.

**Il registro e' l'unico dato che non si rigenera.** Tutto il resto si
riscarica; le previsioni no, perche' vanno scritte prima del calcio d'inizio e
quel momento non torna. Per questo `append_log` fa una copia in
`predictions_log.csv.bak` prima di ogni scrittura e verifica che il file non si
sia accorciato.

**Due difese sul calcio d'inizio, non una.** L'assert in `predict.py` impedisce
di registrare una previsione tardiva; `backtest_log.flag_post_kickoff` la
riconosce e la esclude anche se l'assert ha sbagliato. Serve la seconda perche'
la prima ha gia' fallito una volta, per un errore di fuso orario (vedi sopra):
le righe non valide restano nel registro — che e' append-only e deve conservare
anche gli errori — ma non entrano mai nelle metriche. Il file di giornata le
conserva con `valida=False`: le toglie dalle medie, non dalla storia.

**La previsione va fatta PRIMA che si giochi**, ed e' l'unico passaggio non
recuperabile. `predict.py` verifica con un assert che il timestamp UTC preceda
il calcio d'inizio di ogni partita registrata: se il registro non e' scritto in
tempo, quella giornata e' persa per sempre ai fini del track record.
Ricostruirla dopo con `--as-of` produce un file separato
(`predictions_backfill.csv`) che **non** e' un track record e non va mescolato.

I comandi dei singoli passi, per quando serve lanciarli a mano, stanno in
**`COMANDI.md`**.

### Le quote delle partite in arrivo — nota metodologica, non un dettaglio

`--stage fixtures` scarica `football-data.co.uk/fixtures.csv`, che contiene le
partite del turno imminente di tutti i campionati coperti, con le quote di
apertura. Filtra su `Div` (Serie A = `I1`), rinomina sullo schema di
`matches.parquet` e salva in `data/raw/fixtures_odds.parquet` con il
**timestamp di download**.

**Perche' proprio quella fonte e non un'altra.** Quelle quote sono raccolte il
**venerdi' entro le 17:00 UK** per le partite del weekend e il **martedi'
entro le 13:00** per gli infrasettimanali. E' lo stesso identico criterio con
cui sono state raccolte le quote di apertura dello storico su cui il modello
e' stato addestrato e valutato. La corrispondenza fra addestramento e
produzione e' garantita da questo, non da altro.

Prendere le quote da un altro book, da un aggregatore, o dallo stesso book in
un altro momento della settimana romperebbe quella corrispondenza in modo
invisibile: i numeri sarebbero plausibili, il codice non protesterebbe, e
l'RPS in produzione divergerebbe da 0.188 senza che si capisca perche'. Il
drift apertura-chiusura misurato ha deviazione standard di ~3 punti di
probabilita': e' l'ordine di grandezza dell'errore che si introdurrebbe.

**Il file e' una finestra, non un archivio.** Contiene solo il turno imminente
e viene sovrascritto. Se non lo si scarica in tempo, quelle quote da li' non
si recuperano piu'. Da qui l'avviso quando lo snapshot ha piu' di
`config.FIXTURES_MAX_AGE_DAYS` giorni, e il messaggio esplicito quando la
giornata richiesta non e' coperta — chiedere la giornata 12 a settembre non
produce un errore, produce zero quote, e senza messaggio si cercherebbe il
problema nel posto sbagliato.

**Lo snapshot VUOTO e' lo stato normale fra un turno e l'altro, e faceva
cadere tutto.** Quando football-data risponde ma la Serie A non e' ancora
pubblicata — mercoledi', per dire — `--stage fixtures` riscrive il file con
**zero righe** e `downloaded_at` resta NaT. `load_fixtures_odds` ci faceva
`strftime` sopra: `ValueError: NaTType does not support strftime`. Non cadeva
solo la previsione: `attach_odds` sta anche dentro `rounds --status` e
`close_round`, quindi il 16 settembre 2026 nessuno dei tre partiva. Corretto
con una guardia su `pd.notna(scaricato)` piu' un messaggio esplicito: un turno
non ancora pubblicato non e' un errore e lo deve dire. `predict.py` e'
produzione, quindi la correzione e' stata verificata con
`tests/test_production_unchanged.py` — M1 identico bit a bit, impronta
invariata. **Il bug non si vede il venerdi'**, quando lo snapshot e' pieno: si
vede solo quando il file c'e' ed e' vuoto, ed e' il motivo per cui era rimasto
li'.

**Ripiego manuale.** `manual/upcoming_odds.csv` resta come rete di sicurezza
per le partite che lo snapshot non copre o quando il sito e' giu' (succede:
risponde 503 su tutto il dominio). Stesse colonne, precedenza piu' bassa.
Nel registro `odds_source` distingue le due provenienze — `B365-fixtures`
contro `B365-manual` — perche' la prima e' lo snapshot ufficiale di un momento
noto e la seconda una quota copiata a mano in un momento ignoto: a mesi di
distanza la differenza serve a interpretare il track record.

## Comandi

Sono tutti in **`COMANDI.md`**, con tempi di esecuzione misurati, file
prodotti da ciascuno, e la lista delle cose da non fare. Non duplicarli qui:
due elenchi divergono, e quello sbagliato viene sempre letto per primo.

### L'ambiente: DUE piattaforme, non una

Il progetto si sviluppa su **Linux** (sviluppatore principale, `.venv` nella
radice del repository) e su **Windows con PowerShell** (secondo sviluppatore).
Nessuna delle due e' "quella giusta": il codice deve funzionare su entrambe, e
la CI le prova tutte e due proprio per questo.

Cosa ne segue, in pratica:

- **Niente comandi di shell nel codice.** Un `rm -f` o un `Remove-Item` dentro
  un modulo lo lega a una piattaforma. Si usa `pathlib` e `shutil`.
- **Niente percorsi con la barra scritta a mano.** Sempre `Path` e `/`, mai
  `"data\\raw"` ne' `"data/raw"` come stringa.
- **Quando scrivi un comando nella documentazione**, preferisci la forma
  `goalmodel <comando>`: e' identica sulle due piattaforme e non dipende dalla
  directory corrente. `python -m goalmodel.<modulo>` funziona comunque.
- **Su PowerShell** per cancellare file usare `Remove-Item ... -ErrorAction
  SilentlyContinue`, non `rm -f`. Evitare `python -c "..."` con apici
  annidati: mettere il codice in un file.
- **Un test che passa su una sola piattaforma blocca meta' del team.** Prima
  della CI non c'era modo di accorgersene; ora si vede nel job che fallisce.

## Note operative

- **soccerdata riscarica automaticamente la stagione in corso** e usa la cache
  per quelle concluse (`_is_complete()` + `no_cache=True`). Dopo ogni giornata
  basta rilanciare gli stessi comandi: nessun aggiornamento manuale.
- `make_fixtures` scrive in `data/raw` con gli stessi nomi dei dati veri.
  Svuota la cartella prima di un'ingestion reale.
- Understat scarica una libreria TLS da GitHub al primo avvio. Dietro
  proxy fallisce: scaricarla dalle release di `bogdanfinn/tls-client`.
- La copertura del join dev'essere sopra il 95%. Sotto, mancano voci in
  `manual/team_name_map.json`.
- FBref non ha statistiche avanzate prima del 2017/18. Understat parte dal
  2014/15 ed e' la fonte primaria di xG e PPDA.

## Stile del codice

- Type hints, docstring in italiano che spiegano il **perche'**, non il cosa
- Ogni modulo eseguibile con un `argparse`, raggiungibile sia come
  `python -m goalmodel.<percorso>` sia come sottocomando di `goalmodel`
  (registrarlo in `src/goalmodel/cli.py`, una riga)
- Parquet per tutti i dati intermedi
- Niente notebook nel codice di produzione: solo esplorazione in `notebooks/`
- Log via `logging`, non `print`

## Principi di progettazione — DRY e SOLID

Non sono decorazione: qui hanno conseguenze specifiche, ed e' quello che va
ricordato. La diagnosi completa dello stato attuale sta in
`docs/AUDIT_TECNICO.md`.

### DRY — un fatto, un posto solo

- **Una costante condivisa sta in `src/goalmodel/config.py`**, non ricopiata. La chiave
  di join e' la regola non negoziabile n.5: se e' scritta in quattordici file,
  allargare ai Big 5 significa modificarne quattordici e **dimenticarne uno non
  da' errore** — da' un merge che perde righe in silenzio. Stessa cosa per
  soglie, half-life, stagioni, percorsi.
- **Un percorso di file si legge da un punto solo.** `read_parquet` sparso per
  i moduli significa che ognuno ripete percorso, gestione dell'assenza e
  conversione delle date, e che cambiare formato tocca sei punti.
- **Copiare una riga da `evaluate.py` dentro un esperimento e' il modo tipico
  in cui la produzione viene corrotta.** E' successo abbastanza da meritare una
  guardia a runtime (`experiments.proteggi_produzione()`). Se serve la stessa
  logica in due posti, si estrae una funzione.
- **Eccezione dichiarata: la specifica congelata si RICOPIA di proposito.**
  `features/sets.py` scrive BASE per esteso invece di importarlo da `form.py`,
  perche' un set congelato non deve cambiare quando cambia il modulo che lo
  produce. Duplicare un *valore storico* e' giusto; duplicare una *regola
  viva* no.

### SOLID — dove morde in questo progetto

- **S (responsabilita' singola).** Un modulo che calcola non impagina. Se un
  modulo di presentazione addestra un modello, un errore di modellazione si
  manifesta come sezione mancante nel report, e si cerca nel posto sbagliato.
- **O (aperto/chiuso).** **Questa e' la regola di isolamento della produzione,
  scritta in forma di principio**: un esperimento non modifica `models/gbm.py`
  ne' `predict.py`, li **sottoclassa** (`M5Set`, `M5MediaSemi`). Modificare la
  classe madre cambierebbe anche il report, che e' produzione, senza che
  nessuno lo decida.
- **L (sostituzione).** Ogni modello rispetta `fit(train)` / `predict(test)` e
  restituisce `PRED_COLS` sempre. `walk_forward` non deve sapere chi sta
  valutando: se un modello ha bisogno di un trattamento speciale
  nell'harness, il modello e' sbagliato, non l'harness.
- **I (interfacce ristrette).** L'interfaccia dei modelli e' due metodi. Non
  aggiungerne un terzo per comodita' di un modello solo.
- **D (dipendenza dalle astrazioni).** Un modulo di `src/` non importa uno
  script della cartella di lavoro: la dipendenza deve andare verso il
  pacchetto, mai verso l'ambiente in cui il comando e' stato lanciato.

### Regola di taglio

Prima di aggiungere un'astrazione: **serve adesso o servira'?** Con ~3400
righe di training e un solo campionato, un'interfaccia con una sola
implementazione e una configurazione per un valore che non cambia mai sono
costo senza ricavo. La stessa disciplina che vale per le feature — un blocco
non misurato non entra — vale per il codice.

## Sicurezza — regole di codice

**QUESTA PREMESSA E' CAMBIATA IL 22 SETTEMBRE 2026.** Fino a ieri era vero
che il progetto non avesse utenti ne' superficie di rete in ingresso. Con
`backend/api/` esiste un servizio HTTP, e da qui in poi le regole sotto non
bastano piu' da sole: valgono ancora tutte, ma sopra di esse c'e' una
superficie esposta.

Cosa regge oggi quella superficie, e va saputo prima di toccarla:
`_assert_read_only_routes()` fallisce **all'avvio** se una rotta dichiara un
metodo diverso da GET/HEAD/OPTIONS, e `_guard_writes()` sostituisce
`to_csv`/`to_parquet` e rifiuta ogni scrittura sotto `track_record/` e `data/`
— lo stesso idioma di `experiments.proteggi_produzione()`, con gli stessi
limiti dichiarati. Nessun modello viene mai caricato: gli import pesanti stanno
dentro le funzioni, e `tests/test_api.py` lo verifica **in un sottoprocesso**,
perche' su `sys.modules` condiviso la risposta sarebbe quella della sessione di
test, non quella dell'API.

Il giorno in cui arrivano autenticazione e scritture, quella garanzia va
**ristretta al registro di proposito**, non persa per distrazione: una
previsione vale solo se scritta prima del fischio da un processo locale, e un
endpoint di scrittura e' esattamente il modo in cui quella garanzia si perde.

Il resto della sicurezza qui resta **integrita' dei dati e del registro**. Le
regole sotto sono quelle che mordono davvero.

1. **Nessun segreto nel repository.** Niente chiavi, token, credenziali,
   nemmeno in un commento o in un file di esempio. Se un giorno servira'
   un'API a pagamento: variabile d'ambiente, e il nome della variabile
   documentato in `COMANDI.md`.

2. **Un `assert` non e' un controllo di sicurezza.** `python -O` li cancella
   tutti. Tutto cio' che protegge il registro, il de-vigging o l'ordine
   previsione/fischio d'inizio deve essere `if not cond: raise ValueError(...)`.
   Gli assert vanno bene nei `_demo()` e nei test, dove il codice non gira mai
   ottimizzato.

3. **Ogni dato che arriva dalla rete e' ostile finche' non e' verificato.**
   Un CSV scaricato va accettato solo dopo: tetto alla dimensione della
   risposta, controllo del content-type, e verifica delle colonne attese.
   `encoding="latin-1"` non fallisce **mai** sulla decodifica: una pagina di
   errore HTML entra come dati validi, e senza il controllo colonne finisce in
   parquet.

4. **Mai `shell=True`, mai `eval`, mai `exec`, mai `pickle` su dati esterni.**
   `subprocess` solo nella forma a lista, con `sys.executable`. Oggi il
   progetto e' pulito su tutti e cinque i punti: va tenuto cosi'.

5. **Escaping con la libreria standard.** In HTML si usa
   `html.escape(s, quote=True)`, su **tutte** le interpolazioni di valori, non
   solo su quelle che sembrano rischiose. Una funzione di escaping fatta in
   casa e applicata a macchia di leopardo e' la premessa standard di una XSS il
   giorno in cui il report smette di essere un file locale.

6. **Le eccezioni si catturano strette.** `except Exception` in un passo di
   rete traveste un `TypeError` del proprio codice da sito irraggiungibile, e
   il comando prosegue "con i dati gia' presenti" su una premessa falsa.
   Catturare `(ConnectionError, HTTPError, TimeoutError, OSError)`.

7. **File e processi con context manager.** `with open(...)`, sempre. Un file
   di log aperto a mano in un ciclo di esperimenti resta aperto alla prima
   eccezione.

8. **Il registro delle previsioni e' append-only, e non e' negoziabile.** Non
   si riscrive, non si aggiorna una riga vecchia, non si toglie una previsione
   sbagliata. Una copia `.bak` prima di ogni scrittura e un controllo che il
   file non si sia accorciato. E' l'unico dato del progetto che non si
   rigenera.

9. **Una guardia parziale va dichiarata parziale.**
   `experiments.proteggi_produzione()` copre `to_parquet` e `to_csv`, non
   `to_pickle`, `open()`, `shutil.copy` o `pyarrow`. Va detto nel docstring:
   una protezione sopravvalutata e' peggio di nessuna protezione, perche'
   smette di far pensare.

10. **Le dipendenze si fissano a versione esatta.** Non e' burocrazia: il
    progetto verifica `test_production_unchanged` **bit a bit**, e un
    aggiornamento silenzioso di LightGBM o scipy fa fallire quel test senza che
    il codice sia cambiato — rendendo indistinguibile una regressione vera da
    un aggiornamento di libreria.

## Cosa manca dall'utente

**Aggiornato l'8 settembre 2026.**

- `manual/derbies.csv` — **C'E', MA VA RIVISTO.** 55 coppie compilate durante
  il blocco A perche' senza il file il derby non era misurabile: 5 `city`,
  42 `regional`, 8 `rivalry`, che agganciano 503 partite su 4580 (11.0%).
  **Non e' una fonte, e' un'opinione plausibile**: le coppie `regional` sono
  generose (mezza Lombardia con mezza Lombardia) e le `rivalry` sono
  discrezionali. Se il blocco A dovesse dipendere da questa colonna, il primo
  posto dove guardare e' questo file. La coppia e' trattata come NON ordinata,
  `tuple(sorted([casa, trasferta]))`, e `context.py` funziona anche senza il
  file: in quel caso le due colonne del derby non vengono prodotte, invece di
  essere messe a zero — assente e falso non sono la stessa cosa
- `manual/coach_changes.csv` — **MANCANTE**. Colonne `league, season, team,
  date, coach_out, coach_in`. ~150 righe per la Serie A degli ultimi 10 anni
- `manual/upcoming_odds.csv` — FACOLTATIVO, e' solo il ripiego. Le quote
  arrivano da `goalmodel ingest --stage fixtures`. Serve compilarlo a mano
  soltanto per le partite che lo snapshot non copre o quando football-data e'
  irraggiungibile. Colonne `league, season, home_team, away_team, B365H,
  B365D, B365A, B365>2.5, B365<2.5`: servono **entrambi** i mercati, perche' i 
  gol attesi nascono dall'incrocio fra supremazia (1X2) e totale (over/under)
- `manual/team_name_map.json` — GIA FATTO, 10 voci. Le ultime due
  (`Hellas Verona`, `SPAL`) aggiunte a mano per agganciare `fbref_schedule`,
  che usa nomi diversi da quelli gia' mappati (`Hellas Verona FC`,
  `SPAL 2013`). Senza quelle due la giornata si agganciava solo all'89%  