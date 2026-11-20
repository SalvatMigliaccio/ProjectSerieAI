/**
 * The authentication calls.
 *
 * Separate from `client.ts` because these are the only POSTs in the whole
 * frontend. Everything else is a GET against read-only data, and keeping the
 * distinction visible in the file layout makes it harder to drift.
 *
 * WHY NO TOKEN IS EVER HANDLED HERE. The session is an HttpOnly cookie, which
 * JavaScript cannot read by design: an XSS bug in this application cannot
 * steal it. The price is that `credentials: "include"` has to be on every
 * request, and that the server, not this file, decides when a session ends.
 */

import { ApiError, baseUrl, fetchWithRefresh } from "./client";

export interface Identity {
  id: string;
  email: string;
  status: string;
  roles: string[];
  permissions: string[];
  /** ISO 8601. Optional: an API older than the account page omits them. */
  created_at?: string | null;
  email_verified_at?: string | null;
  /** ADR 0008. Optional: an API older than Google sign-in omits them. */
  has_password?: boolean;
  providers?: string[];
  /** Verifica in due passaggi attiva (ADR 0008, step 3). */
  mfa_enabled?: boolean;
  /** "email", "totp" (app di autenticazione) o null. */
  mfa_method?: string | null;
}

/** L'accesso ha superato il primo passaggio e aspetta il codice: nessuna sessione ancora. */
export interface MfaPending {
  mfa_required: true;
  /** Da dove arriva il codice: per email, o dall'app di autenticazione. */
  method?: "email" | "totp";
  /** La casella mascherata, solo per `email`. */
  sent_to: string | null;
}

export function isMfaPending(x: Identity | MfaPending): x is MfaPending {
  return (x as MfaPending).mfa_required === true;
}

/** Message shapes the API returns on 4xx. */
interface ErrorBody {
  detail?: string | { msg?: string }[];
}

function readDetail(body: ErrorBody | null, fallback: string): string {
  if (!body?.detail) return fallback;
  if (typeof body.detail === "string") return body.detail;
  // FastAPI validation errors arrive as a list of objects. Showing the raw
  // array is how a form ends up displaying "[object Object]".
  const first = body.detail[0]?.msg;
  return first ?? fallback;
}

/**
 * `renew`: on 401, refresh the session and retry — only for the account routes,
 * where a 401 means "access token expired". On the credential forms it means
 * "wrong password" or "link expired", and a refresh there would be noise.
 */
export async function post<T>(path: string, payload?: unknown, { renew = false } = {}): Promise<T> {
  const url = `${baseUrl()}${path}`;
  let response: Response;

  const abort = new AbortController();
  const timer = window.setTimeout(() => abort.abort(), 15000);

  try {
    response = await (renew ? fetchWithRefresh : fetch)(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "include",
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: abort.signal,
    });
  } catch {
    window.clearTimeout(timer);
    throw new ApiError("non riesco a contattare il server", {
      url,
      hint: "Controlla la connessione e riprova.",
    });
  }
  window.clearTimeout(timer);

  if (response.status === 204) return undefined as T;

  let body: ErrorBody | null = null;
  try {
    body = (await response.json()) as ErrorBody;
  } catch {
    /* 204 and empty bodies land here; handled by the status check below */
  }

  if (!response.ok) {
    throw new ApiError(
      readDetail(body, `il server ha risposto ${response.status}`),
      { status: response.status, url, hint: null },
    );
  }
  return body as T;
}

