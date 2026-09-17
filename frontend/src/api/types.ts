/**
 * I tipi del contratto, ricalcati da `web/openapi.json`.
 *
 * PERCHE' SCRITTI A MANO E NON GENERATI. Sono sette risposte e stanno in una
 * schermata: un generatore aggiungerebbe una dipendenza e un passo di build per
 * produrre lo stesso file, e renderebbe piu' difficile annotare — come qui
 * sotto — quali campi sono nullable e perche'. Se un giorno gli endpoint
 * diventassero venti, `openapi-typescript` fa esattamente questo lavoro.
 *
 * LA REGOLA CHE QUESTI TIPI IMPONGONO. Tutto cio' che l'API puo' non avere e'
 * `| null`, e TypeScript obbliga a gestirlo: e' la ragione per cui un `rps`
 * mancante non puo' diventare `0` per distrazione. Uno zero li' sarebbe una
 * previsione perfetta, cioe' il contrario di "non lo sappiamo ancora".
 */

/** predicted: in registro, non giocata — resolved: giocata e archiviata —
 *  invalid: giocata, ma la previsione fu scritta dopo il fischio. */
export type MatchStatus = "predicted" | "resolved" | "invalid";

export type RoundStatus =
  | "future"
  | "open"
  | "predicted"
  | "partially_predicted"
  | "played"
  | "closed";

export type Outcome = "H" | "D" | "A";

export interface Match {
  season: string;
  matchday: number;
  kickoff_utc: string | null;
  match_date: string | null;
  home_team: string;
  away_team: string;
  status: MatchStatus;

  p_home: number | null;
  p_draw: number | null;
  p_away: number | null;
  p_over25: number | null;
  p_btts: number | null;
  lambda_home: number | null;
  lambda_away: number | null;
  predicted_outcome: Outcome | null;

  odds_home: number | null;
  odds_draw: number | null;
  odds_away: number | null;
  odds_source: string | null;
  /** 1/p. Descrittiva: serve a leggere la quota del book, non a consigliarla. */
  fair_odds_home: number | null;
  fair_odds_draw: number | null;
  fair_odds_away: number | null;
  /** Margine del book: somma delle probabilita' implicite meno uno. */
  overround: number | null;

  /** Null finche' la partita non e' giocata. Mai 0. */
  goals_home: number | null;
  goals_away: number | null;
  actual_outcome: Outcome | null;
  correct: boolean | null;
  rps: number | null;

  /** False sulle righe scritte dopo il calcio d'inizio: hanno il risultato,
   *  non le metriche, e restano fuori da ogni media. */
  valid: boolean;
  invalid_reason: string | null;

  timestamp_prediction: string | null;
  model_version: string | null;
}

export interface Round {
  season: string;
  matchday: number;
  first_date: string | null;
  last_date: string | null;
  status: RoundStatus | null;
  matches: number | null;
  matches_with_odds: number | null;
  matches_predicted: number | null;
  matches_played: number | null;
  matches_resolved: number;
  rps: number | null;
  rps_market: number | null;
  closed: boolean;
}

export interface Significance {
  method: "cluster-bootstrap" | "insufficient-rounds" | null;
  rounds: number;
  matches: number;
  tolerance: number;
  half_width: number | null;
  rounds_needed: number | null;
  rounds_missing: number | null;
}

export interface SeasonSummary {
  season: string;
  updated_at: string | null;
  model_version: string;
  rounds_total: number | null;
  rounds_closed: number;
  matches_predicted: number;
  matches_resolved: number;
  matches_invalid: number;
  rps_production: number | null;
  /** 0.1881: il mercato sul test set di 1140 partite. */
  rps_backtest_reference: number;
  /** Numeratore e denominatore separati: una percentuale da sola nasconde
   *  quanto e' piccolo il campione, e qui e' piccolissimo. */
  hits: number;
  hits_denominator: number;
  hit_rate: number | null;
  sample_significant: boolean;
  significance: Significance;
}

export interface RoundPoint {
  matchday: number | null;
  matches: number | null;
  matches_valid: number | null;
  rps: number | null;
  rps_market: number | null;
  accuracy: number | null;
  cumulative_matches: number | null;
  cumulative_rps: number | null;
}

