# Spec — informativa privacy, cookie policy, diritti dell'interessato

Stato: **implementata** il 20 novembre 2026 (T1 e T3, sospesi dal 27 ottobre,
ripresi e chiusi), vedi `docs/adr/0005-privacy-cookie.md`. Pagine: `#/privacy`,
`#/cookie`, in `frontend/src/pages/Legal.tsx`, che legge ogni fatto da
`frontend/src/legal/dati.ts`.

Questa spec dice **cosa** deve essere vero quando il lavoro e' finito e come lo
si verifica. Il testo legale definitivo non e' qui: e' un deliverable (vedi T1).
Non e' una consulenza legale. Prima di pubblicare, una rilettura di chi se ne
occupa di mestiere vale piu' di qualsiasi spec.

## 1. Perche' adesso

Fino al 25 settembre 2026 il sito non trattava dati personali. Con gli account
(`backend/auth/`) li tratta, e per la Serie A di un pubblico italiano vale il
GDPR dal primo utente registrato. Oggi manca tutto: informativa, cookie policy,
cancellazione dell'account, periodo di conservazione dei log di accesso.

## 2. Cosa tratta davvero il sistema — inventario verificato

L'informativa descrive **questi** trattamenti, non trattamenti ipotetici. Se il
codice cambia, cambia prima questa tabella e poi il testo.

| dato | dove | finalita' | base giuridica | conservazione |
|---|---|---|---|---|
| email, hash password, stato, date | `users` | account e accesso | art. 6.1.b (contratto) | fino alla cancellazione |
| account Google collegato: identificativo (`sub`), email al momento del collegamento, date | `user_identities` | accesso con Google, se scelto | art. 6.1.b | fino allo scollegamento o alla cancellazione |
| verifica in due passaggi: metodo e data di attivazione | `users.mfa_method`, `mfa_enabled_at` | secondo fattore, se attivato | art. 6.1.b | fino alla disattivazione o cancellazione |
| codici di verifica (solo HMAC), tentativi, IP, user agent | `mfa_challenges` | secondo fattore; rilevare tentativi di indovinare il codice | art. 6.1.b e 6.1.f (sicurezza) | **30 giorni** dalla scadenza, come i token email |
| app di autenticazione: segreto TOTP (**cifrato**, AES-GCM con `AI_NAPLES_MFA_KEY`), ultimo passo accettato, codici di riserva (solo HMAC) | `users.totp_secret`, `totp_last_step`, `mfa_recovery_codes` | secondo fattore con app, se scelto; accesso se si perde il telefono | art. 6.1.b | fino alla disattivazione dell'app o alla cancellazione: alla disattivazione si cancella tutto |
| squadra preferita | `users.favorite_team` | personalizzazione | art. 6.1.b | fino alla cancellazione |
| pronostici personali | `user_predictions` | funzione "I miei pronostici" | art. 6.1.b | fino alla cancellazione |
| IP, user agent, scadenza | `sessions` | sessioni attive, revoca | art. 6.1.f (sicurezza) | **12 mesi** dalla scadenza o revoca |
| IP, user agent, esito, tipo | `auth_events` | rilevare abusi e accessi illeciti | art. 6.1.f (sicurezza) | **12 mesi** |
| token di verifica e reset (hash) | `email_tokens` | attivazione, reset password | art. 6.1.b | **30 giorni** dalla scadenza |

`users.password_hash` e' vuoto per chi si e' registrato con Google e non ha mai
impostato una password (ADR 0008).
| account mai verificato | `users.status = pending` | — | art. 6.1.b | **30 giorni**, poi cancellato |
| log delle richieste HTTP | server (VPS) | esercizio del servizio | art. 6.1.f | rotazione dei log del server |

Cose che il sistema **non** fa, e l'informativa lo dice esplicitamente:
nessuna profilazione, nessun marketing, nessuna decisione automatizzata
sull'utente (art. 22: le previsioni riguardano le partite, non le persone),
nessuna cessione a terzi, nessun dato di categoria particolare.

