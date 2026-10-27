"""
Pydantic models: one per response, and the place where the null contract lives.

THE RULE. Anything that can be missing is `X | None = None`, never `0` and
never `""`. A match that has not been played has `rps: null`, not `rps: 0.0` —
zero is a perfect prediction, and a frontend that plots it would draw a
triumph where there is no data.

AND THE NULLS MUST TRAVEL. Nothing here sets `exclude_none`: an absent key and
a null key are different things, and the frontend is entitled to see the key.
It is the same distinction `features/context.py` already makes when it refuses
to emit derby columns rather than filling them with zeros.

`protected_namespaces=()` is not decoration: Pydantic v2 reserves the `model_`
prefix, and `model_version` — which is the name the track record has used
since the first row was written — would raise a warning at import without it.
Renaming the field to please the library would break the log's own vocabulary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

class Match(Base):
    """
    One prediction, with its result when there is one.

    `status` is the field to branch on:
      predicted  in the log, not played yet          -> every result field null
      played     played, round not closed yet        -> result yes, metrics null
      resolved   played, archived, metrics computed  -> everything valued
      invalid    played, but written after kickoff   -> result yes, metrics null

    `played` is PROVISIONAL: the score comes from the same source chain
    `close_round` uses, the evening of the match, but it is not on record
    until the round is closed. football-data may still publish an official
    score that differs (a match awarded 0-3), and metrics are computed only
    from the archive.

    `invalid` is not a bug to hide: the log is append-only and keeps its own
    mistakes. `valid` and `invalid_reason` exist so the frontend can say why a
    played match has no RPS instead of showing an unexplained hole.
    """

    season: str = Field(description="Public form, e.g. 2026-27")
    matchday: int
    kickoff_utc: str | None = Field(
        None, description="ISO 8601 UTC. Null when the match is not in the calendar")
    match_date: str | None = Field(
        None, description="ISO 8601 UTC midnight of the scheduled day, as logged")
    home_team: str
    away_team: str
    status: str = Field(description="predicted | played | resolved | invalid")

    p_home: float | None = None
    p_draw: float | None = None
    p_away: float | None = None
    p_over25: float | None = None
    p_btts: float | None = None
    lambda_home: float | None = None
    lambda_away: float | None = None
    predicted_outcome: str | None = Field(None, description="H, D or A")

    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    odds_source: str | None = Field(
        None, description="Book and provenance, e.g. B365-fixtures")
    fair_odds_home: float | None = Field(None, description="1/p, descriptive only")
    fair_odds_draw: float | None = None
    fair_odds_away: float | None = None
    overround: float | None = Field(
        None, description="Book margin: sum of implied probabilities minus one")

    goals_home: int | None = None
    goals_away: int | None = None
    actual_outcome: str | None = None
    result_source: str | None = Field(
        None, description="football-data, understat or fbref. Null when unplayed, "
                          "or archived before the source was recorded")
    correct: bool | None = None
    rps: float | None = Field(
        None, description="Ranked Probability Score, lower is better. Null if "
                          "the match is unplayed or the row is invalid")
    valid: bool = Field(description="False if written after kickoff")
    invalid_reason: str | None = None

    timestamp_prediction: str | None = None
    model_version: str | None = None


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------

class Round(Base):
    """
    A matchday and where it stands.

    `status` is translated from the project's single state machine
    (`rounds.stato_giornate`), never recomputed here: future, open, predicted,
    partially_predicted, played, closed.
    """

    season: str
    matchday: int
    first_date: str | None = None
    last_date: str | None = None
    status: str | None = None
    matches: int | None = None
    matches_with_odds: int | None = None
    matches_predicted: int | None = None
    matches_played: int | None = None
    matches_resolved: int = 0
    rps: float | None = Field(None, description="Archived RPS, null unless closed")
    rps_market: float | None = None
    closed: bool


# ---------------------------------------------------------------------------
# Season
# ---------------------------------------------------------------------------

class Significance(Base):
    """
    How far the track record is from meaning something.

    The bootstrap resamples ROUNDS, not matches: the ten matches of a round are
    predicted by the same fit, and treating them as independent would shrink
    the interval by about sqrt(10) and make anything look significant.
    Under four rounds there is nothing to resample, and the method says so.
    """

    method: str | None = Field(
        None, description="cluster-bootstrap | insufficient-rounds")
    rounds: int
    matches: int
    tolerance: float = Field(
        description="RPS gap from the backtest worth detecting (config.TRACK_TOLERANCE_RPS)")
    half_width: float | None = None
    rounds_needed: int | None = None
    rounds_missing: int | None = None


class SeasonSummary(Base):
    """
    Headline figures. Hits keep numerator and denominator apart on purpose: a
    bare rate hides how thin the sample is, and right now it is very thin.

    `rps_backtest_reference` is 0.1881, the market's RPS on the test set. A
    persistent gap from it would not be a model getting things wrong — the
    model *is* the opening line — it would be the production pipeline behaving
    differently from the backtest.
    """

    season: str
    updated_at: str | None = None
    model_version: str
    rounds_total: int | None = None
    rounds_closed: int = 0
    matches_predicted: int = 0
    matches_resolved: int = 0
    matches_invalid: int = 0
    rps_production: float | None = None
    rps_backtest_reference: float
    hits: int = 0
    hits_denominator: int = 0
    hit_rate: float | None = None
    sample_significant: bool
    significance: Significance


# ---------------------------------------------------------------------------
# Track record
# ---------------------------------------------------------------------------

class RoundPoint(Base):
    matchday: int | None = None
    matches: int | None = None
    matches_valid: int | None = None
    rps: float | None = None
    rps_market: float | None = None
    accuracy: float | None = None
    cumulative_matches: int | None = None
    cumulative_rps: float | None = None


class MatchPoint(Base):
    kickoff_utc: str | None = None
    match_date: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    rps: float | None = None
    cumulative_rps: float | None = None


class CalibrationBin(Base):
    bin: str | None = None
    n: int | None = None
    expected: float | None = None
    observed: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None


class TrackRecord(Base):
    season: str
    updated_at: str | None = None
    rps_backtest_reference: float
    by_round: list[RoundPoint] = []
    by_match: list[MatchPoint] = []
    calibration: list[CalibrationBin] | None = Field(
        None, description="Null below 50 resolved predictions: fewer points "
                          "make a curve that is noise drawn nicely")
    calibration_unavailable_reason: str | None = None


# ---------------------------------------------------------------------------
# Status and health
# ---------------------------------------------------------------------------

class Selection(Base):
    """
    One market of one match, priced by the model.

    `fair_odds` is 1/p — the price at which the bet would break even. It exists
    for every market. `book_odds` exists only for 1X2, because that is all the
    register stores: for double chance, over/under and goal markets the price
    has to come from the reader's own bookmaker, and estimating it by applying
    an average margin would be a made-up number that looks measured.
    """

    matchday: int
    kickoff_utc: str | None = None
    home_team: str
    away_team: str
    market: str = Field(description="Raw market name, e.g. 1X, over 2.5, casa segna")
    market_label: str = Field(description="Readable label, e.g. '1X (Como o pari)'")
    probability: float
    fair_odds: float
    book_odds: float | None = Field(
        None, description="Registered bookmaker price. Only 1X2 has one")
    status: str
    valid: bool
    goals_home: int | None = None
    goals_away: int | None = None
    won: bool | None = Field(None, description="Null while the match is unplayed")


class StandingRow(Base):
    position: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: list[str] = Field(
        [], description="Ultimi risultati in ordine di data, dal piu' vecchio: "
                        "W, D, L. Dai totali non si ricava — la sequenza e' "
                        "proprio l'informazione che interessa")


class Standings(Base):
    """
    La classifica: aritmetica sui risultati, non una previsione.

    `tie_break` dichiara come sono separate le squadre a pari punti, perche' in
    Serie A contano prima gli scontri diretti e non la differenza reti — una
    tabella ordinata nel modo sbagliato sembra giusta proprio nelle settimane
    in cui qualcuno la guarda.
    """

    season: str
    matches_played: int
    teams: int
    last_match_date: str | None = None
    tie_break: str
    table: list[StandingRow] = []


class Scorer(Base):
    position: int
    player: str
    team: str
    goals: int
    penalties: int = Field(description="Of which from the spot")
    shots: int
    xg: float = Field(description="xG of every shot, scored or not")


class AssistMan(Base):
    position: int
    player: str
    team: str
    assists: int
    key_passes: int = Field(description="Passes that led to a shot")
    xa: float = Field(description="xG of the shots the player set up")


class Players(Base):
    """
    Top scorers and assist men: facts, like the table. From Understat's shots,
    own goals excluded; an assist is the last pass before a goal.
    """

    season: str
    matches: int
    source: str
    scorers: list[Scorer] = []
    assists: list[AssistMan] = []


class ExpectedPointsRow(Base):
    team: str
    position: int
    played: int
    points: int
    points_per_game: float | None = None
    predicted_matches: int = Field(
        0, description="Played matches with a prediction in the log")
    expected_points: float | None = Field(
        None, description="Sum of 3*p(win) + p(draw) over those matches")
    points_in_predicted: int | None = Field(
        None, description="Points actually taken in the same matches")
    expected_points_per_game: float | None = None
    projected_points: float | None = Field(
        None, description="Points so far + expected per game x games left. "
                          "An extrapolation: remaining opponents are ignored")


class ExpectedPoints(Base):
    """
    Expected points per team from the production model, on played matches
    only: the upcoming round's probabilities stay behind `picks:read`.
    """

    season: str
    total_games: int
    model_version: str
    table: list[ExpectedPointsRow] = []


class MarketHit(Base):
    key: str
    label: str
    outcomes: int = Field(description="How many outcomes the market has: 3 for 1X2")
    hits: int
    resolved: int
    base_rate: float | None = Field(
        None, description="How often the 'yes' side happened on the same matches. "
                          "The yardstick: 'home team scores' happens 3 times in 4 anyway")


class RoundHits(Base):
    matchday: int
    hits: int
    resolved: int = Field(description="Matches x markets in the round")


class MarketHits(Base):
    """
    How often the most likely side of each market came out, on the closed
    rounds — the same matches as the official 1X2 count.
    """

    season: str
    matches: int
    markets: list[MarketHit] = []
    by_round: list[RoundHits] = Field(
        [], description="Per closed round, hits summed over every market")


class Picks(Base):
    """
    The canonical selections. No threshold field to move, by design.

    `most_probable` is the most likely market of each match, whatever it pays;
    `with_min_odds` is the most likely among those paying at least `min_odds`,
    which comes from `config.QUOTA_MINIMA_SELEZIONE` and not from the request.
    Both are the rules the project has used since the first registered round.
    """

    season: str
    matchday: int | None = None
    min_odds: float = Field(
        description="The declared floor, config.QUOTA_MINIMA_SELEZIONE. Not a "
                    "request parameter: the main line cannot be re-cut per call")
    matches_total: int = 0
    most_probable: list[Selection] = []
    most_probable_resolved: int = 0
    most_probable_won: int = 0
    with_min_odds: list[Selection] = []
    with_min_odds_resolved: int = 0
    with_min_odds_won: int = 0


class Selections(Base):
    """
    Selections inside an odds band, with hits and their denominator.

    The numbers travel together on purpose: "10 su 11" says what "91%" hides,
    which is that eleven bets decide nothing. Filtering by odds moves variance,
    not edge — M1's probabilities are the book's own line with the margin
    removed, so expected value is minus the margin at every threshold.
    """

    season: str
    max_odds: float
    min_odds: float | None = None
    matchday: int | None = None
    excluded_markets: list[str] = Field(
        [], description="Near-certainties never returned: no book prices them "
                        "high enough to be a bet, and at a low ceiling they "
                        "would crowd out everything else")
    count: int
    resolved: int
    won: int
    matches_total: int = Field(
        0, description="Matches with a prediction in the requested scope")
    matches_covered: int = Field(
        0, description="Of those, how many have at least one market inside the "
                       "band. A narrow band leaves matches uncovered, and the "
                       "gap is reported rather than hidden")
    best_resolved: int = 0
    best_won: int = 0
    selections: list[Selection] = []
    best_per_match: list[Selection] = Field(
        [], description="One selection per match: the most probable inside the "
                        "band, highest odds breaking a tie. Recomputed from the "
                        "stored lambdas, never frozen into the register")


class LastRun(Base):
    command: str
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    ok: bool | None = None
    log: str | None = None


class OddsSnapshot(Base):
    """
    The upcoming-odds file. An EMPTY snapshot is a normal state between rounds,
    not a failure: football-data answers but has not published the round yet.
    """

    exists: bool
    empty: bool | None = None
    rows: int | None = None
    downloaded_at: str | None = None
    age_hours: float | None = None


class NextAction(Base):
    command: str | None = Field(
        None, description="predict_round | close_round | null when idle")
    matchday: int | None = None
    reason: str | None = None


class Status(Base):
    """
    Enough for a frontend to show that the system is alive — and no way to
    command it. Writing happens only from local processes.
    """

    season: str | None = None
    updated_at: str | None = None
    current_round: int | None = None
    current_round_status: str | None = None
    last_runs: list[LastRun] = []
    odds_snapshot: OddsSnapshot
    next_action: NextAction
    warnings: list[str] = []


class Health(Base):
    status: str = Field(description="ok | degraded")
    updated_at: str | None = None
    seasons: list[str] = []
    predictions_log_exists: bool
    matches_logged: int
    rounds_closed: int


class ErrorDetail(Base):
    detail: str


# ---------------------------------------------------------------------------
# The machine-learning model (M5), as a second opinion
# ---------------------------------------------------------------------------

class MLMarket(Base):
    """One market of one match: M5 next to the market it starts from."""

    group: str = Field(description="1X2, Doppia chance, Gol / No gol, Under / Over x.5")
    market: str = Field(description="Raw name from all_markets, e.g. over 2.5")
    label: str
    p_ml: float = Field(description="Probability according to M5")
    p_market: float = Field(description="Probability according to the de-vigged market (M1)")
    difference: float = Field(description="p_ml - p_market")
    fair_odds_ml: float | None = Field(None, description="1/p_ml, descriptive only")
    pick: bool = Field(description="The most likely market of its group according to M5")
    won: bool | None = Field(None, description="Null while the match is unplayed")


class MLMatch(Base):
    matchday: int
    home_team: str
    away_team: str
    kickoff_utc: str | None = None
    status: str | None = Field(None, description="Same vocabulary as Match.status")
    goals_home: int | None = None
    goals_away: int | None = None
    lambda_home_ml: float
    lambda_away_ml: float
    lambda_home_market: float
    lambda_away_market: float
    max_divergence_1x2: float = Field(
        description="Largest absolute gap between M5 and the market on 1, X, 2")
    markets: list[MLMarket]


class MLRound(Base):
    """
    M5 on a matchday. DIAGNOSTICS, not the production model.

    On the test set M5 is indistinguishable from the market, so the track
    record stays on M1. Nothing here feeds a metric.
    """

    season: str
    matchday: int
    model: str
    written_at: str | None = Field(None, description="When M5 wrote the earliest row")
    matches: list[MLMatch]


class Fixture(Base):
    """
    A match of the calendar, predicted or not.

    Not a track record row: no probabilities, no metrics. `predicted` says
    whether the model has priced it yet — before Friday's odds it has not, and
    the match still exists, which is the point of this shape.
    """

    season: str
    matchday: int
    home_team: str
    away_team: str
    kickoff_utc: str | None = Field(description="ISO 8601, UTC; null if the calendar has no time")
    predicted: bool
    goals_home: int | None = None
    goals_away: int | None = None


class MLRoundScore(Base):
    matchday: int
    matches: int
    rps_classic: float
    rps_ai: float


class MLMatchScore(Base):
    matchday: int
    home_team: str
    away_team: str
    goals_home: int | None
    goals_away: int | None
    rps_classic: float
    rps_ai: float


class MLTrackRecord(Base):
    """
    The AI model (M5) against the classic one (M1), on the same played matches.

    Diagnostics, not the track record: the production model stays M1.
    `difference` is AI minus classic, and RPS is minimised, so NEGATIVE means
    the AI did better. `ci_low`/`ci_high` are the paired bootstrap interval
    clustered on the matchday; null with fewer than two rounds.
    """

    season: str
    matches: int
    n_rounds: int
    rps_classic: float | None
    rps_ai: float | None
    difference: float | None
    ci_low: float | None
    ci_high: float | None
    ai_better: int
    classic_better: int
    rounds: list[MLRoundScore]
    per_match: list[MLMatchScore]
