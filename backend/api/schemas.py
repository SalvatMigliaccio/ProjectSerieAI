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
      resolved   played, archived, metrics computed  -> everything valued
      invalid    played, but written after kickoff   -> result yes, metrics null

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
    status: str = Field(description="predicted | resolved | invalid")

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
