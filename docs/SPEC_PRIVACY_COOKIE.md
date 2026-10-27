# Spec — informativa privacy, cookie policy, diritti dell'interessato

Stato: **implementata in parte; T1 e T3 sospesi** il 27 ottobre 2026, vedi
`docs/adr/0005-privacy-cookie.md`.

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
| segreto TOTP | `users.totp_secret` | secondo fattore, se attivato | art. 6.1.b | fino alla disattivazione o cancellazione |
| squadra preferita | `users.favorite_team` | personalizzazione | art. 6.1.b | fino alla cancellazione |
| pronostici personali | `user_predictions` | funzione "I miei pronostici" | art. 6.1.b | fino alla cancellazione |
| IP, user agent, scadenza | `sessions` | sessioni attive, revoca | art. 6.1.f (sicurezza) | **12 mesi** dalla scadenza o revoca |
| IP, user agent, esito, tipo | `auth_events` | rilevare abusi e accessi illeciti | art. 6.1.f (sicurezza) | **12 mesi** |
| token di verifica e reset (hash) | `email_tokens` | attivazione, reset password | art. 6.1.b | **30 giorni** dalla scadenza |
| account mai verificato | `users.status = pending` | — | art. 6.1.b | **30 giorni**, poi cancellato |
| log delle richieste HTTP | Vercel | esercizio della piattaforma | art. 6.1.f | secondo i termini di Vercel |

Cose che il sistema **non** fa, e l'informativa lo dice esplicitamente:
nessuna profilazione, nessun marketing, nessuna decisione automatizzata
sull'utente (art. 22: le previsioni riguardano le partite, non le persone),
nessuna cessione a terzi, nessun dato di categoria particolare.

**Fornitori (responsabili ex art. 28)** e trasferimenti:

| fornitore | ruolo | sede dei dati | strumento per l'extra-UE |
|---|---|---|---|
| Vercel Inc. | hosting di frontend e API | USA | DPF / SCC — **da verificare** |
| Neon (Databricks) | Postgres | regione AWS — **da verificare** | se fuori UE: DPF / SCC |
| provider SMTP | invio email transazionali | **da decidere** | dipende dal provider |

## 3. Cookie e archiviazione locale — verificato

| nome | tipo | durata | scopo |
|---|---|---|---|
| `ainaples_session` | tecnico, HttpOnly, SameSite=Lax | sessione del browser | refresh token (ADR 0003) |
| `ainaples_access` | tecnico, HttpOnly, SameSite=Lax | durata del token di accesso | autenticazione delle richieste |
| `localStorage.aiNaplesApiBase` | tecnico | persistente | **solo in sviluppo**, mai impostato in produzione |

Niente analytics, niente pixel, niente font da CDN (sono self-hosted con
`@fontsource`), niente script di terze parti.

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
- richiede la password attuale. (Il TOTP oggi e' solo una colonna, nessun
  flusso lo usa: quando arrivera', la cancellazione dovra' chiederlo.)
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

## 7. Punti aperti, da chiudere prima di pubblicare

1. **Recapito del titolare**: indirizzo postale o PEC. L'email gmail da sola e'
   debole come recapito.
2. **Provider SMTP di produzione**: chi e', dove sta.
3. **Regione di Neon e delle funzioni Vercel**: da leggere nelle dashboard.

## 8. Piano

| # | compito | dipende da | verifica |
|---|---|---|---|
| T1 | testo di informativa e cookie policy, in `frontend/src/legal/` | 7.1-7.3 | rilettura — **sospeso** (ADR 0005) |
| T2 | `dati.ts` con cookie e periodi; test di coerenza con `settings.py` | — | R6 — **fatto** |
| T3 | rotte `#/privacy`, `#/cookie`, titoli in `TITOLI`, link nel footer e in `SignUp` | T1 | R3 — **sospeso** (ADR 0005) |
| T4 | test sui `Set-Cookie` e su `index.html` | — | R6 — **fatto** |
| T5 | `POST /api/me/account/delete` + test (cascate, evento, cookie cancellati, password errata rifiutata) | — | R4 — **fatto** |
| T6 | blocco "Elimina account" in `Profile.tsx` | T5 | R4 — **fatto** |
| T7 | `cli purge` + periodi in `settings.py` + test | — | R5 — **fatto** |
| T8 | voce in `COMANDI.md` per `purge`, rimando a questa spec in `CLAUDE.md` | T7 | — **fatto** |

T1 e T3 sono sospesi in attesa dei punti aperti della sezione 7: il perche',
cosa comporta e quando si riprende stanno in `docs/adr/0005-privacy-cookie.md`. **Il `purge` va messo nello scheduler**: finche' non gira, i
periodi dell'informativa sono una promessa non mantenuta.
