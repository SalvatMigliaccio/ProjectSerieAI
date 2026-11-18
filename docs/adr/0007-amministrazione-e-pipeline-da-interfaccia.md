# ADR 0007 — Tutto dall'interfaccia: amministrazione, registro in Postgres, pipeline in coda

- **Stato**: accettata. Fase 1 implementata il 18 novembre 2026; fasi 2 e 3 da fare
- **Data**: 18 novembre 2026
- **Contesto**: il progetto è deployato, e il proprietario deve poterlo gestire senza toccare codice né file
- **Supera**: in parte l'ADR 0002 (punto 1, "`track_record/` resta su file") e la regola di
  `ARCHITETTURA_MULTIUTENTE.md` "da non fare, mai: una rotta che lancia `predict_round`"

---

## Perché si riapre una regola scritta

Le due regole che questa decisione tocca avevano un motivo solo: **una previsione
vale solo se è scritta prima del fischio e non si può riscrivere dopo**. Il modo
scelto per garantirlo era di processo: un file append-only, scritto solo da un
processo locale, con `git log` come traccia.

Quel modo lega il prodotto a un PC acceso e a un `git push` dopo ogni giornata.
Per chi deve usare il sito deployato vuol dire che il deploy non serve a niente.
La garanzia resta, cambia chi la impone: **il database, non il processo**.

## La decisione, in tre fasi

### Fase 1 — amministrazione degli utenti (fatta)

`/api/admin`, terzo prefisso scrivibile accanto a `/api/auth` e `/api/me`.
Scrive account e ruoli, le stesse tabelle che già scriveva `/api/auth`.

- **Due livelli**: `users:manage` (admin e superadmin) per elencare, cercare,
  vedere la storia di un account, sospendere, riattivare e sbloccare;
  `system:manage` (solo superadmin) per nominare e revocare gli `admin` e per
  agire su un account che è a sua volta amministratore.
- **I permessi si leggono dal database a ogni chiamata**, non dal JWT: un admin
  revocato smette subito, non dopo i 15 minuti del token (ADR 0003).
- **Tre cose che nessuna chiamata HTTP può fare**: agire sul proprio account,
  agire su un superadmin, concedere un ruolo diverso da `admin`.
- **Il superadmin iniziale arriva da `AI_NAPLES_SUPERADMIN_EMAILS`**, e solo
  se l'indirizzo è verificato. Senza quella condizione il ruolo andrebbe a chi
  registra per primo l'indirizzo. Togliere l'indirizzo dalla variabile non
  revoca il ruolo, così una variabile sbagliata non chiude fuori l'unico
  amministratore.
- **Ogni azione scrive un evento in `auth_events`** con chi l'ha fatta
  (`details.by`). La sezione Registro del pannello li mostra.

Limite dichiarato: un utente sospeso perde subito sessioni, account e pannello,
ma un access token già emesso legge ancora le rotte dati per al massimo
`access_token_minutes` (ADR 0003).

### Fase 2 — il registro delle previsioni in Postgres (da fare)

Una tabella al posto di `predictions_log.csv` e dei `round_*.csv`. Ecco come
ogni proprietà del file diventa un vincolo:

| proprietà | file, oggi | Postgres |
|---|---|---|
| append-only | `append_log` controlla che il file non si accorci | il ruolo dell'applicazione non ha `UPDATE` né `DELETE` sulla tabella |
| scritta prima del fischio | assert in `predict.py` + `flag_post_kickoff` | trigger che rifiuta l'`INSERT` se `now() >= kickoff_utc` |
| timestamp affidabile | orologio della macchina | `DEFAULT now()` del server, la colonna non è scrivibile dal client |
| traccia di revisione | `git log` | i vincoli sopra, più un'impronta a catena delle righe |

Lo storico si importa una volta. I CSV restano nel repository come archivio
congelato, e un test verifica che l'import coincida riga per riga.

### Fase 3 — predict e close dalla dashboard (da fare)

Il pulsante **non** lancia il modello dentro l'API. Vercel non potrebbe comunque:
le funzioni hanno un limite di minuti, non hanno un disco persistente e non
hanno un browser per WhoScored. Il pulsante scrive una richiesta in una tabella
`pipeline_jobs` (chi, quando, quale comando). Un **worker** la prende, lancia lo
stesso comando idempotente di oggi e scrive esito e log nella tabella, così la
dashboard mostra lo stato.

Il worker gira **sul PC del proprietario** per ora (attività pianificata che
controlla la coda) e passerà su un **VPS** (servizio in `compose.prod.yaml`,
senza porte esposte). È lo stesso codice nei due posti.

Perché è sicuro: il web può solo *chiedere* di eseguire un comando che lo
scheduler eseguirebbe comunque, idempotente, e che rifiuta ogni previsione dopo
il calcio d'inizio. Non può scrivere il contenuto di una previsione.

## Cosa non cambia

- Nessuna rotta HTTP scrive il contenuto di una previsione.
- Nessun modello gira dentro il processo dell'API (`test_no_model_is_loaded`).
- Valore atteso, puntate e stake restano fuori da ogni payload.