/** `null` rather than throwing: "not signed in" is a normal state at load. */
export async function me(): Promise<Identity | null> {
  const url = `${baseUrl()}/api/auth/me`;
  try {
    const r = await fetchWithRefresh(url, {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    if (r.status === 401) return null;
    if (!r.ok) return null;
    return (await r.json()) as Identity;
  } catch {
    // The server being unreachable is not the same as being signed out, but
    // the interface can do nothing different about it here: the dashboard
    // will report the failure on its own fetches, with a usable message.
    return null;
  }
}

export const signUp = (email: string, password: string) =>
  post<{ detail: string }>("/api/auth/signup", { email, password });

/** 200 con l'identita', oppure 202 con `MfaPending` se l'account ha la verifica in due passaggi. */
export const signIn = (email: string, password: string) =>
  post<Identity | MfaPending>("/api/auth/sign-in", { email, password });

export const signOut = () => post<void>("/api/auth/sign-out");

export const verifyEmail = (token: string) =>
  post<Identity>("/api/auth/verify-email", { token });

export const forgotPassword = (email: string) =>
  post<{ detail: string }>("/api/auth/forgot-password", { email });

export const resetPassword = (token: string, password: string) =>
  post<{ detail: string }>("/api/auth/reset-password", { token, password });

export interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

/**
 * GET JSON from a route that needs a session, with one refresh on 401.
 * A failure carries the status, so a page can tell "not allowed" (403) from
 * "not found" (404) from "server down".
 */
export async function getJson<T>(path: string): Promise<T> {
  const r = await fetchWithRefresh(`${baseUrl()}${path}`, {
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!r.ok) {
    let body: ErrorBody | null = null;
    try {
      body = (await r.json()) as ErrorBody;
    } catch {
      /* not JSON: the status is enough */
    }
    throw new ApiError(readDetail(body, `il server ha risposto ${r.status}`), {
      status: r.status, url: path, hint: null,
    });
  }
  return (await r.json()) as T;
}

/** Where this account is signed in. Never carries the token, by design. */
export async function sessions(): Promise<SessionRow[]> {
  const r = await fetchWithRefresh(`${baseUrl()}/api/auth/sessions`, {
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!r.ok) throw new ApiError("non riesco a leggere le sessioni", {
    status: r.status, url: "/api/auth/sessions", hint: null,
  });
  return (await r.json()) as SessionRow[];
}

export interface ActivityRow {
  kind: string;
  outcome: "ok" | "ko" | string;
  at: string;
  ip: string | null;
  user_agent: string | null;
}

/** The account's own recent audit trail, newest first. Never the `details`. */
export async function activity(): Promise<ActivityRow[]> {
  const r = await fetchWithRefresh(`${baseUrl()}/api/auth/activity`, {
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!r.ok) throw new ApiError("non riesco a leggere l'attività", {
    status: r.status, url: "/api/auth/activity", hint: null,
  });
  return (await r.json()) as ActivityRow[];
}

export const revokeOtherSessions = () =>
  post<{ revoked: number }>("/api/auth/sessions/revoke-others", undefined, { renew: true });

/** Sign out one other device. The current session is refused: that is sign-out. */
export const revokeSession = (id: string) =>
  post<{ revoked: number }>(`/api/auth/sessions/${encodeURIComponent(id)}/revoke`, undefined, { renew: true });

export const changePassword =(currentPassword: string, newPassword: string) =>
  post<void>("/api/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  }, { renew: true });

// --- Accesso con Google (ADR 0008) ---------------------------------------------

export interface Providers {
  google: boolean;
  apple: boolean;
}

/** Quali pulsanti disegnare. Un errore vale "nessuno": il modulo con la password resta. */
export async function providers(): Promise<Providers> {
  try {
    const r = await fetch(`${baseUrl()}/api/auth/providers`, {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    if (!r.ok) return { google: false, apple: false };
    return (await r.json()) as Providers;
  } catch {
    return { google: false, apple: false };
  }
}

export type OAuthIntent = "sign_in" | "link" | "reauth";

/**
 * L'indirizzo che fa partire il giro da Google. Si NAVIGA, non si fa fetch:
 * il ritorno arriva come pagina intera, con i cookie gia' impostati dal server.
 * Nessun token di Google passa mai da questo codice.
 */
export function oauthStartUrl(provider: "google", intent: OAuthIntent, next?: string): string {
  const q = new URLSearchParams({ intent });
  if (next) q.set("next", next);
  return `${baseUrl()}/api/auth/oauth/${provider}/start?${q.toString()}`;
}

/**
 * Il codice che il server mette in `?oauth_error=`, in una frase che dice cosa
 * fare. Un codice sconosciuto ha comunque una frase: il server ne puo'
 * aggiungere uno prima che qualcuno la scriva qui.
 */
export function oauthErrorMessage(code: string | null): string | null {
  if (!code) return null;
  const messaggi: Record<string, string> = {
    cancelled: "Accesso con Google annullato.",
    state: "L'accesso con Google è scaduto o è stato aperto in un'altra scheda. Riprova da qui.",
    provider: "Google non ha risposto come previsto. Riprova fra qualche minuto.",
    rate_limited: "Troppi tentativi. Aspetta qualche minuto e riprova.",
    session: "La sessione è scaduta: accedi di nuovo e ripeti l'operazione.",
    email_unverified: "L'email del tuo account Google non è verificata: verificala su Google, oppure registrati con email e password.",
    link_pending: "Esiste già un account con questa email, ma non è ancora confermato. Usa «Password dimenticata» per attivarlo con una password tua, poi collega Google dal profilo.",
    link_required: "Esiste già un account con questa email. Entra con la password e collega Google dal tuo profilo.",
    inactive: "Questo account non è attivo.",
    other_google: "Il tuo account è già collegato a un altro account Google.",
    linked_elsewhere: "Questo account Google è già collegato a un altro utente.",
    mismatch: "Hai confermato con un account Google diverso da quello collegato al profilo.",
    not_configured: "L'accesso con Google non è disponibile.",
  };
  return messaggi[code] ?? "Accesso con Google non riuscito. Riprova.";
}

export interface LinkedIdentity {
  provider: string;
  email: string | null;
  linked_at: string;
  last_used_at: string | null;
}

export const identities = () => getJson<LinkedIdentity[]>("/api/auth/identities");

/** 409 se e' l'unico modo di entrare: il messaggio del server lo dice. */
export const unlinkProvider = (provider: string) =>
  post<{ unlinked: number }>(`/api/auth/identities/${encodeURIComponent(provider)}/unlink`,
    undefined, { renew: true });

// --- Verifica in due passaggi (ADR 0008, step 3) ---------------------------------

/** Per la schermata del codice: a quale casella e' andato. `null` se non c'e' niente in attesa. */
export async function mfaPending(): Promise<MfaPending | null> {
  try {
    const r = await fetch(`${baseUrl()}/api/auth/mfa/pending`, {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    return r.ok ? ((await r.json()) as MfaPending) : null;
  } catch {
    return null;
  }
}

export const verifyMfa = (code: string) => post<Identity>("/api/auth/mfa/verify", { code });
export const resendMfa = () => post<MfaPending>("/api/auth/mfa/resend");

export const mfaStart = (azione: "enable" | "disable") =>
  post<MfaPending>(`/api/auth/mfa/${azione}/start`, undefined, { renew: true });
export const mfaConfirm = (azione: "enable" | "disable", code: string) =>
  post<Identity>(`/api/auth/mfa/${azione}/confirm`, { code }, { renew: true });

// --- App di autenticazione (TOTP) -------------------------------------------------

export interface MfaStatus {
  method: string | null;
  enabled_at: string | null;
  recovery_codes_left: number | null;
}

export interface TotpSetup {
  /** Il segreto a gruppi di 4, da scrivere a mano se la fotocamera non legge il QR. */
  secret: string;
  otpauth_uri: string;
  /** Il QR code come immagine `data:image/svg+xml`. */
  qr: string;
}

export interface TotpEnabled {
  identity: Identity;
  /** Mostrati UNA volta: il server ne tiene solo l'impronta. */
  recovery_codes: string[];
}

export const mfaStatus = () => getJson<MfaStatus>("/api/auth/mfa/status");
export const totpSetup = () =>
  post<TotpSetup>("/api/auth/mfa/totp/setup", undefined, { renew: true });
/** La password se l'account ce l'ha; senza, il server guarda la riconferma con Google. */
export const totpConfirm = (code: string, password?: string) =>
  post<TotpEnabled>("/api/auth/mfa/totp/confirm", { code, password }, { renew: true });
export const totpNewRecoveryCodes = (code: string) =>
  post<TotpEnabled>("/api/auth/mfa/totp/recovery-codes", { code }, { renew: true });
