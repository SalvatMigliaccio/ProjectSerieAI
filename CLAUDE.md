# CLAUDE.md

Contesto operativo per Claude Code.

| documento | cosa contiene |
|---|---|
| `PROGETTO_SERIE_A.md` | progettazione completa e razionale di fondo |
| **questo file** | decisioni prese, risultati misurati, il **perche'** |
| **`COMANDI.md`** | come si lancia qualsiasi cosa: il **come** |

I comandi vivono solo in `COMANDI.md`. Quando ne cambia uno si aggiorna li',
non qui: due elenchi divergono e viene sempre letto quello sbagliato.

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
  (`python -m src.features.market --coverage`):
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
  generale.** Verificata fuori campione (`python -m src.evaluate --bias`):
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
    "Fixtures" che non esiste. Rimedio: `src/whoscored_patch.py`, che cerca
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

- `ingest.py` — download multi-fonte. Legge leghe e stagioni da `src/config.py`.
  Fail-fast dopo 3 errori consecutivi su ClubElo.
  Lo stage `fixtures` scarica le quote del turno imminente: filtra su `Div`
  (`config.FOOTBALL_DATA_DIV`), deduce la stagione dalla data (stacco a
  luglio, non da `CURRENT_SEASON` che andrebbe aggiornato a mano ogni agosto),
  applica `team_name_map.json` e **avvisa nominando le squadre non
  riconosciute** — un nome non mappato non darebbe errore, farebbe sparire la
  partita dalla previsione in silenzio
- `src/config.py` — percorsi, leghe, stagioni, iperparametri
- `src/normalize.py` — mapping nomi squadra tramite **assegnamento bipartito**
  (`scipy.optimize.linear_sum_assignment`), non fuzzy matching greedy. Risolve
  per esclusione i casi che difflib non trova, come Internazionale -> Inter.
  `--report --apply` scrive la mappa automaticamente sopra confidenza 0.75
- `src/features/form.py` — medie mobili esponenziali leakage-safe, continue
  attraverso le stagioni con attenuazione del 30% al confine
- `src/features/market.py` — de-vigging proporzionale e di Shin su 1X2 e
  over/under 2.5, riferimento `B365` in **apertura**. Produce anche il
  benchmark market-only sulla scala dei gol (`mkt_lambda_home/away`,
  ottenuti invertendo un Poisson indipendente su totale e supremazia).
  `data/processed/features_market.parquet`: 4580 righe, 35 colonne, 100% di
  copertura su tutte le stagioni. Verificato contro `brentq` scalare
  (scarto 9e-16) e contro i risultati veri: over 2.5 atteso 0.515 contro
  0.520 reale, gol totali 2.75 contro 2.72, gol casa 1.51 contro 1.48.
  Usare `FEATURES_T24`, mai `FEATURES_CLOSING`
- `src/evaluate.py` — RPS, log loss, Brier, accuratezza, curve di calibrazione
  ed ECE, harness di walk-forward per giornata. **Il ciclo di valutazione e'
  stato costruito prima delle altre feature**, di proposito: ogni feature
  successiva si misura sullo stesso test set invece di accumularsi non
  validata
- `src/models/baseline.py` — M0/M0b/M1/M1b/M2 piu' la funzione condivisa
  `score_matrix` (lambda -> matrice dei risultati esatti -> 1X2 e over/under)
  con correzione Dixon-Coles opzionale, spenta di default. `valid_rho_floor`
  da il rho minimo ammissibile: la correzione DC non e' valida per ogni rho,
  serve rho > -1/max(lam, mu) o escono probabilita' negative
- `src/models/dixon_coles.py` — M3, decadimento temporale esponenziale e rho
  stimato **insieme** ad attacchi e difese, non fissato a priori. Gradiente
  analitico (verificato a 1.8e-8 relativo contro differenza finita centrata:
  con la differenza in avanti di `approx_fprime` l'errore di troncamento e'
  1e-4 e sembra un bug del gradiente). Un fit costa 25-70 ms
