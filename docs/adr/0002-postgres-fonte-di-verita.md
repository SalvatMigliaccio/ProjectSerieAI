# ADR 0002 — Postgres e' la fonte di verita' dei dati, non solo degli account

- **Stato**: accettata come **direzione**, non ancora schedulata
- **Data**: 22 settembre 2026
- **Contesto**: fase 2 del piano (infrastruttura enterprise)
- **Decide**: dove stanno i dati del modello quando il progetto diventa un
  servizio, e cosa NON si sposta

---

## Il problema

Oggi ogni dato sta in parquet dentro `data/`, 12 file per 6.4 MB. Regge, e
regge bene, per una ragione sola: **c'e' un lettore solo, locale, uno alla
volta**. Nessuna di quelle condizioni sopravvive alla fase 2.

| cosa cambia in fase 2 | cosa rompe in parquet |
|---|---|
| l'API gira in un container | `data/` non e' versionata: in un filesystem effimero non esiste |
| piu' processi applicativi | due scritture concorrenti su un parquet non si fondono, si sovrascrivono |
| utenti che leggono una partita | si legge il file intero e si filtra in pandas |
| aggiornamento di una giornata | si riscrive il file intero |

La difesa "parquet e' piu' veloce per le scansioni colonnari" e' vera ma
risponde alla domanda sbagliata: vale per il **percorso di addestramento**
(walk-forward, 114 riaddestramenti, la matrice intera in memoria), non per il
**percorso di servizio**, che fa letture piccole e filtrate. Il secondo e'
quello che la fase 2 introduce, e parquet non lo serve.

## La decisione

**Postgres diventa la fonte di verita' per i dati del modello**, non solo per
account, organizzazioni e abbonamenti. Parquet smette di essere uno strato di
persistenza.

**Due cose non si spostano, e il motivo e' diverso per ciascuna:**

1. **`track_record/` resta su file, versionato in git.** Non e' una questione
   di prestazioni: l'integrita' del registro e' una proprieta' di **processo**,
   non di schema. Vale perche' e' append-only, scritto solo da un processo
   locale, e `git log` e' la traccia di revisione. In una tabella si e' a un
   `UPDATE` di distanza dal riscrivere una previsione dopo il fischio d'inizio,
   e a mesi di distanza nessuno saprebbe distinguerla dalle altre. E' la stessa
   ragione per cui l'API non ha rotte di scrittura.
2. **Il report HTML resta un file.** E' una vista, si rigenera.

## Cosa si guadagna, e non e' la velocita'

`schema.py` esiste perche' questo progetto ha perso dati in silenzio in tre
modi. Tutti e tre smettono di essere possibili **a livello di storage**:

| il modo di rompersi | oggi | con Postgres |
|---|---|---|
| `season` intero invece che stringa | controllo a runtime in `data.py` | la colonna e' `text`, l'errore e' all'inserimento |
| una colonna che sparisce | controllo a runtime | `NOT NULL`, l'errore e' all'inserimento |
| la quadrupla che si duplica | controllo a runtime | `UNIQUE (league, season, home_team, away_team)` |

E il caso che ci e' gia' costato un bug — Spezia-Hellas Verona 2022/23, la
partita di campionato **e** lo spareggio salvezza — diventa esattamente un
indice unico parziale:

```sql
CREATE UNIQUE INDEX ON fbref_schedule (league, season, home_team, away_team)
    WHERE week IS NOT NULL;
```

Cioe' la regola non negoziabile n.5 con la sua eccezione dichiarata, imposta
dal database invece che verificata a ogni lettura.

## Cosa costa, e va verificato invece che assunto

- **`test_production_unchanged` confronta bit a bit.** I float devono tornare
  identici dopo il giro in Postgres. `double precision` e' IEEE 754 binario a
  64 bit come `numpy.float64`, quindi in linea di principio lo e'; ma il
  round-trip passa dal driver, e questo va **misurato con un test**, non dato
  per scontato. Se non torna esatto, non e' il test a essere troppo severo.
- **I dtype nullable di pandas.** Il master ha 10 `Int64` e 10 `Float64`
  nullable accanto a 189 `float64` normali. Andata e ritorno devono conservare
  la distinzione fra `NaN` e `NA`, che per LightGBM non e' la stessa cosa.
- **Le 222 colonne non diventano 222 colonne di tabella.** Il grezzo e le
  feature sono cose diverse con cicli di vita diversi; lo schema si progetta
  quando si scrive, non qui.

## La precondizione, ed e' l'unica cosa da fare adesso

**Il confine di lettura e' costruito a meta'.** `data.py` esiste proprio per
essere l'unico punto che tocca il disco, ma oggi **11 moduli di produzione
leggono parquet per conto loro**:

```
ingest.py (7)      players.py (3)     registry.py       context.py
normalize.py       risultati.py       predict.py        sezioni.py
power_analysis.py  backend/api/store.py   backend/api/standings.py
```

I tre moduli di `experiments/` che leggono parquet NON contano: per progetto
leggono tutto e non sono importati da nessuno. `ingest.py` e' il caso a parte —
rilegge i grezzi che ha appena scritto — e va giudicato a se'.

Finche' e' cosi', migrare significa modificare una dozzina di file e
**dimenticarne uno non da' errore** — da' un modulo che legge un parquet stantio mentre tutti
gli altri leggono il database. E' lo stesso modo di fallire che la regola DRY
di `CLAUDE.md` gia' descrive per la chiave di join.

Chiudere il confine va fatto **comunque**, indipendentemente dal database:
e' il completamento di A1/A2 dell'audit. Fatto quello, la migrazione e' un
cambiamento dentro `data.py` e resta reversibile.

**Ordine, quindi**: chiudere il confine di lettura -> progettare lo schema ->
migrare. Non il contrario.

## Cosa non e' stato deciso qui

- Lo schema delle tabelle.
- Se il percorso di addestramento avra' bisogno di una cache derivata. Se un
  giorno servira', sara' una **ottimizzazione misurata e buttabile**, non una
  seconda fonte di verita'. La fonte resta una.
- La separazione dei repository, che era stata rimandata dall'ADR 0001 e avra'
  un file suo.
