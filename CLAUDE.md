# CLAUDE.md

Contesto operativo per Claude Code. Il documento completo di progettazione e'
`PROGETTO_SERIE_A.md`: leggilo per il razionale, questo file e' l'operativo.

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
  Fail-fast dopo 3 errori consecutivi su ClubElo
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
  con correzione Dixon-Coles opzionale, spenta di default
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

| modello | RPS | log loss | Brier | accur. | ECE |
|---|---|---|---|---|---|
| M1b market-only diretto | **0.1881** | 0.967 | 0.576 | 0.540 | 0.022 |
| M1 market-only via lambda | 0.1882 | 0.968 | 0.577 | 0.541 | 0.027 |
| M2 GLM Poisson | 0.1969 | 0.994 | 0.594 | 0.520 | 0.017 |
| M0b frequenze di base | 0.2291 | 1.090 | 0.661 | 0.402 | 0.024 |
| M0 sempre casa | 0.4583 | inf | 1.197 | 0.402 | 0.399 |

**Soglia da battere: RPS 0.1881.**

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

### Da fare, in ordine

1. `src/features/team_strength.py` — Elo proprio calcolato dai risultati
2. `src/features/context.py` — giorni di riposo, congestione, coppe europee,
   derby (`manual/derbies.csv` e' pronto), cambi allenatore
   (`manual/coach_changes.csv` e' ancora un template vuoto)
3. `src/models/dixon_coles.py` — modello a gol con decadimento temporale.
   `score_matrix` accetta gia' rho: manca solo stimarlo. Attenzione al segno,
   e' rho **negativo** ad alzare 0-0 e 1-1
4. `src/models/gbm.py` — LightGBM con obiettivo Poisson
5. `src/predict.py` — inferenza settimanale + log append-only

## Comandi

```bash
# Ingestion (stage veloci: ~10 minuti totali)
python ingest.py --stage matches
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage elo        # opzionale, servizio a volte giu'

# Stage lenti (ore, rate-limited, interrompibili grazie alla cache)
python ingest.py --stage lineups
python ingest.py --stage player_stats
python ingest.py --stage missing      # richiede Chrome/Chromium

# Normalizzazione
python -m src.normalize --report --apply   # diagnosi + mappa automatica
python -m src.normalize --build            # costruisce matches_master.parquet

# Feature
python -m src.features.form
python -m src.features.market
python -m src.features.market --coverage   # copertura quote per stagione

# Valutazione
python -m src.evaluate                # tabella di confronto sul test set
python -m src.evaluate --calibration  # curve di calibrazione ed ECE
python -m src.evaluate --bias         # favourite-longshot, stagione per stagione
python -m src.models.baseline --demo  # controlli sulla matrice dei risultati

# Test senza rete
python -m tests.test_form
python -m tests.make_fixtures         # ATTENZIONE: scrive in data/raw
```

L'ambiente di sviluppo e' **Windows con PowerShell**. Per cancellare file usare
`Remove-Item ... -ErrorAction SilentlyContinue`, non `rm -f`. Evitare
`python -c "..."` con apici annidati: mettere il codice in un file.

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

- `manual/coach_changes.csv` — colonne `league, season, team, date, coach_out,
  coach_in`. ~150 righe per la Serie A degli ultimi 10 anni
- `manual/derbies.csv` — GIA FATTO, 48 coppie con colonna `intensity`
  (city/regional/rivalry). La coppia va trattata come NON ordinata:
  `tuple(sorted([casa, trasferta]))`
- `manual/team_name_map.json` — GIA FATTO, 10 voci. Le ultime due
  (`Hellas Verona`, `SPAL`) aggiunte a mano per agganciare `fbref_schedule`,
  che usa nomi diversi da quelli gia' mappati (`Hellas Verona FC`,
  `SPAL 2013`). Senza quelle due la giornata si agganciava solo all'89%