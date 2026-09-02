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

- **Le colonne `Bb*` (Betbrain) sono morte.** Vuote su tutta la 2026/27.
  Non usarle. Preferire `B365` e `PS`.
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
- `tests/make_fixtures.py`, `tests/test_form.py` — dati sintetici e test

### Da fare, in ordine

1. `src/features/market.py` — de-vigging (proporzionale e metodo di Shin),
   drift apertura-chiusura. Decidere quale bookmaker usare come riferimento
   e verificare la copertura per stagione
2. `src/features/team_strength.py` — Elo proprio calcolato dai risultati
3. `src/features/context.py` — giorni di riposo, congestione, coppe europee,
   derby (`manual/derbies.csv` e' pronto), cambi allenatore
   (`manual/coach_changes.csv` e' ancora un template vuoto)
4. `src/models/dixon_coles.py` — modello a gol con decadimento temporale
5. `src/evaluate.py` — RPS, calibrazione, walk-forward per giornata
6. `src/models/gbm.py` — LightGBM con obiettivo Poisson
7. `src/predict.py` — inferenza settimanale + log append-only

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
- `manual/team_name_map.json` — GIA FATTO, 8 voci generate automaticamente