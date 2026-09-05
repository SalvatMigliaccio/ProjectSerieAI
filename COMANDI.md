# COMANDI

Riferimento operativo. **Questo file e' l'unica fonte di verita' sui comandi**:
`CLAUDE.md` spiega il *perche'* delle scelte e rimanda qui per il *come*. Se un
comando cambia, si aggiorna qui e basta — elenchi duplicati divergono.

Ambiente: **Windows con PowerShell**, virtualenv in `.venv`.
I comandi sotto presuppongono `.\.venv\Scripts\python.exe` come interprete;
dove si legge `python`, usa quello.

Tempi misurati sulla macchina di sviluppo (i7-13620H, 10 core) con Serie A,
13 stagioni, 4580 partite.

---

## 1. Ciclo settimanale — un comando solo

```bash
python -m src.weekly
```

Fa tutto: aggiorna i dati, scarica le quote, ricostruisce dataset e feature,
deduce la prossima giornata, la predice tutta e aggiorna il track record.

**Quando lanciarlo**

| turno | snapshot quote caricato | lancia |
|---|---|---|
| weekend | venerdi' 17:00 UK | **sabato mattina** |
| infrasettimanale | martedi' 13:00 UK | **mercoledi' mattina** |

Cioe' dopo che le quote ci sono e prima del primo calcio d'inizio.

**Rilanciarlo e' sicuro.** E' idempotente: una partita gia' prevista dallo
stesso modello viene saltata e dichiarata, il registro resta append-only. Se
non c'e' niente da fare esce con codice 0 e un messaggio, non con un errore.

```bash
python -m src.weekly --dry-run       # tutto tranne la scrittura nel registro
python -m src.weekly --skip-ingest   # riusa i dati gia' scaricati (~10s)
python -m src.weekly --verbose       # log completi dei moduli chiamati
```

### Il report su file

Ogni ciclo scrive un report HTML, uno per giornata:

```
data/processed/reports/giornata_2627_03.html    una per giornata, non si sovrascrivono
data/processed/reports/ultimo.html              percorso fisso, comodo da tenere aperto
```

Aprilo con un doppio clic. Ha la tabella delle previsioni, le **selezioni piu'
probabili** su tutti i mercati (1X2, doppia chance, over/under 1.5-3.5,
gol-gol, squadra che segna) con la **quota equa** accanto, la sezione della
squadra seguita con i punteggi esatti, le partite scoperte e il track record.
Si adatta al tema chiaro/scuro del browser e non dipende da niente di esterno.

Il report e' una **vista**: il dato e' `predictions_log.csv`. Rigenerarlo non
cambia nulla, cancellarlo nemmeno.

### Se serve fare i passi a mano

```bash
python ingest.py --stage matches      # risultati appena giocati
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage fixtures     # quote del turno imminente
python -m src.normalize --build
python -m src.features.form
python -m src.features.market
python -m src.predict --next          # SCRIVE nel registro
python -m src.backtest_log
```

**`predict` e' l'unico passaggio non recuperabile.** Verifica con un assert che
il timestamp UTC preceda il calcio d'inizio: se il registro non viene scritto
in tempo, quella giornata e' persa per sempre ai fini del track record.
Ricostruirla dopo con `--as-of` finisce in un file separato che **non** e' un
track record.

---

## 2. Primo avvio, da zero

```bash
python -m pip install -r requirements.txt

python ingest.py --stage matches
python ingest.py --stage understat
python ingest.py --stage schedule
python ingest.py --stage fixtures

python -m src.normalize --report --apply   # costruisce team_name_map.json
python -m src.normalize --build

python -m src.features.form
python -m src.features.market

python -m src.evaluate                     # ~10 minuti, riproduce la tabella
```

Gli stage lenti (`lineups`, `player_stats`, `missing`) non servono a niente di
quanto e' stato costruito finora: saltali.

---

## 3. Ingestion

| comando | tempo | scrive |
|---|---|---|
| `python ingest.py --stage matches` | ~10s | `data/raw/matches.parquet` |
| `python ingest.py --stage understat` | ~2 min | `understat_schedule`, `understat_team_match` |
| `python ingest.py --stage fixtures` | ~5s | `data/raw/fixtures_odds.parquet` |
| `python ingest.py --stage schedule` | ~5 min | `data/raw/fbref_schedule.parquet` |
| `python ingest.py --stage elo` | ~1 min | `clubelo_history` — opzionale, servizio spesso giu' |

