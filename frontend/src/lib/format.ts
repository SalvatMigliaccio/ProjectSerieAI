/**
 * Formattazione, e con essa la regola sui null.
 *
 * NULL SI MOSTRA VUOTO, MAI ZERO. Un RPS a zero e' una previsione perfetta;
 * una partita non ancora giocata non ha RPS. Scriverci `0` disegnerebbe un
 * trionfo dove non c'e' ancora niente. Ogni funzione qui restituisce stringa
 * vuota quando il dato manca: la cella resta bianca, e si vede.
 *
 * LE PERCENTUALI PORTANO IL DENOMINATORE. "44%" su sedici partite sembra un
 * dato solido e non lo e'; "44% (7 su 16)" si legge per quello che e'.
 */

export const EMPTY = "";

type Nullable = number | null | undefined;

const missing = (value: Nullable): value is null | undefined =>
  value === null || value === undefined || Number.isNaN(value);

/** Numero con decimali fissi. */
export function num(value: Nullable, digits = 2): string {
  return missing(value) ? EMPTY : value.toFixed(digits);
}

/** Percentuale senza denominatore: SOLO per le probabilita' 1X2, dove il
 *  denominatore e' la partita stessa e scriverlo non aggiungerebbe niente. */
export function prob(value: Nullable): string {
  return missing(value) ? EMPTY : `${Math.round(value * 100)}%`;
}

/** Percentuale con il suo denominatore, come impone la regola. */
export function ratio(hits: Nullable, total: Nullable): string {
  if (missing(hits) || missing(total) || total === 0) return EMPTY;
  return `${Math.round((hits / total) * 100)}% (${hits} su ${total})`;
}

export function odds(value: Nullable): string {
  return missing(value) ? EMPTY : value.toFixed(2);
}

/** Margine del book, in punti percentuali. */
export function overround(value: Nullable): string {
  return missing(value) ? EMPTY : `${(value * 100).toFixed(1)}%`;
}

/** RPS: cinque decimali, perche' le differenze che contano sono piccole. */
export function rps(value: Nullable, digits = 5): string {
  return missing(value) ? EMPTY : value.toFixed(digits);
}

export function score(home: Nullable, away: Nullable): string {
  if (missing(home) || missing(away)) return EMPTY;
  return `${home}-${away}`;
}

/** Da ISO 8601 UTC all'ora LOCALE di chi guarda. */
export function localTime(iso: string | null): string {
  if (!iso) return EMPTY;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleString("it-IT", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function localDateTime(iso: string | null): string {
  if (!iso) return EMPTY;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleString("it-IT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "3 ore fa", per la barra di stato. */
export function ago(iso: string | null): string {
  if (!iso) return EMPTY;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return EMPTY;

  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "adesso";
  if (minutes < 60) return `${minutes} min fa`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "ora" : "ore"} fa`;

  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "giorno" : "giorni"} fa`;
}

export const ROUND_LABELS: Record<string, string> = {
  future: "futura",
  open: "aperta",
  predicted: "predetta",
  partially_predicted: "predetta in parte",
  played: "giocata",
  closed: "chiusa",
};

export const OUTCOME_LABELS: Record<string, string> = { H: "1", D: "X", A: "2" };
