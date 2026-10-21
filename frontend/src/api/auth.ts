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
async function post<T>(path: string, payload?: unknown, { renew = false } = {}): Promise<T> {
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

export const signIn = (email: string, password: string) =>
  post<Identity>("/api/auth/sign-in", { email, password });

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
