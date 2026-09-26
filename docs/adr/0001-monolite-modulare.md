# ADR 0001 — Un solo repository, a monolite modulare

- **Stato**: accettata, **da rivedere** (vedi "Aggiornamento" in fondo)
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

---

## Aggiornamento — 22 settembre 2026

**Questa decisione e' stata presa senza sapere che esistevano gia' un'API e un
frontend.** Vivevano sul branch `API_Frontend`, scritto sul vecchio layout
`src/` piatto, e sono stati portati sul pacchetto lo stesso giorno. Due delle
tre ragioni per non spezzare **non valgono piu'**, e uno dei quattro segnali
che riaprono la decisione **e' gia' scattato**. Non si decide niente qui: si
mette per iscritto che chi legge questa ADR al momento della scelta non deve
fidarsi del testo sopra.

### Cosa e' cambiato, punto per punto

| era scritto | oggi |
|---|---|
| "il contratto fra i pezzi e' implicito, non c'e' uno schema versionato ne' una validazione" | **falso.** `src/goalmodel/schema.py` dichiara il contratto dei file e `data.py` lo verifica a **ogni lettura**: colonne obbligatorie, tipi che romperebbero un merge in silenzio, chiave univoca e non nulla |
| "oggi: due sviluppatori, nessuna API, nessun utente, nessun servizio in esecuzione" | **falso.** `backend/api/` e' un servizio FastAPI in sola lettura con sette endpoint, `frontend/` una dashboard React, e `web/openapi.json` e' il contratto **versionato** fra i due |
| "il costo di rimandare e' basso: con i livelli separati e nessun import all'indietro, estrarre un livello e' `git filter-repo` piu' un `pyproject`" | **piu' vero di prima.** Gli import all'indietro erano due, entrambi nascosti dentro funzioni; oggi non ce n'e' nessuno |

### Il prerequisito non negoziabile e' soddisfatto

L'ADR diceva: *"lo schema dei parquet dev'essere versionato e validato al
confine prima che quel confine diventi la rete. Finche' `matches_master` e'
'quelle colonne li'', nessuno split e' sicuro."*

Lo schema c'e' ed e' verificato al confine. **Con un limite dichiarato**: copre
le colonne la cui assenza o il cui tipo sbagliato produrrebbe un errore
silenzioso — chiavi, date, risultati — non tutte le 222. Per un confine di
processo basta; per un confine di rete fra repository con rilasci
indipendenti, il giorno in cui si spezza va deciso se allargarlo alle colonne
di feature, che oggi hanno il loro registro in `features/sets.py` e nessuna
validazione di tipo.

### Il segnale che e' scattato

> "compare un secondo consumatore del core (una API web, un secondo campionato
> con un suo ciclo)" -> estrarre `features` + `models` + `evaluation` come libreria

E' esattamente quello che e' successo. Va letto con una precisazione che conta:
`backend/` **non consuma il core**, consuma i suoi output — legge
`track_record/` e `data/`, non addestra e non importa mai un modello
(`tests/test_api.py` lo verifica in un sottoprocesso). Il consumatore vero del
core resta uno solo. Il segnale e' scattato a meta'.

### Cosa resta vero

La ragione piu' forte per non spezzare **subito** non era nessuna delle tre:
era l'ordine. Prima i confini, poi la distanza. I confini adesso ci sono —
livelli senza import all'indietro, uno schema verificato, due pacchetti
installabili distinti (`goalmodel` e `backend`), un contratto HTTP versionato.

Il candidato naturale per il primo distacco **non e' piu' un livello del
core**: e' `frontend/`, che e' l'unico pezzo con un altro linguaggio, un'altra
toolchain e un altro ciclo di rilascio, e che parla con il resto solo via HTTP
e solo in GET. Estrarlo non tocca nessun import Python.

**La decisione si riprende quando si affronta il frontend, non prima**, e a
quel punto va riscritta come ADR 0002 invece che emendata ancora.

