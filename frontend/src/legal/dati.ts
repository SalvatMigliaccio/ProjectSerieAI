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
export const AGGIORNATA_IL = "2026-10-27";

/** Eta' minima per creare un account: il contenuto riguarda le scommesse. */
export const ETA_MINIMA = 18;

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
];

/** Periodi di conservazione, in giorni. Devono coincidere con `settings.py`. */
export const CONSERVAZIONE_GIORNI = {
  /** IP, user agent ed esito degli accessi (`auth_events`). */
  registroAccessi: 365,
  /** Sessioni, contati da quando hanno smesso di essere utilizzabili. */
  sessioni: 365,
  /** Token di verifica e di reset, contati dalla scadenza. */
  tokenEmail: 30,
  /** Account mai confermati via email. */
  accountNonConfermati: 30,
} as const;
