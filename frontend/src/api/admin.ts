/**
 * The administration panel's calls (`backend/auth/admin.py`).
 *
 * The server decides who may do what on every call, reading the roles from
 * the database: hiding a button here is convenience, never protection. The
 * detail of an account carries `can_manage` and `can_change_roles` so the
 * interface draws only the actions that will be accepted.
 */

import { getJson, post } from "./auth";

export interface AdminUser {
  id: string;
  email: string;
  status: "pending" | "active" | "suspended" | string;
  roles: string[];
  created_at: string;
  email_verified_at: string | null;
  last_seen_at: string | null;
  active_sessions: number;
  locked: boolean;
}

export interface UserPage {
  total: number;
  limit: number;
  offset: number;
  rows: AdminUser[];
}

export interface AdminEvent {
  id: string;
  kind: string;
  outcome: "ok" | "ko" | string;
  at: string;
  ip: string | null;
  user_agent: string | null;
  details: Record<string, unknown> | null;
  user_email: string | null;
  actor_email: string | null;
}

export interface AdminSession {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
}

export interface UserDetail {
  user: AdminUser;
  sessions: AdminSession[];
  events: AdminEvent[];
  can_manage: boolean;
  can_change_roles: boolean;
}

export interface Summary {
  total: number;
  by_status: Record<string, number>;
  new_24h: number;
  new_7d: number;
  new_30d: number;
  admins: number;
  active_sessions: number;
  signups_by_day: { day: string; signups: number }[];
}

export interface UserQuery {
  q?: string;
  status?: string;
  role?: string;
  limit?: number;
  offset?: number;
}

function query(params: UserQuery): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const id = (value: string) => encodeURIComponent(value);

export const admin = {
  summary: () => getJson<Summary>("/api/admin/summary"),
  users: (params: UserQuery = {}) => getJson<UserPage>(`/api/admin/users${query(params)}`),
  user: (userId: string) => getJson<UserDetail>(`/api/admin/users/${id(userId)}`),
  audit: (limit = 50) => getJson<AdminEvent[]>(`/api/admin/audit?limit=${limit}`),

  suspend: (userId: string, reason: string) =>
    post<UserDetail>(`/api/admin/users/${id(userId)}/suspend`, { reason: reason || null }, { renew: true }),
  reactivate: (userId: string) =>
    post<UserDetail>(`/api/admin/users/${id(userId)}/reactivate`, undefined, { renew: true }),
  unlock: (userId: string) =>
    post<UserDetail>(`/api/admin/users/${id(userId)}/unlock`, undefined, { renew: true }),
  grantAdmin: (userId: string) =>
    post<UserDetail>(`/api/admin/users/${id(userId)}/roles/admin/grant`, undefined, { renew: true }),
  revokeAdmin: (userId: string) =>
    post<UserDetail>(`/api/admin/users/${id(userId)}/roles/admin/revoke`, undefined, { renew: true }),
};
