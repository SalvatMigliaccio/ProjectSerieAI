/**
 * L'unico punto che fa `fetch` per i dati. Solo GET: l'API dei dati non ha
 * altri metodi, e non e' una dimenticanza — il track record si scrive dai
 * comandi locali, mai da una richiesta HTTP. (I POST di `/api/auth` stanno in
 * `auth.ts`; il rinnovo della sessione sta qui perche' serve a entrambi.)
 *
 * GLI ERRORI SI RACCONTANO. Una dashboard che resta bianca quando l'API e' giu'
 * fa cercare il problema nei dati, che stanno benissimo. Ogni errore porta con
 * se' l'URL chiamato e un suggerimento su cosa guardare.
 */

import type {
  ExpectedPoints,
  Fixture,
  Health,
  Match,
  MLMatch,
  MLRound,
  MLTrackRecord,
  Picks,
  Players,
  Round,
  SeasonSummary,
  Selections,
  Standings,
  Status,
  TrackRecord,
} from "./types";

/**
 * Stessa origine della pagina, sempre: il proxy di Vite gira `/api` all'API
 * locale (vite.config.ts) e in produzione Caddy fa la stessa cosa.
 *
 * ERA `http://127.0.0.1:8000`, E DALLA FASE 2 NON PUO' PIU' FUNZIONARE. La
 * sessione e' un cookie `SameSite=Lax`: fra `localhost:5173` e
 * `127.0.0.1:8000` il browser vede due siti diversi e quel cookie non lo
 * manda. Il risultato era che l'accesso rispondeva 200, la pagina si diceva
 * autenticata, e ogni richiesta successiva tornava 401 — che l'interfaccia
 * mostra come "non riesco a contattare l'API", cioe' accusando un server che
 * era acceso e rispondeva.
 *
 * Chi ha davvero l'API altrove continua a dirlo con `?api=` o con
 * VITE_API_BASE_URL, che hanno la precedenza qui sotto.
 */
const FALLBACK_BASE = "";

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
 * In sviluppo, precedenza: `?api=` nella query, poi localStorage, poi la
 * variabile d'ambiente di build, infine il locale. In produzione solo le
 * ultime due (vedi sotto).
 *
 * Il parametro nella query serve a provare un tunnel senza ricompilare, e
 * viene ricordato: l'URL di ngrok cambia a ogni riavvio e riscriverlo ogni
 * volta sarebbe la via piu' rapida per incollarlo sbagliato.
 */
export function baseUrl(): string {
  // SOLO IN SVILUPPO, e non e' prudenza generica. Un link con
  // `?api=https://sito-di-qualcuno` cambiava l'API per sempre (localStorage),
  // e da quel momento il modulo di accesso mandava email e password a quel
  // sito: un furto di credenziali con un solo clic, che sopravvive alla
  // chiusura della scheda. In produzione la CSP di Caddy (`connect-src
  // 'self'`) lo fermava, ma una protezione non deve dipendere da un'altra
  // configurata altrove. Una build di produzione parla con la propria origine
  // o con VITE_API_BASE_URL, scelto da chi la compila e non da chi la apre.
  if (!import.meta.env.DEV) {
    // Un valore lasciato da una build precedente, o da quel link, si butta:
    // altrimenti tornerebbe valido alla prima build di sviluppo aperta qui.
    try {
      window.localStorage.removeItem("aiNaplesApiBase");
    } catch {
      /* finestra privata: niente da togliere */
    }
    const configured = import.meta.env.VITE_API_BASE_URL as string | undefined;
    return (configured ?? FALLBACK_BASE).replace(/\/$/, "");
  }

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
  if (stored || configured) return (stored ?? configured ?? "").replace(/\/$/, "");

  // SEMPRE LA STESSA ORIGINE DELLA PAGINA. Da un tunnel o da un altro host,
  // `127.0.0.1:8000` e' il computer di CHI GUARDA: non "quasi giusto",
  // certamente sbagliato. In locale e' un sito diverso da quello della pagina,
  // e il cookie di sessione non parte. In entrambi i casi la risposta e' la
  // stessa, e c'e' gia' chi la gira all'API: il proxy di Vite in sviluppo
  // (vite.config.ts), Caddy in produzione.
  return FALLBACK_BASE;
}