**Dati ricevuti da terzi (art. 14) — l'accesso con Google**, dal 20 novembre
2026 (ADR 0008). Solo per chi lo sceglie. Da Google arrivano **tre** dati, e
nient'altro, perche' lo scope richiesto e' `openid email`: l'identificativo
dell'account Google (`sub`), l'indirizzo email e se Google lo considera
verificato. **Non** arrivano nome, foto, contatti, ne' alcun accesso ad altri
servizi Google (nessun token di accesso viene conservato).

Google per l'accesso **non** e' un nostro responsabile: e' un **titolare
autonomo** del proprio trattamento, e da quel momento sa che la persona entra
in MatchPoint. L'informativa lo deve dire e rimandare alla privacy policy di
Google. Non e' un trasferimento nostro verso Google: e' il browser della persona
che parla con Google; il nostro server invia a Google solo il codice monouso e
le credenziali del client, e riceve il token con i tre dati sopra.

**Fornitori (responsabili ex art. 28)** e trasferimenti:

Scelte del 20 novembre 2026: **VPS nell'UE con Docker** (`compose.prod.yaml`:
sito, API e Postgres sullo stesso server) e **Gmail SMTP** per le email.
Vercel e Neon, citati nella prima stesura, non sono piu' nel piano.

| fornitore | ruolo | sede dei dati | strumento per l'extra-UE |
|---|---|---|---|
| fornitore del VPS (**da scegliere**, purche' nell'UE) | hosting di sito, API e database | UE | non serve |
| Google, servizio Gmail (SMTP) | invio email transazionali, compresi i **codici di verifica in due passaggi** | anche USA | EU-US Data Privacy Framework |

L'informativa nomina il VPS per **categoria** ("fornitore del server, UE"),
come l'art. 13.1.e consente. Quando il fornitore e' scelto, se si vuole
nominarlo basta cambiare `DESTINATARI` in `legal/dati.ts`.

## 3. Cookie e archiviazione locale — verificato

| nome | tipo | durata | scopo |
|---|---|---|---|
| `ainaples_session` | tecnico, HttpOnly, SameSite=Lax | sessione del browser | refresh token (ADR 0003) |
| `ainaples_access` | tecnico, HttpOnly, SameSite=Lax | durata del token di accesso | autenticazione delle richieste |
| `ainaples_oauth` | tecnico, HttpOnly, SameSite=Lax, solo `/api/auth/oauth` | 10 minuti, cancellato al ritorno da Google | lega il ritorno da Google al browser che l'ha iniziato (anti-CSRF) |
| `ainaples_mfa` | tecnico, HttpOnly, SameSite=Lax, solo `/api/auth/mfa` | 30 minuti, cancellato all'inserimento del codice | lega il codice di verifica al browser che l'ha chiesto |
| `ainaples_reauth` | tecnico, HttpOnly, SameSite=Lax, solo `/api` | 5 minuti | solo account senza password: riconferma con Google prima della cancellazione o dell'attivazione dell'app di autenticazione |
| `localStorage.aiNaplesApiBase` | tecnico | persistente | **solo in sviluppo**, mai impostato in produzione |

Niente analytics, niente pixel, niente font da CDN (sono self-hosted con
`@fontsource`), niente script di terze parti.

**L'accesso con Google non cambia questa conclusione.** Il pulsante e' un
semplice link: nessuno script di Google viene caricato nelle nostre pagine, e i
cookie che Google imposta li imposta sul proprio dominio, quando la persona ci
va. I tre cookie nuovi sono tecnici, stretti per percorso e per durata.

**Conseguenza: nessun banner di consenso.** Per i soli cookie tecnici le linee
guida del Garante (10 giugno 2021) chiedono l'informativa, non il consenso. Un
banner qui sarebbe un consenso a niente, e abituerebbe chi lo vede a cliccare
"accetta" senza leggere. **Il giorno in cui entra un cookie non tecnico
(analytics compresi) questa sezione e' falsa**, e serve un banner con rifiuto
facile quanto l'accettazione. Il test R6 esiste per accorgersene.

