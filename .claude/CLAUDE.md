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

## Stato attuale

Fatto:
- `ingest.py` — download multi-fonte via soccerdata, stage indipendenti e
  ri-eseguibili. API verificata sulla 1.9.1
- `src/config.py` — percorsi, leghe, stagioni, iperparametri
- `src/normalize.py` — mapping nomi squadra con scoperta automatica dei
  disallineamenti, join con test di integrita'. Testato su fixture sintetiche
- `tests/make_fixtures.py` — dati sintetici con disallineamenti deliberati

Da fare, in ordine:
1. `src/features/form.py` — medie mobili esponenziali leakage-safe
   (half-life 6 partite), continue tra stagioni
2. `src/features/team_strength.py` — Elo proprio + coefficienti Dixon-Coles
3. `src/features/context.py` — riposo, congestione, coppe europee, derby
4. `src/features/market.py` — de-vigging (proporzionale e metodo di Shin),
   drift quote
5. `tests/test_no_leakage.py` — **scrivilo prima delle feature, non dopo**
6. `src/models/dixon_coles.py` — modello a gol con decadimento temporale
7. `src/evaluate.py` — RPS, calibrazione, walk-forward
8. `src/models/gbm.py` — LightGBM con obiettivo Poisson
9. `src/predict.py` — inferenza settimanale + log append-only

## Comandi

```bash
# Ingestion (stage veloci: ~10 minuti totali)
python ingest.py --stage matches
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage elo

# Stage lenti (ore, rate-limited, interrompibili grazie alla cache)
python ingest.py --stage lineups
python ingest.py --stage player_stats
python ingest.py --stage missing      # richiede Chrome/Chromium

# Normalizzazione
python -m src.normalize --report      # diagnosi + mappa proposta
python -m src.normalize --build       # costruisce interim/matches_master.parquet

# Test senza rete
python -m tests.make_fixtures         # ATTENZIONE: scrive in data/raw
```

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
- `manual/derbies.csv` — lista derby per la flag `is_derby`
- `manual/team_name_map.json` — da generare con `--report` e rivedere a mano