export function season(): string {
  return (import.meta.env.VITE_SEASON as string | undefined) ?? "2627";
}

/**
 * Thrown when the API says the caller is not signed in.
 *
 * A separate type because the interface reacts to it differently: a 401 is not
 * an error to display, it is a reason to show the sign-in screen. Rendering
 * "the API answered 401" to somebody whose session simply expired is how a
 * dashboard turns a routine event into something that looks broken.
 */
export class NotAuthenticated extends ApiError {
  constructor(url: string) {
    super("sessione scaduta", { status: 401, url, hint: null });
    this.name = "NotAuthenticated";
  }
}

/**
 * Announced on the window so the auth layer can react without this module
 * importing it.
 *
 * WHY AN EVENT AND NOT A DIRECT CALL. `client.ts` is the bottom of the stack;
 * importing the React context here would point the dependency the wrong way
 * and make the API client untestable without React. The event keeps the arrow
 * pointing one way, the same rule the Python side follows.
 *
 * WHAT IT FIXES. A session that expires while the dashboard is open otherwise
 * turns every panel into an error box reading "sessione scaduta", with no way
 * out but a manual reload. With this, the app returns to the sign-in screen.
 */
export const UNAUTHENTICATED_EVENT = "ainaples:unauthenticated";

/**
 * Ask the server for a new access token, once, however many callers ask.
 *
 * DUE TOKEN, DALL'ADR 0003. Il JWT di accesso vale 15 minuti e lo legge ogni
 * rotta; il token di rinnovo e' la sessione sul server, vale un giorno e
 * viaggia solo verso `/api/auth`. Quando il primo scade, il secondo ne compra
 * un altro: per chi usa la pagina non succede niente.
 *
 * UNA SOLA RICHIESTA IN VOLO. La dashboard ne fa sette insieme, e tutte e
 * sette tornano 401 nello stesso istante: senza questo partirebbero sette
 * rinnovi. Chi arriva mentre uno e' in corso aspetta quello.
 *
 * Nessun token passa da qui: entrambi sono cookie HttpOnly, il codice della
 * pagina non li vede e non li deve vedere.
 */
let inFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  inFlight ??= fetch(`${baseUrl()}/api/auth/refresh`, {
    method: "POST",
    headers: { Accept: "application/json", "ngrok-skip-browser-warning": "1" },
    credentials: "include",
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

/**
 * `fetch`, and on 401 one refresh and one retry.
 *
 * SI RIPROVA ANCHE SE IL RINNOVO FALLISCE. In quel caso il server ha appena
 * cancellato entrambi i cookie, quindi la seconda richiesta parte anonima: una
 * rotta pubblica risponde 200 con la parte gratuita, una riservata 401 di
 * nuovo — ed e' quel 401 che riporta alla schermata di accesso.
 *
 * Solo dove un 401 vuol dire "token scaduto". Sull'accesso vuol dire
 * "password sbagliata", e rinnovare li' non avrebbe senso: per questo i
 * moduli delle credenziali non passano da qui.
 */
export async function fetchWithRefresh(input: string, init: RequestInit): Promise<Response> {
  const first = await fetch(input, init);
  if (first.status !== 401) return first;
  await refreshSession();
  return fetch(input, init);
}

async function get<T>(path: string): Promise<T> {
  const url = `${baseUrl()}${path}`;
  let response: Response;

  // timeout-feedback: una fetch senza tetto puo' restare appesa per minuti
  // se il tunnel e' caduto a meta' — e la pagina mostra "carico…" per sempre,
  // che e' la cosa meno informativa possibile. Dopo 15 secondi si arrende e
  // dice perche'.
  const controllo = new AbortController();
  const timer = window.setTimeout(() => controllo.abort(), 15000);

  try {
    // `ngrok-skip-browser-warning` non serve a noi e non disturba nessuno: con
    // un account ngrok gratuito, senza, la prima risposta a una `fetch` puo'
    // essere la pagina di avviso del tunnel — HTML dove il codice aspetta
    // JSON, e l'errore parla di sintassi invece che di tunnel.
    response = await fetchWithRefresh(url, {
      headers: { Accept: "application/json", "ngrok-skip-browser-warning": "1" },
      // The session lives in an HttpOnly cookie, and a cross-origin fetch
      // drops cookies unless asked to send them. Without this every request
      // arrives anonymous and the whole dashboard answers 401 while the user
      // is, as far as the browser is concerned, signed in.
      credentials: "include",
      signal: controllo.signal,
    });
  } catch {
    window.clearTimeout(timer);
    if (controllo.signal.aborted) {
      throw new ApiError("l'API non risponde da 15 secondi", {
        url,
        hint: "Il server e' acceso ma bloccato, oppure il tunnel e' caduto. Riavvialo e riprova.",
      });
    }
    throw new ApiError("non riesco a contattare l'API", {
      url,
      hint:
        "Il server è acceso? Prova `python -m backend.api --port 8000`. " +
        "Se usi un tunnel, l'URL di ngrok cambia a ogni riavvio.",
    });
  }

  window.clearTimeout(timer);

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT));
    throw new NotAuthenticated(url);
  }

  if (!response.ok) {
    let detail = "";
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* il corpo non era JSON: resta il codice di stato */
    }
    if (response.status === 429) {
      const attesa = Number(response.headers.get("Retry-After")) || 60;
      throw new ApiError("troppe richieste in poco tempo", {
        status: 429,
        url,
        hint: `Il server ha messo un limite per proteggersi. Riprova tra ${attesa} secondi.`,
      });
    }
    throw new ApiError(detail || `l'API ha risposto ${response.status}`, {
      status: response.status,
      url,
      hint:
        response.status === 503
          ? "Il dato non è ancora stato prodotto: servono `python -m goalmodel.prediction.predict_round` e `python -m goalmodel.prediction.close_round`."
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
  /** Marcatori e assistman: fatti, non previsioni. */
  players: (limit = 10, code = season()) => get<Players>(`/api/players/${code}?limit=${limit}`),
  /** Punti attesi dal modello, sulle sole partite giocate. */
  expectedPoints: (code = season()) => get<ExpectedPoints>(`/api/expected-points/${code}`),
  /** Il modello AI contro il classico, sulle partite giocate. */
  mlTrackRecord: (code = season()) => get<MLTrackRecord>(`/api/ml-track-record/${code}`),
  /** Il calendario di una giornata: le partite prima ancora delle quote. */
  fixtures: (matchday: number, code = season()) =>
    get<Fixture[]>(`/api/fixtures/${code}?matchday=${matchday}`),
  /** Il modello AI (M5) su tutte le partite di una squadra. */
  mlTeam: (team: string, code = season()) =>
    get<MLMatch[]>(`/api/ml-team/${code}?team=${encodeURIComponent(team)}`),
  /** Le partite di una squadra nella stagione, in casa e fuori. */
  teamMatches: (team: string, code = season()) =>
    get<Match[]>(`/api/matches/${code}?team=${encodeURIComponent(team)}`),
  /** M5 su una giornata: null se M5 non e' stato lanciato su quella giornata. */
  ml: (matchday: number, code = season()) =>
    getOrNull<MLRound>(`/api/ml/${code}/${matchday}`),
  /** Le principali: nessun parametro di quota, e non e' una dimenticanza. */
  picks: (code = season()) => get<Picks>(`/api/picks/${code}`),
  /** Stessa regola, gol attesi del modello AI (M5): seconda opinione. */
  picksMl: (code = season()) => get<Picks>(`/api/picks-ml/${code}`),
  selections: (minOdds: number, maxOdds: number, code = season()) =>
    get<Selections>(
      `/api/selections/${code}?min_odds=${minOdds}&max_odds=${maxOdds}`,
    ),
};
