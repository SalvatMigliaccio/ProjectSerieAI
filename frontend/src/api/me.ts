/**
 * The signed-in user's own data: favourite team and "I miei pronostici".
 *
 * Every call needs a session and renews it once on 401, like the account
 * routes. The types mirror `backend/auth/me.py`; `frontend/openapi.json` is the
 * contract they come from.
 */

import { getJson, post } from "./auth";
import { season } from "./client";

export interface Preferences {
  favorite_team: string | null;
}

export interface MarketOption {
  market: string;
  label: string;
  group: string;
  probability: number;
  fair_odds: number | null;
  picked: boolean;
}

export interface OpenMatch {
  season: string;
  matchday: number;
  home_team: string;
  away_team: string;
  kickoff_utc: string;
  lambda_home: number;
  lambda_away: number;
  markets: MarketOption[];
}

export interface Pick {
  id: string;
  season: string;
  matchday: number;
  home_team: string;
  away_team: string;
  kickoff_utc: string | null;
  market: string;
  label: string;
  model_probability: number | null;
  fair_odds: number | null;
  created_at: string;
  locked: boolean;
  goals_home: number | null;
  goals_away: number | null;
  won: boolean | null;
}

export interface PicksSummary {
  picks: number;
  pending: number;
  resolved: number;
  won: number;
  hit_rate: number | null;
  mean_model_probability: number | null;
}

export interface MyPredictions {
  summary: PicksSummary;
  picks: Pick[];
}

export const me = {
  preferences: () => getJson<Preferences>("/api/me/preferences"),
  setFavorite: (team: string | null) =>
    post<Preferences>("/api/me/preferences", { favorite_team: team }, { renew: true }),
  openMatches: (code = season()) => getJson<OpenMatch[]>(`/api/me/predictions/${code}/open`),
  predictions: (code = season()) => getJson<MyPredictions>(`/api/me/predictions/${code}`),
  addPick: (home: string, away: string, market: string, code = season()) =>
    post<{ id: string }>(
      "/api/me/predictions",
      { season: code, home_team: home, away_team: away, market },
      { renew: true },
    ),
  deletePick: (id: string) =>
    post<{ deleted: number }>(`/api/me/predictions/${encodeURIComponent(id)}/delete`, undefined, {
      renew: true,
    }),
};