Quando football-data e' irraggiungibile (risponde 503 su tutto il dominio piu'
spesso di quanto dovrebbe), scarica `fixtures.csv` dal browser e poi:

```bash
python ingest.py --stage fixtures --fixtures-file C:\percorso\fixtures.csv
```

**Stage lenti**, ore, rate-limited, interrompibili grazie alla cache:

```bash
python ingest.py --stage lineups
python ingest.py --stage player_stats
python ingest.py --stage missing        # richiede Chrome/Chromium
```

---

## 4. Normalizzazione

```bash
python -m src.normalize --report           # diagnosi dei nomi squadra
python -m src.normalize --report --apply   # scrive la mappa automatica
python -m src.normalize --build            # costruisce matches_master.parquet
```

`--build` fallisce se il join scende sotto la soglia: e' voluto, significa che
mancano voci in `manual/team_name_map.json`.

---

## 5. Feature

```bash
python -m src.features.form                # medie mobili leakage-safe
python -m src.features.market              # de-vigging Shin + proporzionale
python -m src.features.market --coverage   # copertura quote, book per stagione
```

`--coverage` va rilanciato dopo ogni ingestion: i bookmaker spariscono senza
preavviso (Pinnacle si e' spento nel 2025/26 a meta' stagione).

---

## 6. Valutazione

```bash
python -m src.evaluate                # ~10 min — tabella + confronto appaiato
python -m src.evaluate --calibration  # curve di calibrazione ed ECE
python -m src.evaluate --bias         # favourite-longshot, stagione per stagione
python -m src.evaluate --no-save      # non riscrive walk_forward_predictions
```

Il default riaddestra 10 modelli su 114 giornate: e' il grosso dei 10 minuti.

---

## 7. Modelli: controlli e taratura

**La taratura gira SEMPRE sulla validazione (2122, 2223), mai sul test.**
Se ritari qualcosa, aggiorna il valore in `src/config.py` e rifai il punto 6.

```bash
# Controlli di correttezza (secondi)
python -m src.models.baseline --demo        # matrice risultati, segno di rho, gol-gol
python -m src.models.dixon_coles --check    # gradiente analitico vs differenza centrata

# Taratura
python -m src.models.dixon_coles --tune               # 28s  -> config.DC_HALFLIFE
python -m src.models.gbm --tune --workers 8           # ~11 min, tutte le varianti
python -m src.models.gbm --tune --variants ancorato   # ~2 min, solo M5
python -m src.models.gbm --blend                      # 61s  -> config.BLEND_WEIGHT

# Diagnostica
python -m src.models.gbm --importance       # 4s — cosa usa M4 senza mercato
```

Opzioni utili di `--tune`: `--configs N` (quante configurazioni provare, default
24), `--stride N` (giornate per blocco durante la ricerca, default 3 — solo per
abbassare il costo, il risultato riportato usa sempre stride 1).

---

## 8. Produzione

```bash
python -m src.predict --next                  # prossima giornata con partite future
python -m src.predict --matchday 3            # una giornata precisa
python -m src.predict --team Napoli           # prossima partita del Napoli
python -m src.predict --matchday 3 --dry-run  # mostra senza registrare

# Ricostruzione di una previsione passata: finisce in predictions_backfill.csv
python -m src.predict --as-of 2026-08-27 --next
```

```bash
python -m src.backtest_log             # metriche sulle previsioni risolte
python -m src.backtest_log --pending   # previsioni in attesa di risultato
python -m src.backtest_log --by-season # stagione per stagione
python -m src.backtest_log --backfill  # rilegge le ricostruzioni (NON e' un track record)
```

---

## 9. Test senza rete

```bash
python -m tests.test_form
python -m tests.test_predictions_log   # append-only e idempotenza del registro
```

`tests/make_fixtures.py` genera dati sintetici **sovrascrivendo `data/raw/`**.
Ora rifiuta di partire senza consenso esplicito:

```bash
python -m tests.make_fixtures --overwrite-raw    # DISTRUGGE i dati veri
```

