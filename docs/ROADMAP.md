# Le fasi

Stato al 26 settembre 2026. Una fase si chiude quando il suo criterio e'
verificato, non quando "sembra fatta".

| # | fase | chi | stato |
|---|---|---|---|
| 1 | motore: modello e feature | noi | **chiusa**, con un rinvio dichiarato |
| 2 | harness enterprise: auth, authz, modelli dati | noi | **fatta** (25 set 2026) |
| 3 | infrastruttura: separazione, Docker, deploy | misto | **Docker e deploy fatti** (26 set 2026); separazione al collega |
| 4 | modello di business | noi | da aprire |
| 5 | frontend: porting e build | collega | dipende dalla 3 |
| 6 | feature nuove | noi | da aprire |

---

## Fase 1 — motore

Chiusa. Modello, feature, valutazione, previsione e report girano e sono
misurati. Le decisioni stanno in `CLAUDE.md`, i numeri pure.

**Il rinvio, dichiarato e non nascosto**: il blocco GIOCATORI resta
PROVVISORIO. Per completarlo servono ~7 ore di WhoScored via Selenium, e il
blocco non sopravvive alla correzione per confronti multipli — non vale il
costo adesso. `config.SEASONS_PRIORITA` fa si' che un run futuro produca prima
le stagioni di test: si puo' fermare in qualsiasi momento e avere gia' qualcosa
di misurabile.

Due test restano rossi per questo, ed e' voluto: `test_sets.py::test_dataset_reale`
e `test_experiments_modelli.py::test_colonne_dichiarate`. Sono il promemoria
che il blocco e' a meta', non un guasto.

## Fase 2 — harness enterprise

Autenticazione, autorizzazione, modelli dati. Postgres come fonte di verita',
deciso in `docs/adr/0002-postgres-fonte-di-verita.md`.

**Precondizione, e va fatta per prima**: chiudere il confine di lettura. Oggi
11 moduli di produzione leggono parquet saltando `data.py`, e due di loro
(`risultati.py`, `backend/api/standings.py`) saltano cosi' anche la verifica
dello schema. Finche' e' cosi', migrare significa toccare una dozzina di file
e dimenticarne uno **senza avere un errore**.

**Il punto che rompe per primo**: `backend/api/__init__.py` fallisce all'avvio
se una rotta non e' GET/HEAD/OPTIONS, e `_guard_writes()` rifiuta ogni
scrittura sotto `track_record/` e `data/`. Un `POST /login` li fa saltare
entrambi. La garanzia va **ristretta al registro di proposito**: una previsione
vale solo se scritta prima del fischio da un processo locale, e un endpoint di
scrittura e' il modo in cui quella garanzia si perde. Non si allarga il buco
per far passare l'auth.

**Criterio di chiusura**: un utente si registra, entra, e vede solo i propri
dati; il registro resta scrivibile solo da processo locale, verificato da un
test che fallisce se compare una rotta di scrittura verso `track_record/`.

## Fase 3 — infrastruttura

Tre cose separate, con proprietari diversi.

**Separazione backend / frontend — la fa il collega.** Il monorepo di oggi e'
una scelta motivata (`docs/adr/0001-monolite-modulare.md`), presa pero' prima
che esistesse l'API. Lo split va scritto come **ADR 0003**, non come emendamento
al 0001: e' una decisione nuova, con un contesto diverso.
La dipendenza e' gia' a senso unico — `frontend` -> `backend` -> `goalmodel` —
ed e' il motivo per cui lo split non deve toccare un solo modulo di produzione.
Il contratto fra i due e' `web/openapi.json`, che `tests/test_api.py` verifica
non diverga.

