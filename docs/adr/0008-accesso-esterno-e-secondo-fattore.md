# ADR 0008 — Accesso con Google (e poi Apple), secondo fattore via email

- **Stato**: accettata. Step 1, 2, 3, 5 e 6 (app di autenticazione) implementati il 20 novembre 2026; step 4 (Apple) rimandato
- **Data**: 20 novembre 2026
- **Contesto**: oggi si entra solo con email e password (`backend/auth/`, ADR 0003)
- **Decide**: come un account si lega a un fornitore esterno, cosa succede a un
  account senza password, e come funziona la verifica in due passaggi

---

## Il piano in step

| step | cosa | stato |
|---|---|---|
| 1 | schema: `user_identities`, `mfa_challenges`, `password_hash` facoltativo | **fatto** |
| 2 | accesso con Google (OpenID Connect, codice + PKCE, lato server) | **fatto** |
| 3 | secondo fattore via email, facoltativo | **fatto** |
| 4 | accesso con Apple | **rimandato**: servono account Apple Developer e un dominio HTTPS |
| 6 | secondo fattore con **app di autenticazione (TOTP)** e codici di riserva | **fatto** |
| 5 | informativa privacy (Google come fonte, art. 14), cookie nuovi, `openapi.json`, `COMANDI.md` | **fatto**; con esso chiusi T1 e T3 dell'ADR 0005 |

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
| cancellazione account | si riconferma con Google: il ritorno lascia un cookie firmato (`ainaples_reauth`, 5 minuti) legato a utente **e** sessione, che la rotta di cancellazione controlla |
| scollegare l'ultimo fornitore | rifiutato (409) se l'account non ha password: resterebbe senza nessun modo di entrare |

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

## Step 2 — com'e' fatto l'accesso con Google

| file | cosa |
|---|---|
| `backend/auth/oauth.py` | il protocollo: URL di Google, scambio del codice, verifica del token, cookie del flusso firmato. Nessun accesso al database |
| `backend/auth/identities.py` | le regole della sezione 2: chi entra, quando si collega, quando si rifiuta |
| `backend/auth/oauth_routes.py` | `GET /api/auth/providers`, `/oauth/google/start`, `/oauth/google/callback`, `GET /identities`, `POST /identities/google/unlink` |
| `frontend/src/components/GoogleButton.tsx` | il pulsante, visibile solo se il server dice che Google e' configurato |

**Tre intenti, un solo giro.** `sign_in` (pagina di accesso e registrazione),
`link` (profilo: collega Google a un account gia' aperto) e `reauth` (profilo,
account senza password: riconferma prima di cancellare). `link` e `reauth`
registrano nel flusso la sessione che li ha fatti partire, e al ritorno
pretendono di trovare la stessa: un collegamento non deve atterrare su chi ha
fatto l'accesso nel frattempo.

**Il ritorno non risponde mai con una pagina d'errore.** E' una navigazione,
non una chiamata: ogni esito e' un redirect al frontend, con i cookie oppure
con `?oauth_error=<codice>`. I codici sono una lista chiusa, tradotta in
`api/auth.ts` (`oauthErrorMessage`); il testo di Google non arriva mai alla
pagina.

**Due cookie nuovi, dichiarati nella cookie policy** (`legal/dati.ts`, e
`test_privacy` li lega a `settings.py`): `ainaples_oauth` (10 minuti, solo su
`/api/auth/oauth`, cancellato al ritorno) e `ainaples_reauth` (5 minuti, solo
su `/api/me/account`).

**Dipendenza**: `pyjwt[crypto]`, cioe' `cryptography`, per la firma RS256 di
Google. Servira' anche per Apple (ES256).

**Verifica**: `tests/test_auth_oauth.py`, 33 test con un Google finto che firma
con una chiave RSA generata nel test. Controllo per mutazione: togliendo il
controllo dello `state` o il rifiuto degli account `pending`, i test
corrispondenti falliscono.

## Step 3 — com'e' fatto il secondo fattore

| file | cosa |
|---|---|
| `backend/auth/mfa.py` | emissione, verifica, rinvio del codice; attivazione e disattivazione |
| `backend/auth/mfa_routes.py` | `/api/auth/mfa/`: `pending`, `verify`, `resend`; `enable/start`, `enable/confirm`, `disable/start`, `disable/confirm` |
| `service.MfaRequired` | l'eccezione che `service.sign_in` e `identities.sign_in` sollevano al posto di aprire la sessione |
| `frontend/src/pages/Account.tsx` (`MfaCode`) | la schermata `/#/sign-in/codice` |
| `frontend/src/pages/Profile.tsx` (`SezioneDueFattori`) | attivare e disattivare, in Sicurezza |