export interface MatchPoint {
  kickoff_utc: string | null;
  match_date: string | null;
  home_team: string | null;
  away_team: string | null;
  rps: number | null;
  cumulative_rps: number | null;
}

export interface CalibrationBin {
  bin: string | null;
  n: number | null;
  expected: number | null;
  observed: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export interface TrackRecord {
  season: string;
  updated_at: string | null;
  rps_backtest_reference: number;
  by_round: RoundPoint[];
  by_match: MatchPoint[];
  /** Null sotto le 50 previsioni risolte: una curva su sedici punti e' rumore
   *  disegnato bene, e il motivo arriva nel campo qui sotto. */
  calibration: CalibrationBin[] | null;
  calibration_unavailable_reason: string | null;
}

export interface Selection {
  matchday: number;
  kickoff_utc: string | null;
  home_team: string;
  away_team: string;
  /** Nome grezzo: 1X, over 2.5, casa segna… */
  market: string;
  /** Etichetta leggibile: "1X (Como o pari)". */
  market_label: string;
  probability: number;
  /** 1/p: il prezzo a valore atteso zero. Esiste per ogni mercato. */
  fair_odds: number;
  /** Solo l'1X2 ha una quota registrata. Per gli altri mercati il prezzo vero
   *  ce l'ha il tuo book: stimarlo con un margine medio sarebbe inventarlo. */
  book_odds: number | null;
  status: MatchStatus;
  valid: boolean;
  goals_home: number | null;
  goals_away: number | null;
  /** Null finche' la partita non e' giocata. */
  won: boolean | null;
}

export interface StandingRow {
  position: number;
  team: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goals_for: number;
  goals_against: number;
  goal_difference: number;
  points: number;
}

/** La classifica: aritmetica sui risultati, non una previsione. Le due cose
 *  stanno vicine in pagina proprio perche' non vanno confuse. */
export interface Standings {
  season: string;
  matches_played: number;
  teams: number;
  last_match_date: string | null;
  /** Come sono separate le squadre a pari punti: in Serie A contano prima gli
   *  scontri diretti, non la differenza reti. */
  tie_break: string;
  table: StandingRow[];
}

export interface Picks {
  season: string;
  matchday: number | null;
  /** La soglia dichiarata dal progetto (config.QUOTA_MINIMA_SELEZIONE), non un
   *  parametro della richiesta: la linea principale non si ritaglia a chiamata. */
  min_odds: number;
  matches_total: number;
  most_probable: Selection[];
  most_probable_resolved: number;
  most_probable_won: number;
  with_min_odds: Selection[];
  with_min_odds_resolved: number;
  with_min_odds_won: number;
}

export interface Selections {
  season: string;
  max_odds: number;
  min_odds: number | null;
  matchday: number | null;
  excluded_markets: string[];
  count: number;
  resolved: number;
  won: number;
  /** Partite con una previsione nel perimetro richiesto. */
  matches_total: number;
  /** Di quelle, quante hanno almeno un mercato dentro la banda: una banda
   *  stretta ne lascia scoperte, e il buco va detto. */
  matches_covered: number;
  best_resolved: number;
  best_won: number;
  selections: Selection[];
  /** Una per partita: la piu' probabile dentro la banda. */
  best_per_match: Selection[];
}

export interface LastRun {
  command: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  ok: boolean | null;
  log: string | null;
}

export interface OddsSnapshot {
  exists: boolean;
  empty: boolean | null;
  rows: number | null;
  downloaded_at: string | null;
  age_hours: number | null;
}

export interface NextAction {
  command: string | null;
  matchday: number | null;
  reason: string | null;
}

export interface Status {
  season: string | null;
  updated_at: string | null;
  current_round: number | null;
  current_round_status: RoundStatus | null;
  last_runs: LastRun[];
  odds_snapshot: OddsSnapshot;
  next_action: NextAction;
  warnings: string[];
}

/** Attenzione: NON contiene model_version. Quello sta in SeasonSummary, ed e'
 *  da li' che va preso invece di inventarsi un campo che il contratto non ha. */
export interface Health {
  status: "ok" | "degraded";
  updated_at: string | null;
  seasons: string[];
  predictions_log_exists: boolean;
  matches_logged: number;
  rounds_closed: number;
}
