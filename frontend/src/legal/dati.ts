/**
 * I fatti che l'informativa privacy e la cookie policy dichiarano.
 *
 * UN FILE SOLO, E UN TEST CHE LO LEGA AL BACKEND. I nomi dei cookie e i
 * periodi di conservazione vivono davvero in `backend/auth/settings.py`; qui
 * sono ricopiati perche' il frontend non importa Python e non deve chiederli
 * all'API per mostrare una pagina statica. Una copia che diverge in silenzio
 * renderebbe falsa l'informativa senza che niente protesti: per questo
 * `tests/test_privacy.py` legge questo file e lo confronta con le impostazioni.
 *
 * Le pagine non scrivono questi valori a mano: li leggono da qui. Cambiare un
 * periodo vuol dire cambiare `settings.py`, questo file e `AGGIORNATA_IL`.
 *
 * Specifica: docs/SPEC_PRIVACY_COOKIE.md.
 */

/** Data dell'ultima modifica sostanziale dell'informativa, ISO. */
export const AGGIORNATA_IL = "2026-11-20";

/** Eta' minima per creare un account: il contenuto riguarda le scommesse. */
export const ETA_MINIMA = 18;

/**
 * Il titolare del trattamento (SPEC sezione 4): una persona fisica.
 *
 * SOLO EMAIL, scelta del 20 novembre 2026: niente PEC ne' indirizzo postale per
 * ora. L'indirizzo era gia' pubblico nel piede di ogni pagina; ora il piede lo
 * legge da qui, cosi' le due copie non possono divergere.
 */
export const TITOLARE = {
  // `nominativo` e non `nome`: test_privacy legge ogni campo `nome` di questo
  // file, seguito da una stringa, come il nome di un cookie.
  nominativo: "Salvatore Migliaccio",
  email: "salvatore.migliaccio998@gmail.com",
} as const;

/** Entro quanto si risponde a una richiesta sui diritti (art. 12.3 GDPR: un mese). */
export const RISPOSTA_DIRITTI_GIORNI = 30;

/** Il Garante, per il reclamo (art. 77). */
export const GARANTE_URL = "https://www.garanteprivacy.it";
export const GOOGLE_PRIVACY_URL = "https://policies.google.com/privacy";

/**
 * Chiave di `CONSERVAZIONE_GIORNI`, oppure una durata detta a parole quando
 * non e' un numero di giorni ("fino alla cancellazione dell'account").
 */
export type Conservazione = keyof typeof CONSERVAZIONE_GIORNI | string;

export type Trattamento = {
  dati: string;
  perche: string;
  base: string;
  quanto: Conservazione;
};

/**
 * L'inventario della SPEC (sezione 2), in parole. Un dato nuovo nel codice entra
 * prima nella spec e poi qui: l'informativa descrive cio' che il sistema fa,
 * non cio' che potrebbe fare.
 */
export const TRATTAMENTI: Trattamento[] = [
  {
    dati: "Email, password (solo in forma cifrata, mai leggibile), stato e date dell'account",
    perche: "Creare l'account e farti accedere",
    base: "Contratto (art. 6.1.b): senza, il servizio non si può dare",
    quanto: "Fino a quando elimini l'account",
  },
  {
    dati: "Se entri con Google: l'identificativo del tuo account Google e la sua email",
    perche: "Riconoscerti quando premi «Continua con Google»",
    base: "Contratto (art. 6.1.b)",
    quanto: "Fino a quando scolleghi Google o elimini l'account",
  },
  {
    dati: "Se attivi la verifica in due passaggi: che è attiva e da quando; i codici inviati (solo in forma cifrata), i tentativi, indirizzo IP e browser",
    perche: "Chiederti il codice all'accesso e accorgerci se qualcuno prova a indovinarlo",
    base: "Contratto (art. 6.1.b) e sicurezza dell'account (legittimo interesse, art. 6.1.f)",
    quanto: "tokenEmail",
  },
  {
    dati: "Squadra preferita e i tuoi pronostici",
    perche: "Le funzioni «La mia squadra» e «I miei pronostici»",
    base: "Contratto (art. 6.1.b)",
    quanto: "Fino a quando elimini l'account",
  },
  {
    dati: "Sessioni aperte: indirizzo IP, browser, date",
    perche: "Tenerti dentro senza chiederti la password a ogni pagina; farti chiudere una sessione da un altro dispositivo",
    base: "Sicurezza (legittimo interesse, art. 6.1.f)",
    quanto: "sessioni",
  },
  {
    dati: "Registro degli accessi: tipo di evento, esito, indirizzo IP, browser, data",
    perche: "Accorgersi di tentativi di accesso non tuoi e ricostruirli",
    base: "Sicurezza (legittimo interesse, art. 6.1.f)",
    quanto: "registroAccessi",
  },
  {
    dati: "Collegamenti per attivare l'account o reimpostare la password (solo in forma cifrata)",
    perche: "Verificare la tua email, farti cambiare una password dimenticata",
    base: "Contratto (art. 6.1.b)",
    quanto: "tokenEmail",
  },
  {
    dati: "Account registrato ma mai confermato via email",
    perche: "Nessuno: viene cancellato",
    base: "Contratto (art. 6.1.b)",
    quanto: "accountNonConfermati",
  },
];