**Un'eccezione, non un valore di ritorno.** Chi chiama `service.sign_in` su un
account con la 2FA attiva riceve `MfaRequired`, non una sessione: un chiamante
che se ne dimentica fallisce, non apre una sessione per sbaglio. La rotta la
cattura senza rilanciarla, quindi la richiesta fa commit: quello che il primo
fattore ha scritto (il contatore azzerato, un collegamento a Google appena
fatto) resta.

**L'accesso con password risponde 202** con `{"mfa_required": true, "sent_to":
"m•••o@gmail.com"}` e il solo cookie `ainaples_mfa`. L'accesso con Google
rimanda a `/#/sign-in/codice?next=...`, e la pagina chiede la casella a
`GET /api/auth/mfa/pending`.

**I limiti, insieme.** 5 tentativi per codice (`AI_NAPLES_MFA_MAX_ATTEMPTS`),
10 minuti di validita' (`AI_NAPLES_MFA_CODE_MINUTES`), un solo codice vivo per
utente e scopo, rinvio limitato a 5 l'ora per account e a 30 minuti dal primo
codice, 30 richieste ogni 5 minuti per indirizzo su verifica e rinvio. Un
codice sbagliato costa il tentativo anche se la richiesta fallisce
(`keep_on_refusal`, come per la password).

**Spegnerla chiede un codice**, come accenderla: un cookie rubato non deve
poter togliere la protezione che esiste per i cookie rubati.

**Il codice non e' nell'oggetto dell'email**: l'oggetto si legge nelle
notifiche di un telefono bloccato.

**Cookie nuovo, dichiarato**: `ainaples_mfa`, 30 minuti, solo su
`/api/auth/mfa`.

**Verifica**: `tests/test_auth_mfa.py`, 18 test. Per mutazione: togliendo il
blocco dopo la password, il tetto ai tentativi, o il blocco dopo Google, falliscono
i test corrispondenti. Provato dal vivo il 20 novembre 2026 sullo stack Docker
con Mailpit: attivazione, accesso in due passaggi, rinvio che invalida il
codice vecchio, account di prova cancellato.

## Step 6 — l'app di autenticazione (TOTP)

Chiude il limite scritto nella sezione 4: il codice per email arriva alla stessa
casella del recupero password. Il codice dell'app sta sul telefono.

| file | cosa |
|---|---|
| `backend/auth/totp.py` | segreto cifrato, verifica con blocco del riuso, codici di riserva |
| `backend/auth/mfa.py` | `uses_app`: accesso e disattivazione chiedono l'app se l'account la usa; nessuna email |
| `backend/auth/mfa_routes.py` | `GET /status`, `POST /totp/setup`, `/totp/confirm`, `/totp/recovery-codes` |
| migrazione `c5e2a9f4b1d3` | `users.totp_last_step`, tabella `mfa_recovery_codes` |

**Le quattro decisioni**, ciascuna contro un buco preciso:

1. **Segreto cifrato** (AES-GCM) con `AI_NAPLES_MFA_KEY`, **separata** da
   `SECRET_KEY`. Il segreto deve essere reversibile per verificare un codice, e in
   chiaro un dump genererebbe codici validi. Chiave separata perche' ruotare
   `SECRET_KEY` e' il freno d'emergenza dei JWT: se cifrasse anche i segreti
   TOTP, chi ha l'app resterebbe chiuso fuori, codici di riserva compresi.
   `production_problems()` segnala la chiave vuota. **Va salvata insieme al
   database**: perderla vuol dire far riconfigurare l'app a tutti.
2. **Un codice vale una volta.** `totp_last_step` ricorda il passo di 30 secondi
   dell'ultimo codice accettato; tolleranza di un passo per lo sfasamento degli
   orologi, non di piu'.
3. **Codici di riserva, nessun ripiego sull'email.** Dieci, monouso, mostrati una
   volta, conservati come HMAC. Rigenerarli chiede il codice dell'APP, non uno di
   riserva: chi ne ha uno avanzato non deve poterne coniare dieci.
4. **Attivarla chiede la password** (o la riconferma con Google per chi non ce
   l'ha), oltre al codice dell'app. Senza, una sessione rubata installerebbe la
   propria app e chiuderebbe fuori il proprietario. Per questo il cookie
   `ainaples_reauth` ora vale su `/api` e non solo su `/api/me/account`.

**Passare da email ad app** si fa direttamente (la conferma dell'app sostituisce
il metodo); da app a email si spegne l'app (con il suo codice) e si accende
l'email. Spegnere l'app cancella segreto, passo e codici di riserva.

**Verifica**: `tests/test_auth_totp.py`, 17 test con un orologio spostato a mano.
Per mutazione: togliendo il blocco del riuso, la richiesta della password, il
"monouso" dei codici di riserva o la cifratura, falliscono i test
corrispondenti. Provato dal vivo il 20 novembre 2026 sullo stack Docker:
attivazione (403 senza password), accesso con l'app senza alcuna email, codice
riusato rifiutato, codice di riserva accettato una volta, disattivazione.
