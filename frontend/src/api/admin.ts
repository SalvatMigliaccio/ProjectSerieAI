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

export interface Count {
  label: string;
  count: number;
}

export interface Metrics {
  active_24h: number;
  active_7d: number;
  active_30d: number;
  signups_30d: number;
  verified_30d: number;
  pending_stale: number;
  failed_sign_ins_24h: number;
  failed_sign_ins_7d: number;
  failed_ips_24h: number;
  locked_now: number;
  failure_causes_7d: Count[];
  users_with_favorite: number;
  favorite_teams: Count[];
  pick_season: string | null;
  users_with_picks: number;
  picks_total: number;
  picks_7d: number;
  picks_by_market: Count[];
  picks_by_matchday: Count[];
  daily: { day: string; sign_ins: number; failed: number; picks: number }[];
}

export interface Job {
  id: string;
  command: "predict_round" | "close_round" | string;
  options: { dry_run?: boolean; skip_ingest?: boolean };
  status: "queued" | "running" | "done" | "failed" | "cancelled" | string;
  requested_by: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  worker: string | null;
  exit_code: number | null;
  /** Un job "running" il cui worker non scrive da oltre 10 minuti. */
  stale: boolean;
  log_tail: string | null;
}

export interface Worker {
  name: string;
  host: string | null;
  started_at: string;
  last_seen_at: string;
  poll_seconds: number;
  online: boolean;
}

export interface PipelineState {
  workers: Worker[];
  any_online: boolean;
  active: Job | null;
  jobs: Job[];
  can_launch: boolean;
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
  metrics: () => getJson<Metrics>("/api/admin/metrics"),
  users: (params: UserQuery = {}) => getJson<UserPage>(`/api/admin/users${query(params)}`),
  user: (userId: string) => getJson<UserDetail>(`/api/admin/users/${id(userId)}`),
  audit: (limit = 50) => getJson<AdminEvent[]>(`/api/admin/audit?limit=${limit}`),
  pipeline: () => getJson<PipelineState>("/api/admin/pipeline"),
  job: (jobId: string) => getJson<Job>(`/api/admin/pipeline/jobs/${id(jobId)}`),
  launch: (command: string, options: { dry_run?: boolean; skip_ingest?: boolean }) =>
    post<PipelineState>("/api/admin/pipeline/jobs", { command, options }, { renew: true }),
  cancelJob: (jobId: string) =>
    post<PipelineState>(`/api/admin/pipeline/jobs/${id(jobId)}/cancel`, undefined, { renew: true }),

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