export type Destinatario = {
  chi: string;
  ruolo: string;
  dove: string;
};

/**
 * Chi altro tratta i dati (SPEC sezione 2, "Fornitori").
 *
 * Hosting: VPS nell'UE, scelta del 20 novembre 2026; il fornitore non e' ancora
 * scelto, e l'informativa ne dice la categoria (art. 13.1.e lo consente).
 * Email: Gmail SMTP, scelta dello stesso giorno.
 */
export const DESTINATARI: Destinatario[] = [
  {
    chi: "Fornitore del server (hosting)",
    ruolo: "Responsabile del trattamento (art. 28): custodisce il server su cui girano sito e database",
    dove: "Unione europea",
  },
  {
    chi: "Google, servizio Gmail",
    ruolo: "Invia per nostro conto le email di attivazione, di reimpostazione della password e i codici di verifica",
    dove: "Anche fuori dall'UE (Stati Uniti), con le garanzie dell'EU-US Data Privacy Framework",
  },
  {
    chi: "Google, accesso con Google (solo se lo scegli)",
    ruolo: "Titolare autonomo: verifica chi sei sul proprio sito e ci comunica identificativo ed email. Da quel momento sa che usi MatchPoint",
    dove: "Secondo l'informativa di Google",
  },
];

export type Cookie = {
  nome: string;
  durata: string;
  scopo: string;
};

/** Tutti e soli i cookie che il sito imposta. Tutti tecnici. */
export const COOKIE: Cookie[] = [
  {
    nome: "ainaples_session",
    durata: "fino alla chiusura del browser (al massimo 24 ore lato server)",
    scopo: "mantiene l'accesso: rinnova il token di accesso senza chiedere di nuovo la password",
  },
  {
    nome: "ainaples_access",
    durata: "15 minuti",
    scopo: "autentica le richieste ai dati riservati agli utenti registrati",
  },
  {
    nome: "ainaples_oauth",
    durata: "10 minuti, e si cancella al ritorno da Google",
    scopo: "solo durante l'accesso con Google: lega il ritorno da Google al browser che l'ha iniziato, contro le richieste contraffatte",
  },
  {
    nome: "ainaples_mfa",
    durata: "30 minuti, e si cancella quando inserisci il codice",
    scopo: "solo se hai attivato la verifica in due passaggi: lega il codice ricevuto per email al browser da cui stai entrando",
  },
  {
    nome: "ainaples_reauth",
    durata: "5 minuti",
    scopo: "solo per gli account senza password: conferma di aver appena verificato la tua identità con Google prima di eliminare l'account",
  },
];

/** Periodi di conservazione, in giorni. Devono coincidere con `settings.py`. */
export const CONSERVAZIONE_GIORNI = {
  /** IP, user agent ed esito degli accessi (`auth_events`). */
  registroAccessi: 365,
  /** Sessioni, contati da quando hanno smesso di essere utilizzabili. */
  sessioni: 365,
  /** Token di verifica e di reset e codici di accesso via email, contati dalla scadenza. */
  tokenEmail: 30,
  /** Account mai confermati via email. */
  accountNonConfermati: 30,
} as const;
