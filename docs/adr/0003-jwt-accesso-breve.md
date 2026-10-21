# ADR 0003 — JWT di accesso breve, sessione sul server come rinnovo

- **Stato**: accettata, implementata
- **Data**: 21 ottobre 2026
- **Contesto**: fase 2, autenticazione (`backend/auth/`)
- **Decide**: come si autentica una richiesta, e quanto dura un accesso

---

## Il punto di partenza

Fino a questa decisione l'accesso era una **sessione opaca sul server**: un
token casuale in un cookie HttpOnly, la sua impronta SHA-256 in Postgres, una
query a ogni richiesta. Due scadenze: 12 ore di inattivita', **14 giorni** di
massimo assoluto.

La richiesta era: token che scadono, **24 ore e non settimane**, gestiti con
JWT.

## Le tre strade considerate

| | cosa | revoca | costo |
|---|---|---|---|
| A | sessione opaca, massimo 24 h | immediata ovunque | una riga di configurazione |
| **B** | **JWT 15 min + sessione sul server come rinnovo, 24 h** | immediata sull'account, entro 15 min sui dati | nuova libreria, rinnovo nel frontend |
| C | JWT puro 24 h | **nessuna** fino alla scadenza | il piu' basso, e il piu' pericoloso |

**C e' scartata.** Un JWT non si revoca: "Esci", "Chiudi sessione", il cambio
password che chiude le altre sessioni e la sospensione di un account
smetterebbero di avere effetto per un giorno intero. La pagina account
mostrerebbe una revoca che non revoca niente.

**A era la raccomandazione tecnica**: una sola API, stessa origine, un
Postgres — il caso in cui JWT compra meno. **Scelta B**, per due motivi che
valgono: le rotte dei dati smettono di interrogare il database a ogni
richiesta della dashboard (sette chiamate per giro, a ogni aggiornamento), e
il formato regge il giorno in cui l'API dei dati e quella dell'account
staranno su processi diversi.

## La decisione

**Due token, entrambi cookie HttpOnly, nessuno leggibile dal JavaScript.**

| | token di accesso | token di rinnovo |
|---|---|---|
| forma | JWT HS256 (`backend/auth/access.py`) | opaco, 256 bit, in DB solo l'hash |
| durata | **15 minuti** (`AI_NAPLES_ACCESS_TOKEN_MINUTES`, max 60) | **24 ore** assolute, 12 ore di inattivita' |
| cookie | `ainaples_access`, `Path=/`, `Max-Age` = durata | `ainaples_session`, `Path=/api/auth`, di sessione |
| chi lo verifica | ogni rotta, **senza database** | solo `POST /api/auth/refresh` |
| contiene | utente, sessione, permessi | niente: e' una chiave per una riga |

**Le rotte dell'account ricontrollano la riga.** `/me`, l'elenco sessioni, il
cambio password, la revoca: qui una sessione revocata deve fermarsi subito,
non fra quindici minuti. `deps.current_session` carica la riga indicata dal
`sid` del JWT e rifa' gli stessi controlli del rinnovo.

**Il rinnovo non ruota il token.** La rotazione scopre un furto solo se ladro
e proprietario usano lo stesso token, e butta fuori il proprietario ogni volta
che due schede rinnovano nello stesso istante. Con un tetto di 24 ore e una
pagina che revoca un dispositivo con un clic, la corsa costa piu' di quanto
compra.

**Un cookie di accesso scaduto e' 401, anche sulle rotte pubbliche.** Senza
cookie la richiesta e' anonima; con un cookie morto no. Altrimenti un
abbonato col token appena scaduto riceverebbe la parte gratuita con un 200,
senza niente che dica che manca il resto. Il 401 fa rinnovare il frontend.

## Cosa costa, detto chiaro

**Una finestra di 15 minuti sulle rotte dei dati.** Una sessione revocata, un
account sospeso, un permesso tolto continuano a vedere i dati finche' il JWT
che hanno in mano non scade. L'area account no. Se servisse chiuderla del
tutto: abbassare `AI_NAPLES_ACCESS_TOKEN_MINUTES`, oppure ruotare
`AI_NAPLES_SECRET_KEY`, che invalida ogni JWT in un colpo — e' il freno di
emergenza.

**Il frontend deve rinnovare.** `api/client.ts` rinnova una volta sola al
primo 401 e riprova (`fetchWithRefresh`, con una sola richiesta in volo anche
se ne falliscono sette insieme); `AuthContext` rinnova ogni 10 minuti e al
ritorno su una scheda rimasta in secondo piano.

## Le trappole di JWT, e dove sono chiuse

Ognuna ha il suo test in `tests/test_auth_access.py`.

- **`alg: none` e confusione fra algoritmi**: `algorithms=["HS256"]`, una
  lista di uno. Mai l'algoritmo che dichiara il token.
- **Token senza scadenza**: PyJWT controlla `exp` solo se c'e'. `exp`, `iat`,
  `sub`, `sid`, `aud`, `iss` sono obbligatori.
- **Token di un altro scopo**: `aud`, `iss` e `typ = "access"` fissi. Lo
  stesso segreto firma i link di verifica e di reset.
- **Chiave condivisa**: la chiave del JWT e' un HMAC del segreto con
  un'etichetta, non il segreto stesso.
- **Token in `localStorage`**: no. Cookie HttpOnly, come prima.

## Un difetto trovato strada facendo

`POST /api/auth/sign-out` cancellava i cookie sulla `response` iniettata e poi
restituiva un `Response` nuovo, che quegli header non li aveva: la sessione
veniva revocata sul server ma il browser teneva il cookie. Innocuo finche'
ogni richiesta passava dal database; con un JWT le rotte dei dati lo
avrebbero accettato fino alla scadenza. Corretto, con un test.