Serviva: prima non aveva argparse, quindi un `--help` non stampava l'aiuto ma
**eseguiva la sovrascrittura**, e da li' `normalize --build` propagava i dati
finti fino a `matches_master`. Dopo averlo usato, rilancia l'ingestion vera.

---

## 10. File prodotti

| file | da | contiene |
|---|---|---|
| `data/raw/matches.parquet` | `--stage matches` | risultati e quote, solo partite giocate |
| `data/raw/fixtures_odds.parquet` | `--stage fixtures` | quote del turno imminente + `downloaded_at` |
| `data/raw/fbref_schedule.parquet` | `--stage schedule` | calendario completo, con la giornata |
| `data/interim/matches_master.parquet` | `normalize --build` | 4580 righe, 222 colonne |
| `data/processed/features_form.parquet` | `features.form` | medie mobili |
| `data/processed/features_market.parquet` | `features.market` | de-vigging + lambda impliciti |
| `data/processed/walk_forward_predictions.parquet` | `evaluate` | previsioni di tutti i modelli sul test |
| `data/processed/gbm_tuning.parquet` | `gbm --tune` | esito della ricerca iperparametri |
| **`data/processed/predictions_log.csv`** | `predict` | **il track record. Append-only, mai riscritto** |
| `data/processed/predictions_log.csv.bak` | `predict` | copia di sicurezza, rifatta prima di ogni scrittura |
| `data/processed/reports/giornata_*.html` | `weekly` | report leggibile, uno per giornata |
| `data/processed/reports/ultimo.html` | `weekly` | copia dell'ultimo, a percorso fisso |
| `data/processed/predictions_backfill.csv` | `predict --as-of` | ricostruzioni, non un track record |

Tutto `data/` e' in `.gitignore`.

---

## 11. Se il dataset si accorcia

`normalize --build` **rifiuta** di sostituire `matches_master.parquet` con uno
piu' corto del 20%. Il dataset cresce di dieci partite a settimana e non si
accorcia mai: un calo significa che un file in `data/raw/` e' stato
sovrascritto, non che i dati veri sono cambiati.

```
ValueError: il nuovo matches_master.parquet avrebbe 200 righe contro le 4580
attuali: un calo del 96%.
```

Rimedio: rilancia l'ingestion vera (`--stage matches`, `--stage understat`).
I CSV grezzi restano nella cache di soccerdata (`~/soccerdata/data/`), quindi
il ripristino non dipende dalla rete tranne che per la stagione in corso.

Se la riduzione e' voluta (per esempio hai ridotto `config.SEASONS`):

```bash
$env:AI_NAPLES_FORCE_BUILD = "1"; python -m src.normalize --build
```

---

## 12. Cose da non fare

- **Non lanciare `predict` dopo il calcio d'inizio.** L'assert lo blocca, ma il
  punto e' che quella riga di track record e' persa.
- **Non mescolare `predictions_backfill.csv` con `predictions_log.csv`.** Il
  primo e' costruito conoscendo il risultato, il secondo no.
- **Non tarare niente sul test set** (2324, 2425, 2526). La validazione e'
  2122-2223 e sta in `config.VALIDATION_SEASONS`.
- **Non usare le quote di chiusura** (`*C`) come feature: si formano dopo le
  formazioni ufficiali, fuori dall'orizzonte T-24h.
- **Non leggere le differenze di RPS senza il loro intervallo appaiato.** La
  varianza fra stagioni non e' una soglia di rumore.
- **Non lanciare `tests.make_fixtures` senza sapere cosa fa.** Sovrascrive
  `data/raw/` con dati sintetici; ora chiede conferma esplicita.

---

## 13. Note sull'ambiente

- Per cancellare file: `Remove-Item ... -ErrorAction SilentlyContinue`, non `rm -f`.
- Evitare `python -c "..."` con apici annidati: mettere il codice in un file.
- Understat scarica una libreria TLS da GitHub al primo avvio. Dietro proxy
  fallisce: scaricarla dalle release di `bogdanfinn/tls-client`.
- soccerdata usa una cache persistente in `~/soccerdata/data/`: rilanciare uno
  stage gia' completato non riscarica nulla, tranne la stagione in corso che
  viene sempre aggiornata.