**Dockerization e deploy — li facciamo noi.** `backend` e' gia' un secondo
pacchetto installabile con dipendenze proprie (`pyproject.toml`, extra `api`),
quindi un'immagine sua e' naturale. Le due cose da decidere prima di scrivere
il Dockerfile:
- **da dove arrivano i dati nel container.** `data/` non e' versionata e oggi
  e' un filesystem locale. E' esattamente cio' che l'ADR 0002 risolve, e per
  questo la fase 3 viene **dopo** la 2: containerizzare la lettura da parquet
  significherebbe rifarlo subito dopo;
- **il track record.** Resta su file e versionato, quindi il container che
  serve l'API lo monta in sola lettura. Il processo che lo scrive non e'
  containerizzato e resta locale, per la stessa ragione di sempre.

**Configurazione e segreti.** Variabili d'ambiente, mai nel repository — e'
gia' la regola di sicurezza n.1. Il nome di ogni variabile va documentato in
`docs/COMANDI.md`. Le dipendenze restano fissate a versione esatta: il progetto
verifica `test_production_unchanged` **bit a bit**, e un aggiornamento
silenzioso renderebbe indistinguibile una regressione da un cambio di libreria.

**Criterio di chiusura**: l'immagine parte da zero su una macchina pulita, la
suite passa dentro il container, e nessun segreto compare in `docker history`.

**Come si mette in produzione**: `docs/DEPLOY.md`.

### Dockerization e deploy — FATTI, 26 settembre 2026

Verificato sullo stack vero, non sulla carta: `/api/health` a 200 leggendo il
`data/` montato, una rotta dati a **401** da anonimo e **200** da autenticato,
cookie con `Secure` su HTTPS, header di sicurezza presenti, e i due montaggi che
rifiutano la scrittura **nel kernel** (`OSError`), non nelle guardie
dell'applicazione. Immagine 726 MB, 46 pacchetti, senza browser automation:
`pyautogui`, `seleniumbase`, `lightgbm` e `matplotlib` non sono installabili e
la build fallisce se rientrano. `tests/test_deploy.py` copre 22 invarianti del
deploy.

**Quattro difetti che solo un container ha mostrato**, tutti chiusi: l'immagine
installava PyAutoGUI attraverso `soccerdata`; `config.ROOT` dedotta da
`__file__` puntava dentro site-packages e l'API accusava i dati invece del
percorso; il `mkdir` all'import uccideva ogni import da non-root su filesystem
in sola lettura; e l'elenco di pacchetti scritto a mano dimenticava
scikit-learn, con `/api/health` a 500 e la build verde.

**Due difetti di sicurezza trovati provando lo stack**, non leggendo il codice:
uvicorn scartava `X-Forwarded-For` perche' non sapeva quale proxy credere — ogni
riga di audit registrava Caddy e il limite per indirizzo diventava globale — e
una richiesta di accesso rifiutata annullava per rollback il contatore che
l'aveva rifiutata, quindi **il blocco dell'account non esisteva** e l'audit
conteneva solo successi. Dettagli nei due commit; il secondo spiega anche
perche' 220 test non lo vedevano.

**Resta aperto e non e' nostro**: la separazione backend/frontend e l'ADR 0003
che la motiva. L'ADR va scritto quando il collega decide i tempi: scriverlo
adesso vincolerebbe una decisione che non prendiamo noi.

## Fase 4 — modello di business

Da aprire. Dipende dalla 2 (abbonamenti) e dalla 3 (qualcosa di deployabile).

**Il vincolo che non cambia**: si mostrano le selezioni con la quota equa, mai
valore atteso, puntata consigliata o "value". Non e' prudenza, e' aritmetica
misurata — zero giocate su 3420 hanno EV positivo contro B365. Un prodotto che
promettesse il contrario mentirebbe sui propri numeri.

## Fase 5 — frontend

La fa il collega, dopo la separazione. I tipi in `frontend/src/api/types.ts`
ricalcano `web/openapi.json`, che e' il contratto.

## Fase 6 — feature nuove

Da aprire. Il primo candidato e' il seguito del blocco B: sostituire gol+assist
con l'xG di Understat, ora che `--stage shots` funziona.