- `src/models/gbm.py` — M4 (LightGBM Poisson a due gol separati, con e senza
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
- `src/predict.py` — inferenza settimanale. Modello **parametrico**, con M1
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
- `src/backtest_log.py` — rilegge `predictions_log.csv`, aggancia i risultati
  veri sulla quadrupla e calcola RPS e calibrazione **solo** sulle previsioni
  scritte prima del calcio d'inizio. Quando ci sono piu' righe per la stessa
  partita usa la PRIMA per timestamp: il registro e' append-only e la piu'
  recente sarebbe anche la piu' informata. Ricalcola anche l'RPS delle quote
  registrate, che e' il motivo per cui vanno salvate: a mesi di distanza
  distingue un errore del modello da un prezzo cambiato
- `src/features/context.py` — **blocco A**: riposo, congestione su finestra
  (d-14, d) aperta da entrambi i lati, infrasettimanale, derby da
  `manual/derbies.csv` con coppia NON ordinata. Solo calendario, nessuna
  ingestion nuova. **Le coppe europee non ci sono e non ci possono essere**:
  `fbref_schedule` contiene la sola Serie A, quindi una partita di Champions
  del martedi' non compare da nessuna parte. Le due scorciatoie (dedurre chi
  gioca in Europa dalla classifica dell'anno prima; usare l'orario di calcio
  d'inizio come indizio) sono peggio del buco — la prima e' una funzione dei
  risultati passati, cioe' proprio cio' che il piano esclude
- `src/report.py` — il report settimanale in HTML statico: CSS dentro il file,
  grafici in SVG generato a mano, nessun CDN e nessun framework. Cinque
  sezioni: giornata in arrivo, cosa e' cambiato rispetto all'ultima previsione
  di quelle squadre, divergenza fra M4-senza-mercato e M1 (**diagnostica, non
  segnale di scommessa**), track record con la linea del backtest e la stima di
  quante previsioni servono ancora, stato del sistema. Lo chiamano in coda i
  due comandi di giornata, ma gira anche da solo con `python -m src.report
  --open` — e in
  quel caso **non tocca il registro**, e il report lo dichiara.
  La sezione 3 addestra M4 e controlla che non sia degenerato: con le feature
  di forma nulle LightGBM si ferma a un albero e prevede la stessa cosa per
  tutte le partite, senza sollevare niente
- `src/rounds.py` — il ciclo di vita della giornata e la tabella di stato. E'
  qui che si decide su quale giornata agire, guardando quote, registro e
  risultati: nessun comando chiede il numero e nessuno sa che giorno e'.
  Lo stato CHIUSA e' l'esistenza del file di archivio, non una colonna che
  potrebbe divergere dai fatti
- `src/predict_round.py` — porta una giornata da aperta a predetta. Gestisce la
  giornata coperta a meta' (lo snapshot copre solo il turno imminente):
  registra quelle che puo', nomina quelle che restano, e al lancio dopo
  completa senza duplicare
- `src/close_round.py` — porta una giornata da giocata a chiusa. Non chiude una
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

`python -m src.models.gbm --importance`, guadagno medio su 3 stagioni x 2 lati,
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
  `python -m src.models.dixon_coles --tune` dopo ogni nuovo blocco di feature.

### Potenza: cosa questo test set puo' vedere — rifatto due volte

`python -m src.power_analysis`. Tre errori corretti, e ognuno cambiava la
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

`python -m src.evaluate --blocco contesto`, 8 settembre 2026. Nove colonne:
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

`python ingest.py --stage cups` scarica Champions, Europa e Conference League
da FBref: **solo calendario, una richiesta per competizione e stagione, niente
browser** (4140 partite). Le tre coppe sono chiavi nuove in `LEAGUE_DICT`,
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

#### Blocco B — giocatori e infortuni — IL PROSSIMO

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
sull'RPS. Ritarare con `python -m src.models.dixon_coles --tune`.

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

`src/features/team_strength.py` — Elo proprio calcolato dai risultati. Scende
di priorita': M2, M3 e M4 sono gia' indistinguibili fra loro, e un quarto modo
di misurare la forza della squadra non cambierebbe il quadro.

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
python -m src.predict_round     # da aperta a predetta
python -m src.close_round       # da giocata a chiusa
python -m src.rounds --status   # dove sta ogni giornata della stagione
```

Opzioni comuni: `--round N` per forzare una giornata invece di dedurla,
`--dry-run`, `--skip-ingest`.

**Perche' non piu' un comando solo.** `weekly` faceva due cose con
precondizioni opposte: predire vuole le quote e nessun risultato, chiudere
vuole tutti i risultati. Una delle due era sempre fuori tempo. E "sabato
mattina" individua la giornata giusta solo finche' il calendario e' regolare —
un infrasettimanale, un rinvio, una partita spostata per la coppa, e non piu'.
`src/weekly.py` resta come rimando: stampa i due comandi ed esce con codice 2.

**Nessun giorno della settimana compare nel codice.** Nota informativa, non un
vincolo: football-data pubblica le quote il **venerdi' entro le 17:00 UK** per
il weekend e il **martedi' entro le 13:00** per gli infrasettimanali. Lanciare
`predict_round` prima di quei momenti trovera' la giornata ancora `futura`, e
lo dira' esplicitamente. Non e' un errore ed esce con codice 0.

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

**Il report va su file, non solo a schermo.** Lo scrive `src/report.py`, in
coda a entrambi i comandi: `track_record/report.html` a percorso fisso, piu'
una copia d'archivio in
`data/processed/reports/giornata_<stagione>_<NN>.html`. Una per giornata e non
una sola sovrascritta: riaprire il report di tre turni fa e' esattamente cio'
che serve per capire come sono andate le previsioni. Resta comunque una
**vista** — il dato e' il registro, il report si rigenera con
`python -m src.report` e per questo non e' versionato.

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

Regola dell'ambiente, che vale ovunque: si sviluppa su **Windows con
PowerShell**. Per cancellare file usare `Remove-Item ... -ErrorAction
SilentlyContinue`, non `rm -f`. Evitare `python -c "..."` con apici annidati:
mettere il codice in un file.

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
- Ogni modulo eseguibile con `python -m src.<modulo>` e un argparse
- Parquet per tutti i dati intermedi
- Niente notebook nel codice di produzione: solo esplorazione in `notebooks/`
- Log via `logging`, non `print`

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
  arrivano da `python ingest.py --stage fixtures`. Serve compilarlo a mano
  soltanto per le partite che lo snapshot non copre o quando football-data e'
  irraggiungibile. Colonne `league, season, home_team, away_team, B365H,
  B365D, B365A, B365>2.5, B365<2.5`: servono **entrambi** i mercati, perche' i 
  gol attesi nascono dall'incrocio fra supremazia (1X2) e totale (over/under)
- `manual/team_name_map.json` — GIA FATTO, 10 voci. Le ultime due
  (`Hellas Verona`, `SPAL`) aggiunte a mano per agganciare `fbref_schedule`,
  che usa nomi diversi da quelli gia' mappati (`Hellas Verona FC`,
  `SPAL 2013`). Senza quelle due la giornata si agganciava solo all'89%  