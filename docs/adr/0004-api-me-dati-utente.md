# ADR 0004 — `/api/me`: i dati dell'utente, secondo prefisso scrivibile

- **Stato**: accettata, implementata
- **Data**: 21 ottobre 2026
- **Contesto**: squadra preferita e "I miei pronostici"
- **Decide**: dove e con quali regole l'API scrive dati che non sono account

---

## Il vincolo da cui si parte

L'API dei dati e' in sola lettura per costruzione: `_assert_read_only_routes`
fa fallire l'avvio se una rotta fuori da `/api/auth/` dichiara un metodo di
scrittura. La ragione e' una sola, e resta valida: **nessuna rotta HTTP deve
poter toccare il track record**, che vale solo se scritto prima del fischio da
un processo locale.

Due funzioni nuove hanno bisogno di scrivere: la squadra preferita, salvata
nell'account, e i pronostici dell'utente.

## La decisione

**Un secondo prefisso, `/api/me/`, accanto a `/api/auth/`.**
`WRITABLE_PREFIX` diventa una tupla di due. E' un'altra porta stretta, non un
allargamento: tutto cio' che sta sotto `/api/me` scrive righe dell'utente in
Postgres, come gli account; il track record resta file, fuori portata di ogni
rotta. I test che garantivano "nessuna rotta scrive il registro" valgono
invariati.

**Perche' non dentro `/api/auth`.** Funzionerebbe, ed e' esattamente il
motivo per non farlo: `/api/auth` diventerebbe "qualsiasi cosa scriva", e la
regola perderebbe di significato senza che nessun test se ne accorga.

## Le regole dei pronostici, che sono quelle del registro

- **Solo prima del calcio d'inizio, deciso dal server.** La richiesta non
  porta nessun orario: il server legge il calcio d'inizio dai file delle
  partite e lo confronta col proprio orologio. Una partita senza orario noto
  non si puo' pronosticare: "sconosciuto" non e' "piu' tardi".
- **Non si ritira dopo il fischio.** Una giocata persa che sparisce dallo
  storico e' lo stesso problema di una previsione riscritta a posteriori.
- **Le probabilita' del modello non si salvano.** Si ricalcolano dai due gol
  attesi del registro, come fa la dashboard: la riga conserva solo partita e
  mercato.
- **Nessun punteggio.** La pagina confronta quante giocate sono uscite con
  quanto il modello si aspettava che uscissero; non fa classifiche.
- **Un limite per account** (`ratelimit.prediction_limiter`) e al massimo dieci
  giocate per partita.

## Cosa costa

Il pacchetto `backend/auth` legge il track record tramite `backend.api.store`
(in sola lettura), come gia' facevano le selezioni. La tabella
`user_predictions` e la colonna `users.favorite_team` arrivano con la
migrazione `7c1e5a2d9b40`.

## Aggiornamento, 23 ottobre 2026 — si gioca dalla chiusura della giornata prima

I pronostici non aspettano piu' le quote. Chiusa una giornata, quella dopo si
apre subito dal **calendario** (`store.fixtures`, nuova rotta pubblica
`GET /api/fixtures/{season}`): partite e orari, nessuna probabilita'. Le
probabilita' del modello compaiono quando `predict_round` registra la
giornata, e valgono anche per le giocate gia' fatte, perche' si ricalcolano.

Restano invariate le due regole che contano: il calcio d'inizio lo decide il
server (ora dal calendario, anche per le partite non ancora prezzate), e una
giocata non si ritira dopo il fischio. Si aggiunge un limite: si gioca solo la
**prossima giornata**, non tutta la stagione — un pronostico fatto ad ottobre
su una partita di maggio non e' confrontabile con un modello che quella
partita la prezza la settimana stessa.
