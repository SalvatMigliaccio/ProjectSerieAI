/**
 * L'unico punto che fa `fetch`. Solo GET: l'API non ha altri metodi, e non e'
 * una dimenticanza — il track record si scrive dai comandi locali, mai da una
 * richiesta HTTP.
 *
 * GLI ERRORI SI RACCONTANO. Una dashboard che resta bianca quando l'API e' giu'
 * fa cercare il problema nei dati, che stanno benissimo. Ogni errore porta con
 * se' l'URL chiamato e un suggerimento su cosa guardare.
 */

import type {
  Health,
  Match,
  Picks,
  Round,
  SeasonSummary,
  Selections,
  Standings,
  Status,
  TrackRecord,
} from "./types";

const FALLBACK_BASE = "http://127.0.0.1:8000";

export class ApiError extends Error {
  readonly status: number | null;
  readonly url: string;
  readonly hint: string | null;

  constructor(
    message: string,
    options: { status?: number | null; url: string; hint?: string | null },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? null;
    this.url = options.url;
    this.hint = options.hint ?? null;
  }
}

/**
 * Precedenza: `?api=` nella query, poi localStorage, poi la variabile
 * d'ambiente di build, infine il locale.
 *
 * Il parametro nella query serve a provare un tunnel senza ricompilare, e
 * viene ricordato: l'URL di ngrok cambia a ogni riavvio e riscriverlo ogni
 * volta sarebbe la via piu' rapida per incollarlo sbagliato.
 */
export function baseUrl(): string {
  const fromQuery = new URLSearchParams(window.location.search).get("api");
  if (fromQuery) {
    try {
      window.localStorage.setItem("aiNaplesApiBase", fromQuery);
    } catch {
      /* finestra privata: pazienza, vale solo per questa visita */
    }
    return fromQuery.replace(/\/$/, "");
  }

  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem("aiNaplesApiBase");
  } catch {
    /* idem */
  }

  const configured = import.meta.env.VITE_API_BASE_URL as string | undefined;
  return (stored ?? configured ?? FALLBACK_BASE).replace(/\/$/, "");
}

export function season(): string {
  return (import.meta.env.VITE_SEASON as string | undefined) ?? "2627";
}

async function get<T>(path: string): Promise<T> {
  const url = `${baseUrl()}${path}`;
  let response: Response;

  try {
    response = await fetch(url, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("non riesco a contattare l'API", {
      url,
      hint:
        "Il server è acceso? Prova `python -m backend.api --port 8000`. " +
        "Se usi un tunnel, l'URL di ngrok cambia a ogni riavvio.",
    });
  }

  if (!response.ok) {
    let detail = "";
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* il corpo non era JSON: resta il codice di stato */
    }
    throw new ApiError(detail || `l'API ha risposto ${response.status}`, {
      status: response.status,
      url,
      hint:
        response.status === 503
          ? "Il dato non è ancora stato prodotto: servono `python -m src.predict_round` e `python -m src.close_round`."
          : null,
    });
  }

  return (await response.json()) as T;
}

/**
 * Come `get`, ma un 404 diventa `null` invece di un errore.
 *
 * Serve alle giornate future: non hanno previsioni registrate, l'API risponde
 * 404 con un messaggio leggibile, e quella e' una risposta legittima. Trattarla
 * come un guasto mostrerebbe un allarme rosso per una situazione normale.
 */
async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const api = {
  health: () => get<Health>("/api/health"),
  status: () => get<Status>("/api/status"),
  season: (code = season()) => get<SeasonSummary>(`/api/season/${code}`),
  rounds: (code = season()) => get<Round[]>(`/api/rounds/${code}`),
  roundMatches: (matchday: number, code = season()) =>
    getOrNull<Match[]>(`/api/rounds/${code}/${matchday}`),
  trackRecord: (code = season()) => get<TrackRecord>(`/api/track-record/${code}`),
  standings: (code = season()) => get<Standings>(`/api/standings/${code}`),
  /** Le principali: nessun parametro di quota, e non e' una dimenticanza. */
  picks: (code = season()) => get<Picks>(`/api/picks/${code}`),
  selections: (minOdds: number, maxOdds: number, code = season()) =>
    get<Selections>(
      `/api/selections/${code}?min_odds=${minOdds}&max_odds=${maxOdds}`,
    ),
};