## 4. Decisioni prese

| decisione | scelta |
|---|---|
| titolare | persona fisica: Salvatore Migliaccio |
| dove | pagine dedicate `#/privacy` e `#/cookie`, linkate dal footer |
| cancellazione | self-service, "Elimina account" nel profilo |
| conservazione log di accesso | 12 mesi |
| lingua | italiano |
| eta' minima | **18 anni**, coerente con il "18+" gia' nel footer: il contenuto riguarda le scommesse |

## 5. Requisiti

### R1 — Pagina informativa privacy, `#/privacy`
Contenuti obbligatori (art. 13): titolare e recapiti; tabella dei trattamenti
della sezione 2 resa leggibile; fornitori e trasferimenti; diritti (accesso,
rettifica, cancellazione, limitazione, portabilita', opposizione); reclamo al
Garante con link; eta' minima; data di ultimo aggiornamento.
Linguaggio semplice: sezioni brevi, la tabella prima della prosa.

### R2 — Pagina cookie, `#/cookie`
La tabella della sezione 3, la frase "nessun cookie di profilazione o di
terze parti", come cancellarli dal browser, data di ultimo aggiornamento.

### R3 — Raggiungibilita'
- link "Privacy" e "Cookie" nel footer (`Colophon` in
  `frontend/src/components/Layout.tsx`), quindi su landing, dashboard e account;
- sotto il pulsante di registrazione (`SignUp`): *"Creando un account dichiari
  di aver letto l'informativa privacy"*, con il link.

**Nessuna casella "acconsento al trattamento"**: la base giuridica e' il
contratto, non il consenso. Chiederlo farebbe credere che lo si possa revocare
continuando a usare l'account, ed e' falso.

### R4 — Cancellazione dell'account
- `POST /api/me/account/delete` (POST e non DELETE: CORS ammette solo GET e
  POST, e `/api/me/` e' gia' fra i prefissi scrivibili, `WRITABLE_PREFIX`);
- richiede la password attuale. **Un account senza password** (creato con
  Google) conferma invece rientrando con Google: il ritorno lascia
  `ainaples_reauth`, firmato, valido 5 minuti, legato a utente **e** sessione
  (ADR 0008);
- **non chiede anche il codice di verifica in due passaggi**, ed e' una scelta:
  il caso da cui la cancellazione si difende e' chi ha in mano una sessione
  (portatile sbloccato, cookie rubato), e quel caso e' gia' fermato dalla
  password o dal rientro con Google, che la sessione da sola non da'.
- cancella la riga `users`; sessioni, token, ruoli e pronostici spariscono per
  `ON DELETE CASCADE`, gia' presente; `auth_events` resta con `user_id = NULL`,
  come il modello prevede, e sparisce alla scadenza dei 12 mesi;
- scrive un evento `account_deleted` in `auth_events`;
- cancella i due cookie nella risposta;
- **non tocca il track record**: sono dati del modello, non dell'utente;
- interfaccia: blocco "Elimina l'account" in fondo a `/account/sicurezza`, in
  due passi (spiegazione, poi password), con la frase che dice cosa resta (log
  di sicurezza anonimi, periodo letto da `legal/dati.ts`). Dopo, la pagina
  conferma "Account eliminato" invece della scheda di accesso.

### R5 — Pulizia per conservazione
Comando `python -m backend.auth.cli purge` (idempotente, `--dry-run`) che
applica i periodi della sezione 2 e stampa quante righe toglie per tabella. Si
lancia dallo scheduler. I periodi stanno in `backend/auth/settings.py`, non
ricopiati nel codice del comando. L'informativa li riporta: se cambiano li',
cambia il testo (vedi R6).

### R6 — Guardie, non promesse
- **test sui cookie**: percorrendo signup, verifica, sign-in, refresh e
  sign-out, i soli `Set-Cookie` emessi hanno i nomi dichiarati in R2. Un cookie
  nuovo fa fallire il test, che dice di aggiornare la cookie policy;
- **test di coerenza**: nomi dei cookie e periodi di conservazione mostrati
  nelle pagine vengono da un solo file (`frontend/src/legal/dati.ts`), e un test
  Python lo confronta con `settings.py`;
- **test su `index.html`**: nessuno `<script src>` o `<link>` verso un'origine
  esterna.

### R7 — Esercizio degli altri diritti
Accesso e portabilita' via email al titolare, entro 30 giorni (art. 12.3). Un
export self-service **non** e' in questo lavoro: con questa base utenti sarebbe
costo senza ricavo. Va scritto nell'informativa come si chiede.

## 6. Fuori da questa spec

- termini di servizio e disclaimer sulle scommesse (documento diverso, altra
  base);
- pagamenti: quando arriva la fase 4 di `MODELLO_DI_BUSINESS.md`, l'inventario
  della sezione 2 si riapre;
- registro dei trattamenti (art. 30): l'esenzione sotto i 250 dipendenti non
  vale per i trattamenti non occasionali, e i log di accesso lo sono. Va
  tenuto, ma e' un documento interno, non una pagina: la tabella della
  sezione 2 ne e' gia' quasi tutto il contenuto.

## 7. Punti aperti — chiusi il 20 novembre 2026

Erano la ragione della sospensione di T1 e T3. Le risposte:

- **recapito del titolare**: **solo email**, scelta del titolare. E' l'indirizzo
  gia' pubblico nel piede del sito; un recapito postale o PEC si aggiunge in
  `TITOLARE` (`legal/dati.ts`) quando ci sara';
- **provider SMTP**: **Gmail SMTP**;
- **hosting**: **VPS nell'UE**, fornitore da scegliere; nessun trasferimento
  extra-UE per l'hosting.

Restano veri due avvisi:

0. **L'accesso con Google e' chiuso a chi non e' "utente di test" finche' manca
   l'informativa.** Nella Google Cloud Console la schermata di consenso OAuth
   in modalita' *Testing* ammette solo gli indirizzi elencati li'; per
   pubblicarla Google chiede l'URL di un'informativa privacy e della home.
   E' un motivo in piu', concreto, per chiudere T1 e T3.

1. se il VPS scelto non fosse nell'UE, la riga dei destinatari diventa falsa:
   va cambiata **prima** del deploy;
2. se le email passeranno da un altro provider, idem.

## 8. Piano

| # | compito | dipende da | verifica |
|---|---|---|---|
| T1 | testo di informativa e cookie policy, `frontend/src/pages/Legal.tsx` | 7 | rilettura — **fatto** (20 novembre 2026) |
| T2 | `dati.ts` con cookie e periodi; test di coerenza con `settings.py` | — | R6 — **fatto** |
| T3 | rotte `#/privacy`, `#/cookie`, titoli in `TITOLI`, link nel footer e in `SignUp` | T1 | R3 — **fatto**; `test_the_policy_pages_are_published_and_reachable` |
| T4 | test sui `Set-Cookie` e su `index.html` | — | R6 — **fatto** |
| T5 | `POST /api/me/account/delete` + test (cascate, evento, cookie cancellati, password errata rifiutata) | — | R4 — **fatto** |
| T6 | blocco "Elimina account" in `Profile.tsx` | T5 | R4 — **fatto** |
| T7 | `cli purge` + periodi in `settings.py` + test | — | R5 — **fatto** |
| T8 | voce in `COMANDI.md` per `purge`, rimando a questa spec in `CLAUDE.md` | T7 | — **fatto** |
| T9 | inventario, cookie e cancellazione aggiornati per Google e la verifica in due passaggi; guardia sui cookie estesa a quei flussi | ADR 0008 | R6 — **fatto** (20 novembre 2026) |

T1 e T3 erano sospesi in attesa dei punti della sezione 7, ora chiusi: la
storia sta in `docs/adr/0005-privacy-cookie.md`. Resta valido il consiglio
della spec: prima di pubblicare, una rilettura di chi se ne occupa di mestiere. **Il `purge` va messo nello scheduler**: finche' non gira, i
periodi dell'informativa sono una promessa non mantenuta.
