# ADR 0005 — Privacy e cookie: cosa promettiamo, e cosa lo verifica

- **Stato**: accettata, implementata in parte. **Pagine pubbliche (T1, T3) sospese**
- **Data**: 27 ottobre 2026
- **Contesto**: gli account (`backend/auth/`, dal 25 settembre 2026) trattano dati personali
- **Decide**: base giuridica, cookie, cancellazione, conservazione, e come restano veri
- **Specifica**: `docs/SPEC_PRIVACY_COOKIE.md`

---

## Il vincolo da cui si parte

Un'informativa descrive il sistema. Come ogni descrizione invecchia senza
sollevare niente: un cookie nuovo o un periodo cambiato in `settings.py` la
rendono falsa, e nessun test se ne accorge. Il progetto ha gia' una risposta a
questo genere di problema, in `proteggi_produzione()` e nelle guardie sulle
rotte di scrittura: **la guardia, non la promessa**.

## Le decisioni

**Nessun banner di consenso.** Il sito imposta solo due cookie tecnici
(`ainaples_session`, `ainaples_access`). Non usa analytics, non carica
script di terze parti e non prende font da CDN. Per le linee guida del Garante
del 10 giugno 2021 basta l'informativa. Un banner chiederebbe un consenso che
non serve a niente, e abituerebbe chi lo vede ad accettare senza leggere.

**Base giuridica: il contratto, non il consenso.** In registrazione quindi
**non** c'e' la casella "acconsento al trattamento". Farebbe credere che il
consenso si possa revocare continuando a usare l'account, e non e' vero.

**Cancellazione self-service** (art. 17), `POST /api/me/account/delete`, con
la password. Sessioni, token, ruoli e pronostici spariscono per
`ON DELETE CASCADE`. `auth_events` resta, con `user_id` a NULL. Il track
record non si tocca: e' del modello, non dell'utente.

**Periodi di conservazione** pubblicati e applicati da
`python -m backend.auth.cli purge`: 365 giorni per il registro degli accessi e
per le sessioni concluse, 30 giorni per i token scaduti e per gli account mai
confermati.

**Le guardie** stanno in `tests/test_privacy.py`:
- i cookie e i periodi scritti in `frontend/src/legal/dati.ts` devono coincidere
  con i valori predefiniti di `settings.py`;
- dalla registrazione alla cancellazione, nessun flusso imposta un cookie non
  dichiarato;
- `index.html` non carica niente da altri siti.

## Cosa e' fatto e cosa no

| compito | stato |
|---|---|
| T2 `dati.ts` e test di coerenza | fatto |
| T4 test sui cookie e su `index.html` | fatto |
| T5 rotta di cancellazione | fatto |
| T6 "Elimina l'account" nel profilo | fatto |
| T7 `purge` e periodi in `settings.py` | fatto |
| T8 documentazione | fatto |
| **T1 testo di informativa e cookie policy** | **sospeso** |
| **T3 rotte `#/privacy`, `#/cookie` e link nel footer e in registrazione** | **sospeso** |

## La sospensione di T1 e T3 — 27 ottobre 2026

**Perche'.** Il testo non si puo' scrivere giusto senza tre fatti che oggi
mancano:
- un recapito del titolare oltre all'email: indirizzo postale o PEC;
- il provider SMTP di produzione e dove tiene i dati;
- la regione di Neon e quella delle funzioni Vercel, che decidono quali
  trasferimenti extra-UE dichiarare.

Un testo con questi campi inventati o lasciati generici sarebbe un'informativa
falsa, cioe' il problema che questo ADR esiste per evitare.

**Cosa comporta, detto chiaro.** Finche' T1 e T3 restano sospesi, il sito
raccoglie dati personali senza un'informativa pubblicata, e l'art. 13 la chiede
al momento della raccolta. Il resto e' gia' conforme (cookie solo tecnici,
cancellazione, conservazione), ma questo buco resta. **Una sospensione lunga va
accompagnata da registrazioni chiuse** (o limitate a utenti noti): e' la scelta
da fare se la ripresa slitta.

**Cosa resta valido nel frattempo.** Le guardie girano gia'. `dati.ts` e' la
fonte che le pagine leggeranno, e i test lo tengono allineato al backend anche
senza pagine.

**Quando si riprende.** Appena arrivano i tre fatti sopra. Si parte dalla
sezione 2 della specifica: va riletta perche' l'inventario dei trattamenti sia
ancora vero, e solo dopo si scrive il testo. Alla ripresa si aggiorna
`AGGIORNATA_IL` in `dati.ts` e lo stato di questo ADR.

**Da fare comunque, sospensione o no.** Mettere `purge` nello scheduler.
Finche' non gira, i periodi dichiarati non sono applicati, anche se nessuna
pagina li mostra ancora.

## Cosa la riaprirebbe

- un cookie non tecnico, analytics compresi: serve un banner con il rifiuto
  facile quanto l'accettazione, e `test_privacy` fallira' per ricordarlo;
- i pagamenti (fase 4, `MODELLO_DI_BUSINESS.md`): un nuovo fornitore e nuovi
  dati, e l'inventario della specifica va rifatto;
- il TOTP: oggi esiste solo come colonna, e quando funzionera' la
  cancellazione dovra' chiederlo.
