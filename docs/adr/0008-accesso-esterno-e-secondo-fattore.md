# ADR 0008 — Accesso con Google (e poi Apple), secondo fattore via email

- **Stato**: accettata. Step 1 (schema) implementato il 20 novembre 2026; step 2-5 da fare
- **Data**: 20 novembre 2026
- **Contesto**: oggi si entra solo con email e password (`backend/auth/`, ADR 0003)
- **Decide**: come un account si lega a un fornitore esterno, cosa succede a un
  account senza password, e come funziona la verifica in due passaggi

---

## Il piano in step

| step | cosa | stato |
|---|---|---|
| 1 | schema: `user_identities`, `mfa_challenges`, `password_hash` facoltativo | **fatto** |
| 2 | accesso con Google (OpenID Connect, codice + PKCE, lato server) | da fare |
| 3 | secondo fattore via email, facoltativo | da fare |
| 4 | accesso con Apple | **rimandato**: servono account Apple Developer e un dominio HTTPS |
| 5 | informativa privacy (Google come destinatario), `openapi.json`, `COMANDI.md` | da fare |

## Le decisioni

### 1. Un fornitore si riconosce da `(provider, subject)`, mai dall'email

`user_identities` lega un account a un fornitore. La chiave è il `sub` del
token, che il fornitore garantisce stabile e mai riassegnato. L'email cambia
(presso Google si può cambiare), e con Apple può essere un indirizzo di inoltro
che non somiglia a quello vero. Far entrare qualcuno perché un'email coincide
è il modo in cui una persona finisce nell'account di un'altra.

L'email resta salvata solo come era al collegamento, per mostrare nella pagina
account quale account Google è connesso. Non serve a trovare nessuno.

Un solo account Google per utente (`uq_user_identities_user_provider`): due
renderebbero ambiguo "scollega Google" e lascerebbero una seconda chiave
dimenticata della stessa porta.

### 2. Collegamento automatico solo con due verifiche

Scelta dell'utente, 20 novembre 2026. Chi entra con Google usando l'email di un
account già esistente viene collegato **solo se** Google dichiara l'email
verificata (`email_verified: true`) **e** l'account nostro l'ha già confermata
(`email_verified_at` non nullo). In ogni altro caso si rifiuta, e si chiede di
entrare con la password e collegare Google dalla pagina account.

La seconda condizione non è ridondante: un account `pending` può essere stato
aperto da chiunque con l'indirizzo di un altro. Collegarlo a Google
consegnerebbe a quell'estraneo un account che il vero proprietario attiverebbe
poi credendolo suo.

### 3. Un account può non avere password

`users.password_hash` diventa facoltativo: un account creato con Google non ne
ha. `NULL` e non stringa vuota, perché il servizio deve poterlo riconoscere e
rifiutare come tale, non come hash corrotto.

| azione | account senza password |
|---|---|
| accesso con password | rifiutato con lo **stesso** messaggio e lo stesso tempo di una password sbagliata, e **non** conta per il blocco: non c'è niente da indovinare, e bloccare chiuderebbe fuori il proprietario dal reset |
| cambio password | rifiutato: la prima password si imposta con "password dimenticata", che prova il controllo dell'indirizzo. Una sessione da sola no |
| cancellazione account | oggi rifiutata; con lo step 2 si conferma rientrando con il fornitore |
| scollegare l'ultimo fornitore | (step 2) rifiutato se l'account non ha password: resterebbe senza nessun modo di entrare |

Il downgrade della migrazione si **ferma** se esiste un account senza password,
invece di cancellarlo per poter rimettere il vincolo `NOT NULL`.

### 4. Secondo fattore: un codice via email, facoltativo per tutti

Scelte dell'utente, 20 novembre 2026: codice a 6 cifre via email, attivabile da
chiunque, obbligatorio per nessuno (amministratori compresi).

**Il limite, detto chiaro.** Anche il reset della password passa dall'email.
Chi controlla la casella supera entrambi i fattori: chiede il reset, poi riceve
il codice. Il secondo fattore via email ferma chi ha **solo** la password,
rubata o indovinata, che è il caso più comune. Non ferma chi ha la casella. Per
un account Gmail che entra con Google il codice arriva nella stessa Gmail.
`users.totp_secret` resta per aggiungere l'app di autenticazione come seconda
opzione, senza migrare lo schema.

**Vale per ogni metodo di accesso**, Google compreso. Se il secondo fattore
scattasse solo con la password, "Continua con Google" sarebbe il modo per
saltarlo.

**`users.mfa_method`, non un booleano.** "Attiva" deve dire *quale* fattore; il
giorno in cui arriva il TOTP un booleano chiederebbe una seconda colonna che può
contraddire la prima. Tre vincoli nel database: il metodo è `email` o `totp`;
metodo e data di attivazione sono entrambi presenti o entrambi assenti; un
metodo `totp` non esiste senza segreto.

### 5. Il codice sta in `mfa_challenges`, non in `email_tokens`

Un accesso a metà, con il primo fattore superato e il secondo in attesa, è una
riga di `mfa_challenges`, con due segreti e un contatore di tentativi:

- `challenge_hash`: impronta di un token casuale tenuto in un cookie breve.
  Lega il codice al browser che ha superato il primo fattore, quindi un codice
  letto da sopra la spalla non serve da un altro dispositivo;
- `code_hash`: **HMAC con la chiave del server**, non SHA-256 semplice. I codici
  a 6 cifre sono un milione: un hash senza chiave in un dump del database si
  inverte provandoli tutti;
- `attempts`: dopo il massimo la sfida è morta, e serve ricominciare dal primo
  fattore.

Non in `email_tokens`, per un motivo concreto: lì `token_hash` è UNIQUE, e un
codice a 6 cifre non lo è. Due utenti con lo stesso codice negli stessi dieci
minuti farebbero fallire il secondo inserimento.

`purpose` distingue l'accesso (`sign_in`) dalla conferma che attiva o disattiva
il secondo fattore dalla pagina account (`enable`, `disable`).

**Conservazione**: le sfide scadute si cancellano con lo stesso periodo dei
token via email (30 giorni dalla scadenza, `token_retention_days`). Per chi
legge l'informativa sono la stessa cosa, un codice arrivato per email, quindi
non serve una promessa nuova. `retention.purge` le include, e
`legal/dati.ts` lo dice.

## Cosa non si fa

- **Niente libreria OAuth.** `pyjwt` (con `PyJWKClient` per le chiavi di Google)
  e `httpx` sono già dipendenze fissate. Una libreria in più è una versione in
  più da tenere ferma per i test bit a bit (regola di sicurezza 10).
- **Niente popup né token nel browser.** Il ritorno da Google arriva al
  backend, che apre la sessione con gli stessi due cookie HttpOnly dell'ADR 0003.
  Il JavaScript non vede mai un token di Google.
- **Apple non si prova su localhost**: Apple non accetta `localhost` come
  indirizzo di ritorno. Arriva con il dominio, sulle stesse tabelle.
