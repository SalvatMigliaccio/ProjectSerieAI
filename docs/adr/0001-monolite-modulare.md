# ADR 0001 — Un solo repository, a monolite modulare

- **Stato**: accettata
- **Data**: 22 settembre 2026
- **Contesto**: audit tecnico (`docs/AUDIT_TECNICO.md`), 46 file Python, 13.906 righe
- **Decide**: se spezzare il progetto in piu' repository adesso

---

## Il problema

Il progetto fa quattro cose con cadenze e modi di fallire diversi:

| pezzo | cosa fa | fallisce cosi' |
|---|---|---|
| `ingest` | scarica da quattro fonti esterne | rete: 503, rate limit, HTML al posto del CSV |
| `features` + `models` + `evaluation` | la libreria di ricerca | numerico: una misura sbagliata |
| `prediction` | il lavoro settimanale | temporale: una previsione tardiva e' persa per sempre |
| `reporting` | presentazione | cosmetico: una sezione in meno |

Sono quattro giunzioni reali, e la domanda "vanno in repository separati?"
e' legittima.

## La decisione

**Un solo repository**, con confini di pacchetto espliciti dentro `src/goalmodel/`
e la regola che ogni livello puo' importare solo quelli sotto di se'.

```
config
  ingest, normalize
    features/
      models/
        evaluation/
          prediction/
            reporting/

experiments/   importa tutto, non e' importato da nessuno
```

## Perche' non spezzare adesso

**1. Il contratto fra i pezzi e' implicito.** Cio' che `features` si aspetta da
`ingest` sono nomi di colonna sparsi nel codice e documentati in `CLAUDE.md`.
Non c'e' uno schema versionato, né una validazione. Spezzare prima di rendere
il contratto esplicito significa spostare un accoppiamento invisibile da dentro
un processo a dentro un `pip install`, dove diventa **version skew silenzioso**.

Non e' teorico: e' esattamente il difetto B2 dell'audit. `features_players.parquet`
e' entrato nel modello senza entrare nella pipeline che lo ricostruisce, e nessuno
se n'e' accorto perche' il merge riempiva di NaN e LightGBM i NaN li accetta. Con
tre repository quel difetto diventa la norma, non l'eccezione.

**2. Il costo e' immediato, il beneficio no.** Oggi: due sviluppatori, nessuna
API, nessun utente, nessun servizio in esecuzione. Spezzare costa tre CI, tre
cicli di rilascio e pin incrociati da mantenere. In cambio non da' niente che i
confini di pacchetto non diano gia'.

**3. Il costo di rimandare e' basso.** Con i livelli separati e nessun import
all'indietro, estrarre un livello e' `git filter-repo` piu' un `pyproject`.
E' l'ordine giusto: prima i confini, poi la distanza.

## Quando riaprire la decisione

Uno solo di questi basta:

| segnale | cosa estrarre per primo |
|---|---|
| `ingest` deve girare su una macchina o con una cadenza diverse dalla previsione | `ingest` + `normalize` |
| compare un secondo consumatore del core (una API web, un secondo campionato con un suo ciclo) | `features` + `models` + `evaluation` come libreria |
| due o piu' persone lavorano in parallelo su livelli diversi e i merge si scontrano | il livello conteso |
| il modello va servito a qualcuno che non sia chi lo ha addestrato | `prediction` come servizio |

**Prerequisito non negoziabile, in ogni caso**: lo schema dei parquet
dev'essere versionato e validato al confine prima che quel confine diventi la
rete. Finche' `matches_master` e' "quelle colonne li'", nessuno split e' sicuro.

## Conseguenze

- I confini vanno rispettati anche quando importare all'indietro sarebbe comodo.
  Oggi c'e' gia' un'eccezione: `models/gbm.py` e `models/dixon_coles.py`
  importano `evaluation` **dentro le funzioni** di taratura, per evitare un
  ciclo di import. E' un debito noto, da sciogliere spostando la taratura in
  `evaluation/`.
- `experiments/` puo' importare tutto e non dev'essere importato da niente.
- Una dipendenza nuova fra livelli e' una decisione di architettura, non un
  import: va discussa, non aggiunta.

## Alternative scartate

- **Tre repository subito** (`ingest` / `core` / `app`): scartata per i motivi 1
  e 2. Da riconsiderare quando scatta un segnale della tabella.
- **Monorepo con un `pyproject` per livello** (workspace): tutto il costo dello
  split senza il beneficio della separazione, finche' i livelli si rilasciano
  insieme. Da riconsiderare solo insieme allo split vero.
