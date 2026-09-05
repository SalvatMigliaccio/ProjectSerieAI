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
- `tests/make_fixtures.py`, `tests/test_form.py` — dati sintetici e test

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

`weekly` mostra le selezioni piu' probabili con la **quota equa** accanto
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

### Il test set non ha la potenza per misurare il layer giocatori

`python -m src.power_analysis`, con la deviazione standard della differenza
appaiata gia' misurata (0.00354 per cluster, su 114 cluster osservati):

| perimetro | cluster | n | MDE (80%, 5%) | effetto atteso |
|---|---|---|---|---|
| Serie A | giornata = settimana | 114 | **0.00240** | 0.00060 |
| Big 5 | giornata di campionato | 570 | **0.00107** | 0.00060 |
| Big 5 | settimana di calendario | 114 | **0.00240** | 0.00060 |

**Nessuno dei due perimetri basta.** Il minimo rilevabile e' 4 volte l'effetto
atteso in Serie A, e ancora 1.8 volte con i Big 5 nell'ipotesi ottimistica.

**La definizione di cluster cambia il risultato di sqrt(5).** Se ogni lega ha
un modello proprio, i Big 5 danno 5 volte i cluster (giornata di campionato).
Se il modello e' unico — ed e' il caso, `config.LEAGUES` e' una lista sola —
le giornate della stessa settimana condividono lo stesso addestramento e
contano per **un** cluster: i Big 5 non aggiungono potenza, aggiungono solo
partite dentro gli stessi 114 cluster.

Quanto servirebbe, per dimensione dell'effetto:

| shift di lambda | effetto RPS | cluster necessari | stagioni Serie A |
|---|---|---|---|
| 0.05 gol | 0.00015 | 28 974 | 762 |
| **0.10 gol** | **0.00060** | **1 839** | **48** |
| 0.15 gol | 0.00127 | 410 | 11 |
| 0.20 gol | 0.00196 | 171 | 4.5 |
| 0.30 gol | 0.00522 | 24 | 0.6 |

Il ginocchio della curva sta fra 0.15 e 0.20 gol. **Sotto quella soglia
l'effetto non e' dimostrabile con i dati che questo progetto potra' mai
avere**; sopra, bastano poche stagioni di Serie A.

### Da fare, in ordine

1. **Layer giocatori e infortuni** — ma prima decidere se ha senso misurarlo.
   Il calcolo di potenza dice che un effetto da 0.10 gol richiederebbe 48
   stagioni di Serie A, e che i Big 5 non aiutano se il modello resta unico.
   Le opzioni oneste sono tre:
   - **restringere il campo alle assenze grosse** (portiere titolare, o oltre
     il 30% dei minuti pesati): meno partite ma effetto piu' grande, e il
     ginocchio della curva e' li';
   - **un modello per lega**, che rende i Big 5 davvero 5 volte i cluster;
   - **costruirlo comunque senza pretendere di dimostrarlo**, dichiarandolo.

   Quando si fara', il modo giusto di misurarlo e' **rifare M5**: l'ancoraggio
   al mercato e' il test piu' potente disponibile e l'infrastruttura c'e'.
   Non ripartire da M4
2. `src/features/context.py` — giorni di riposo, congestione, coppe europee,
   derby e cambi allenatore. **Entrambi i file manuali MANCANO**: `derbies.csv`
   e `coach_changes.csv` non esistono in `manual/`, che contiene solo
   `team_name_map.json` e `upcoming_odds.csv`
3. `src/features/team_strength.py` — Elo proprio calcolato dai risultati.
   Scende di priorita': M2, M3 e M4 sono gia' indistinguibili fra loro, e un
   quarto modo di misurare la forza della squadra non cambiera' il quadro

## Ciclo settimanale — un comando solo

```bash
python -m src.weekly
```

`src/weekly.py` fa tutto in sequenza: aggiorna i dati, scarica le quote,
ricostruisce il dataset e le feature, deduce da solo la prossima giornata, la
predice **tutta** (nessun filtro per squadra: piu' righe nel registro
significano stime piu' precise) e aggiorna il track record.

**Quando lanciarlo: sabato mattina** per il weekend, **mercoledi' mattina**
per gli infrasettimanali. Cioe' dopo che football-data ha caricato lo snapshot
quote (venerdi' 17:00 UK, martedi' 13:00 UK) e prima del primo calcio
d'inizio.

**E' idempotente**: rilanciarlo tre volte nella stessa settimana non sporca il
registro. Una partita gia' prevista dallo stesso modello viene saltata e
dichiarata. Se non c'e' niente da fare — lunedi', o secondo lancio — esce con
codice **0** e un messaggio esplicito, non con un errore.

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

**Il report va su file, non solo a schermo.** Ogni ciclo scrive
`data/processed/reports/giornata_<stagione>_<NN>.html`, uno per giornata, piu'
una copia in `ultimo.html` a percorso fisso. Uno per giornata e non uno solo
sovrascritto: riaprire il report di tre turni fa e' esattamente cio' che serve
per capire come sono andate le previsioni. Resta comunque una **vista** —
il dato e' il registro, e il report si rigenera.

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
anche gli errori — ma non entrano mai nelle metriche.

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

**Verificato il 5 settembre 2026**: in `manual/` ci sono solo
`team_name_map.json`, `team_name_map_suggested.json` e `upcoming_odds.csv`.
Una versione precedente di questo documento dava `derbies.csv` per fatto: non
c'e'. Chi scrive `context.py` non deve darlo per scontato.

- `manual/derbies.csv` — **MANCANTE**. Colonne `home_team, away_team,
  intensity` con intensity in (city/regional/rivalry). ~48 coppie per la
  Serie A. La coppia va trattata come NON ordinata:
  `tuple(sorted([casa, trasferta]))`
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