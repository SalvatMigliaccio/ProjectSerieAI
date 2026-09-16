"""
The only module in the API that opens files. Everything else reads from here.

WHY A SINGLE READER. The routes must not know that the track record is a pile
of CSVs: the day it becomes something else, one module changes. More
importantly, every path is a parameter (`Sources`), so the tests run against a
copy in `tmp_path` instead of the real track record. That is not paranoia —
`test_form` once overwrote `features_form.parquet` with 90 synthetic rows and
nothing raised.

READ ONLY, AND NO MODELS. Nothing here writes. Heavy project modules are
imported INSIDE the functions that need them, so `import backend.api.store` pulls
in pandas and nothing else: `tests/test_api.py` asserts that `lightgbm` is
absent from `sys.modules` after importing the app, and lazy imports are what
keep that assertion honest.

WHAT IT READS, AND WHY THE PERIMETER IS WIDER THAN track_record/
  track_record/predictions_log.csv   predictions, append-only
  track_record/rounds/round_*.csv    closed rounds, with results and metrics
  track_record/rounds/riepilogo.csv  per-round summary, cumulative RPS
  data/raw/fbref_schedule.parquet    kickoff times — NOWHERE ELSE
  data/interim/matches_master.parquet  results, needed by the round state machine
  data/raw/fixtures_odds.parquet     odds snapshot age, for /api/status

The last three are the reason the perimeter is not just `track_record/`: the
log stores `match_date` (a date), never a kickoff time, and the round state
machine lives in `rounds.stato_giornate()`. Both are read-only here.

THE FIELD NAMES ARE ENGLISH ON PURPOSE. This module is the contract towards
the frontend, and the payload keys (`season`, `kickoff_utc`, `overround`) are
English. The rest of the project stays Italian; when this module calls it, the
Italian names stay as they are (`rounds.stato_giornate`, `predict.kickoff`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from src import config

log = logging.getLogger("api.store")

KEYS = ["league", "season", "home_team", "away_team"]
PROB_COLS = ["p_home", "p_draw", "p_away"]
OUTCOMES = ("H", "D", "A")

# The production model. The log is keyed by (match, model_version): filtering
# here means a second opinion logged one day would not be double counted.
PRODUCTION_MODEL = "M1 market-only (lambda)"

# Closed round files carry no `league` column, so matches are joined on
# (season, home_team, away_team) and the league is filled in from the log.
DEFAULT_LEAGUE = "ITA-Serie A"

# Per-match status. Three values, and no more: a match that has not been
# predicted yet does not exist in the track record, so there is no "scheduled".
STATUS_PREDICTED = "predicted"   # in the log, not played yet
STATUS_RESOLVED = "resolved"     # played, archived, metrics available
STATUS_INVALID = "invalid"       # played, archived, but written after kickoff

INVALID_REASON = (
    "prediction written after kickoff: kept in the log, excluded from metrics"
)

# Round states come from `rounds.stato_giornate()`, which speaks Italian.
# Translated once, here, so the contract stays English.
ROUND_STATUS = {
    "futura": "future",
    "aperta": "open",
    "predetta": "predicted",
    "predetta in parte": "partially_predicted",
    "giocata": "played",
    "chiusa": "closed",
}

# Below this many resolved predictions a calibration curve is noise drawn
# nicely. Same threshold `report.py` already uses.
CALIBRATION_MIN_MATCHES = 50


# ---------------------------------------------------------------------------
# Sources: every path is a parameter, so tests can point elsewhere
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sources:
    """Where the data lives. Frozen because it is an `lru_cache` key."""

    predictions_log: Path
    rounds_dir: Path
    rounds_summary: Path
    schedule: Path
    master: Path
    odds_snapshot: Path

    @classmethod
    def at(cls, track_record: Path, data: Path) -> "Sources":
        return cls(
            predictions_log=track_record / "predictions_log.csv",
            rounds_dir=track_record / "rounds",
            rounds_summary=track_record / "rounds" / "riepilogo.csv",
            schedule=data / "raw" / "fbref_schedule.parquet",
            master=data / "interim" / "matches_master.parquet",
            odds_snapshot=data / "raw" / "fixtures_odds.parquet",
        )


def default_sources() -> Sources:
    return Sources.at(config.TRACK_RECORD, config.DATA)


# ---------------------------------------------------------------------------
# Null discipline. A missing value is None, never 0 and never ""
# ---------------------------------------------------------------------------

def _opt_float(value) -> float | None:
    """NaN/NaT/None -> None; numpy scalar -> native float."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _opt_int(value) -> int | None:
    """FTHG is 3.0, not 3: cast only after NaN is ruled out."""
    out = _opt_float(value)
    return None if out is None else int(round(out))


def _opt_str(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _opt_bool(value) -> bool | None:
    """
    `azzeccato` is stored as 1.0 / 0.0 / empty — a float, not a boolean.

    `bool(float('nan'))` is True, so a match with no metrics would come out as
    "correct" if NaN were not ruled out first. That is the whole reason this
    helper exists.
    """
    out = _opt_float(value)
    return None if out is None else bool(out)


def _iso(value) -> str | None:
    """Any timestamp -> ISO 8601 in UTC. Naive input is assumed to be UTC."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def season_out(code: str) -> str:
    """Internal '2627' -> public '2026-27'."""
    code = str(code).strip()
    if len(code) == 4 and code.isdigit():
        start = int(code[:2])
        century = 2000 if start < 90 else 1900
        return f"{century + start}-{code[2:]}"
    return code


def season_in(label: str) -> str:
    """Public '2026-27' (or '2627') -> internal '2627'. Both are accepted."""
    text = str(label).strip()
    if "-" in text:
        left, right = text.split("-", 1)
        return f"{left[-2:]}{right[-2:]}"
    return text


# ---------------------------------------------------------------------------
# Loading, cached on file mtimes
# ---------------------------------------------------------------------------

def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def _fingerprint(src: Sources) -> tuple:
    """
    Cache key: (path, mtime, size) of every source, round files included.

    No time-based TTL. The data does not change on a schedule, it changes when
    `predict_round` or `close_round` writes — and the mtime knows exactly when.
    The round directory is listed as well, because closing a round ADDS a file
    without touching any existing one.
    """
    items = [_stat(src.predictions_log), _stat(src.rounds_summary),
             _stat(src.schedule), _stat(src.master), _stat(src.odds_snapshot)]
    if src.rounds_dir.is_dir():
        items.extend(_stat(p) for p in sorted(src.rounds_dir.glob("round_*.csv")))
    return tuple(items)


@dataclass(frozen=True)
class Snapshot:
    predictions: pd.DataFrame   # deduplicated log, production model only
    closed: pd.DataFrame        # every archived round file, concatenated
    summary: pd.DataFrame       # riepilogo.csv as it is
    kickoffs: dict              # (season, home, away) -> UTC Timestamp
    updated_at: str | None      # newest mtime among track record files


def _read_predictions(src: Sources) -> pd.DataFrame:
    """
    The log, one row per (match, model), keeping the FIRST by timestamp.

    `backtest_log.load_log` already does that and explains why: the log is
    append-only, re-running a round appends rows, and the most recent row is
    also the best informed one. Taking it would flatter the track record.
    """
    from src.backtest_log import load_log

    try:
        df = load_log(path=src.predictions_log, first_only=True)
    except FileNotFoundError:
        log.info("no predictions log at %s", src.predictions_log)
        return pd.DataFrame()

    df = df[df["model_version"] == PRODUCTION_MODEL].copy()
    df["season"] = df["season"].astype(str)
    df["matchday"] = df["matchday"].astype(int)
    df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce")
    return df.reset_index(drop=True)


def _read_closed(src: Sources) -> pd.DataFrame:
    """Every `round_<season>_<NN>.csv`, concatenated. The file IS the state."""
    if not src.rounds_dir.is_dir():
        return pd.DataFrame()
    frames = []
    for path in sorted(src.rounds_dir.glob("round_*.csv")):
        try:
            piece = pd.read_csv(path)
        except (pd.errors.EmptyDataError, OSError) as exc:
            log.warning("unreadable round file %s: %s", path.name, exc)
            continue
        piece["source_file"] = path.name
        frames.append(piece)
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["season"] = df["season"].astype(str)
    df["matchday"] = df["matchday"].astype(int)
    df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce")
    if "league" not in df.columns:
        df["league"] = DEFAULT_LEAGUE
    if "valida" in df.columns:
        df["valida"] = df["valida"].astype(str).str.lower().isin(("true", "1", "1.0"))
    else:
        df["valida"] = True
    return df


def _read_summary(src: Sources) -> pd.DataFrame:
    if not src.rounds_summary.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(src.rounds_summary)
    except (pd.errors.EmptyDataError, OSError):
        return pd.DataFrame()
    df["season"] = df["season"].astype(str)
    df["matchday"] = df["matchday"].astype(int)
    return df


def _read_kickoffs(src: Sources) -> dict:
    """
    Kickoff times, from the calendar, via `predict.kickoff`.

    Never rebuild this: fbref publishes the time in the stadium's local zone,
    and reading it as UTC shifts kickoff two hours forward in summer. That bug
    already put two rows in the track record written after the match started.

    If the calendar is missing the API still works — `kickoff_utc` comes back
    null, which is the honest answer, not an error.
    """
    if not src.schedule.exists():
        log.warning("no schedule at %s: kickoff_utc will be null", src.schedule)
        return {}
    try:
        from src.rounds import calendario

        sched = calendario()
    except Exception as exc:  # noqa: BLE001 - a broken calendar must not 500
        log.warning("calendar unavailable (%s): kickoff_utc will be null", exc)
        return {}

    out = {}
    for row in sched.itertuples(index=False):
        out[(str(row.season), row.home_team, row.away_team)] = row.kickoff
    return out


@lru_cache(maxsize=4)
def _load(fingerprint: tuple, src: Sources) -> Snapshot:
    """The fingerprint is an argument on purpose: change a file, miss the cache."""
    predictions = _read_predictions(src)
    closed = _read_closed(src)
    summary = _read_summary(src)
    kickoffs = _read_kickoffs(src)

    mtimes = [m for _, m, _ in
              [_stat(src.predictions_log), _stat(src.rounds_summary)]
              + ([_stat(p) for p in sorted(src.rounds_dir.glob("round_*.csv"))]
                 if src.rounds_dir.is_dir() else [])
              if m is not None]
    updated = (_iso(pd.Timestamp(max(mtimes), unit="ns", tz="UTC"))
               if mtimes else None)
    return Snapshot(predictions, closed, summary, kickoffs, updated)


def load(src: Sources | None = None) -> Snapshot:
    src = src or default_sources()
    return _load(_fingerprint(src), src)


@lru_cache(maxsize=8)
def _round_states(fingerprint: tuple, season: str) -> pd.DataFrame:
    """
    Round states from `rounds.stato_giornate()` — the project's only state machine.

    Wrapped because it reads the calendar, the master frame and the odds
    snapshot: if any is missing the API degrades to "no state" instead of
    failing, and the routes report it.
    """
    try:
        from src.rounds import stato_giornate

        return stato_giornate(season=season)
    except Exception as exc:  # noqa: BLE001
        log.warning("round states unavailable for %s: %s", season, exc)
        return pd.DataFrame()


def round_states(season: str, src: Sources | None = None) -> pd.DataFrame:
    src = src or default_sources()
    return _round_states(_fingerprint(src), season_in(season))


# ---------------------------------------------------------------------------
# Match records: the field-by-field contract
# ---------------------------------------------------------------------------

def _predicted_outcome(row: dict) -> str | None:
    """
    Most likely outcome, H/D/A.

    Closed rounds already store it as `esito_previsto`; open ones take the
    argmax of the three probabilities. Same rule on both sides, so a match
    cannot appear to change its own prediction when the round is closed.
    Ties break towards H, then D: `np.argmax` returns the first maximum, and
    the order H, D, A is the project's ordinal convention.
    """
    stored = _opt_str(row.get("esito_previsto"))
    if stored in OUTCOMES:
        return stored
    probs = [_opt_float(row.get(c)) for c in PROB_COLS]
    if any(p is None for p in probs):
        return None
    return OUTCOMES[int(np.argmax(probs))]


def _overround(row: dict) -> float | None:
    """
    Book margin: sum of the implied probabilities minus one.

    Null if even one price is missing — two thirds of an overround is not a
    smaller overround, it is a different quantity.
    """
    odds = [_opt_float(row.get(c)) for c in ("odds_home", "odds_draw", "odds_away")]
    if any(o is None or o <= 0 for o in odds):
        return None
    return float(sum(1.0 / o for o in odds) - 1.0)


def _fair(prob: float | None) -> float | None:
    """Fair odds, 1/p, from `models.baseline.fair_odds`."""
    from src.models.baseline import fair_odds

    if prob is None or prob <= 0:
        return None
    value = float(fair_odds(np.array([prob]))[0])
    return value if np.isfinite(value) else None


def _record(row: dict, kickoffs: dict, from_closed: bool) -> dict:
    """One match, in the shape the frontend receives."""
    season = str(row.get("season"))
    home, away = row.get("home_team"), row.get("away_team")

    valid = bool(row.get("valida", True)) if from_closed else True
    has_result = _opt_str(row.get("FTR")) is not None
    if from_closed and not valid:
        status = STATUS_INVALID
    elif has_result:
        status = STATUS_RESOLVED
    else:
        status = STATUS_PREDICTED

    probs = {c: _opt_float(row.get(c)) for c in PROB_COLS}

    return {
        "season": season_out(season),
        "matchday": _opt_int(row.get("matchday")),
        "kickoff_utc": _iso(kickoffs.get((season, home, away))),
        "match_date": _iso(row.get("match_date")),
        "home_team": _opt_str(home),
        "away_team": _opt_str(away),
        "status": status,

        "p_home": probs["p_home"],
        "p_draw": probs["p_draw"],
        "p_away": probs["p_away"],
        "p_over25": _opt_float(row.get("p_over25")),
        "p_btts": _opt_float(row.get("p_btts")),
        "lambda_home": _opt_float(row.get("lambda_home")),
        "lambda_away": _opt_float(row.get("lambda_away")),
        "predicted_outcome": _predicted_outcome(row),

        "odds_home": _opt_float(row.get("odds_home")),
        "odds_draw": _opt_float(row.get("odds_draw")),
        "odds_away": _opt_float(row.get("odds_away")),
        "odds_source": _opt_str(row.get("odds_source")),
        "fair_odds_home": _fair(probs["p_home"]),
        "fair_odds_draw": _fair(probs["p_draw"]),
        "fair_odds_away": _fair(probs["p_away"]),
        "overround": _overround(row),

        # Result: null on everything until the match is played, and metrics
        # stay null on rows written after kickoff even though the result is
        # known. The frontend needs `valid` to explain that gap.
        "goals_home": _opt_int(row.get("FTHG")),
        "goals_away": _opt_int(row.get("FTAG")),
        "actual_outcome": _opt_str(row.get("FTR")),
        "correct": _opt_bool(row.get("azzeccato")) if valid else None,
        "rps": _opt_float(row.get("rps")) if valid else None,
        "valid": valid,
        "invalid_reason": None if valid else INVALID_REASON,

        "timestamp_prediction": _iso(row.get("timestamp_prediction")),
        "model_version": _opt_str(row.get("model_version")),
    }


def _all_records(season: str, snap: Snapshot) -> list[dict]:
    """
    Every match of a season, closed rounds winning over the log.

    A match of a closed round sits in BOTH sources. The archived file wins: it
    carries the result and the metrics that were put on record, and those are
    the numbers the track record is made of.
    """
    code = season_in(season)
    records, seen = [], set()

    if not snap.closed.empty:
        rows = snap.closed[snap.closed["season"] == code]
        for row in rows.to_dict("records"):
            key = (row["season"], row["home_team"], row["away_team"])
            seen.add(key)
            records.append(_record(row, snap.kickoffs, from_closed=True))

    if not snap.predictions.empty:
        rows = snap.predictions[snap.predictions["season"] == code]
        for row in rows.to_dict("records"):
            key = (row["season"], row["home_team"], row["away_team"])
            if key in seen:
                continue
            records.append(_record(row, snap.kickoffs, from_closed=False))

    records.sort(key=lambda r: (r["kickoff_utc"] or r["match_date"] or "",
                                r["home_team"] or ""))
    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seasons(src: Sources | None = None) -> list[str]:
    """Seasons present in the track record, most recent last."""
    snap = load(src)
    codes = set()
    for frame in (snap.predictions, snap.closed):
        if not frame.empty:
            codes.update(frame["season"].astype(str).unique())
    return [season_out(c) for c in sorted(codes)]


def has_season(season: str, src: Sources | None = None) -> bool:
    return season_out(season_in(season)) in seasons(src)


def matches(
    season: str,
    team: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    src: Sources | None = None,
) -> list[dict]:
    """Matches of a season, filtered. Dates compare against the kickoff day."""
    records = _all_records(season, load(src))

    if team:
        wanted = team.strip().lower()
        records = [r for r in records
                   if wanted in ((r["home_team"] or "").lower(),
                                 (r["away_team"] or "").lower())]
    if status:
        records = [r for r in records if r["status"] == status]

    def day(record: dict) -> str | None:
        stamp = record["kickoff_utc"] or record["match_date"]
        return stamp[:10] if stamp else None

    if date_from:
        records = [r for r in records if (day(r) or "") >= str(date_from)[:10]]
    if date_to:
        records = [r for r in records if (day(r) or "9999") <= str(date_to)[:10]]
    return records


def round_matches(season: str, matchday: int,
                  src: Sources | None = None) -> list[dict]:
    """Matches of one round. Empty list if the round has nothing recorded."""
    return [r for r in _all_records(season, load(src))
            if r["matchday"] == int(matchday)]


def rounds(season: str, src: Sources | None = None) -> list[dict]:
    """
    One entry per round: dates, state, counts and archived RPS.

    The state comes from `rounds.stato_giornate()` and is translated, never
    recomputed: two definitions of "closed" would diverge the first time a
    postponed match showed up.
    """
    src = src or default_sources()
    snap = load(src)
    code = season_in(season)
    states = round_states(code, src)
    summary = snap.summary[snap.summary["season"] == code] if not snap.summary.empty \
        else pd.DataFrame()
    records = _all_records(season, snap)

    by_round: dict[int, list[dict]] = {}
    for record in records:
        by_round.setdefault(record["matchday"], []).append(record)

    numbers = set(by_round)
    if not states.empty:
        numbers.update(int(n) for n in states["matchday"])

    out = []
    for number in sorted(numbers):
        state_row = states[states["matchday"] == number] if not states.empty \
            else pd.DataFrame()
        state = state_row.iloc[0].to_dict() if not state_row.empty else {}
        summary_row = summary[summary["matchday"] == number] if not summary.empty \
            else pd.DataFrame()
        archived = summary_row.iloc[0].to_dict() if not summary_row.empty else {}
        here = by_round.get(number, [])

        out.append({
            "season": season_out(code),
            "matchday": number,
            "first_date": _iso(state.get("prima_data")) if state else
                          (here[0]["match_date"] if here else None),
            "last_date": _iso(state.get("ultima_data")) if state else
                         (here[-1]["match_date"] if here else None),
            "status": ROUND_STATUS.get(_opt_str(state.get("stato")), None),
            "matches": _opt_int(state.get("n_partite")) if state else len(here),
            "matches_with_odds": _opt_int(state.get("n_quote")),
            "matches_predicted": _opt_int(state.get("n_predette")) if state
                                 else len(here),
            "matches_played": _opt_int(state.get("n_giocate")),
            "matches_resolved": sum(1 for r in here if r["status"] == STATUS_RESOLVED),
            "rps": _opt_float(archived.get("rps", state.get("rps"))),
            "rps_market": _opt_float(archived.get("rps_mercato")),
            "closed": bool(archived) or _opt_str(state.get("stato")) == "chiusa",
        })
    return out


def _significance(resolved: list[dict]) -> dict:
    """
    Is the sample big enough to tell a `tolerance` gap from the backtest?

    Same method as `report.fabbisogno`: paired cluster bootstrap on the round,
    because the ten matches of a round are predicted by the same fit. The
    half-width scales as 1/sqrt(rounds), so the rounds needed follow from the
    one measured now. Under four rounds the bootstrap has nothing to resample
    and the answer is "not yet", stated as such instead of guessed.
    """
    tolerance = config.TRACK_TOLERANCE_RPS
    values = [r["rps"] for r in resolved if r["rps"] is not None]
    clusters = [f"{r['season']}-{r['matchday']}" for r in resolved
                if r["rps"] is not None]
    n_rounds = len(set(clusters))

    base = {"method": None, "rounds": n_rounds, "matches": len(values),
            "tolerance": tolerance, "half_width": None,
            "rounds_needed": None, "rounds_missing": None}

    if n_rounds < 4:
        base["method"] = "insufficient-rounds"
        return base

    from src.evaluate import cluster_bootstrap

    result = cluster_bootstrap(
        np.asarray(values, dtype=float) - config.TEST_RPS_REFERENCE,
        np.asarray(clusters),
    )
    half = (result["ic_alto"] - result["ic_basso"]) / 2
    needed = int(np.ceil(n_rounds * (half / tolerance) ** 2))
    base.update(method="cluster-bootstrap", half_width=float(half),
                rounds_needed=needed, rounds_missing=max(needed - n_rounds, 0))
    return base


def season_summary(season: str, src: Sources | None = None) -> dict | None:
    """Season headline. None if the season has nothing recorded (the route 404s)."""
    src = src or default_sources()
    snap = load(src)
    if not has_season(season, src):
        return None

    records = _all_records(season, snap)
    resolved = [r for r in records if r["status"] == STATUS_RESOLVED]
    invalid = [r for r in records if r["status"] == STATUS_INVALID]
    scored = [r["rps"] for r in resolved if r["rps"] is not None]
    hits = sum(1 for r in resolved if r["correct"] is True)

    round_list = rounds(season, src)
    significance = _significance(resolved)

    return {
        "season": season_out(season_in(season)),
        "updated_at": snap.updated_at,
        "model_version": PRODUCTION_MODEL,
        "rounds_total": len(round_list) or None,
        "rounds_closed": sum(1 for r in round_list if r["closed"]),
        "matches_predicted": len(records),
        "matches_resolved": len(resolved),
        "matches_invalid": len(invalid),
        "rps_production": float(np.mean(scored)) if scored else None,
        "rps_backtest_reference": config.TEST_RPS_REFERENCE,
        # Numerator and denominator kept apart: a rate alone hides how thin
        # the sample is, and here it is very thin.
        "hits": hits,
        "hits_denominator": len(resolved),
        "hit_rate": (hits / len(resolved)) if resolved else None,
        "sample_significant": significance["rounds_missing"] == 0
                              if significance["rounds_missing"] is not None else False,
        "significance": significance,
    }


def track_record(season: str, src: Sources | None = None) -> dict | None:
    """Cumulative RPS series plus the calibration curve, when there is enough of it."""
    src = src or default_sources()
    snap = load(src)
    if not has_season(season, src):
        return None
    code = season_in(season)

    by_round = []
    if not snap.summary.empty:
        rows = snap.summary[snap.summary["season"] == code].sort_values("matchday")
        for row in rows.to_dict("records"):
            by_round.append({
                "matchday": _opt_int(row.get("matchday")),
                "matches": _opt_int(row.get("n_previsioni")),
                "matches_valid": _opt_int(row.get("n_valide")),
                "rps": _opt_float(row.get("rps")),
                "rps_market": _opt_float(row.get("rps_mercato")),
                "accuracy": _opt_float(row.get("accuratezza")),
                "cumulative_matches": _opt_int(row.get("n_cumulate")),
                "cumulative_rps": _opt_float(row.get("rps_cumulativo")),
            })

    resolved = [r for r in _all_records(season, snap)
                if r["status"] == STATUS_RESOLVED and r["rps"] is not None]
    running, total = [], 0.0
    for i, record in enumerate(resolved, start=1):
        total += record["rps"]
        running.append({
            "kickoff_utc": record["kickoff_utc"],
            "match_date": record["match_date"],
            "home_team": record["home_team"],
            "away_team": record["away_team"],
            "rps": record["rps"],
            "cumulative_rps": total / i,
        })

    calibration, reason = None, None
    if len(resolved) >= CALIBRATION_MIN_MATCHES:
        from src.evaluate import _onehot, calibration_table, outcome_index

        probs = np.array([[r["p_home"], r["p_draw"], r["p_away"]] for r in resolved],
                         dtype=float)
        y = outcome_index(pd.Series([r["actual_outcome"] for r in resolved]))
        table = calibration_table(probs.reshape(-1), _onehot(y).reshape(-1))
        calibration = [{
            "bin": str(row.get("bin")),
            "n": _opt_int(row.get("n")),
            "expected": _opt_float(row.get("atteso")),
            "observed": _opt_float(row.get("osservato")),
            "ci_low": _opt_float(row.get("ic_basso")),
            "ci_high": _opt_float(row.get("ic_alto")),
        } for row in table.to_dict("records")]
    else:
        reason = (f"{len(resolved)} resolved predictions: a calibration curve "
                  f"needs at least {CALIBRATION_MIN_MATCHES} to mean anything")

    return {
        "season": season_out(code),
        "updated_at": snap.updated_at,
        "rps_backtest_reference": config.TEST_RPS_REFERENCE,
        "by_round": by_round,
        "by_match": running,
        "calibration": calibration,
        "calibration_unavailable_reason": reason,
    }


def odds_snapshot(src: Sources | None = None) -> dict:
    """
    Age of the upcoming-odds snapshot, for /api/status.

    THE EMPTY SNAPSHOT IS A NORMAL STATE, and it used to crash things: between
    rounds football-data answers but has not published Serie A yet, the file is
    rewritten with zero rows and `downloaded_at` stays NaT. Formatting that NaT
    raised `ValueError` and took down `rounds --status` with it. Hence the
    `pd.notna` guard and an explicit `empty` flag.
    """
    src = src or default_sources()
    out = {"exists": src.odds_snapshot.exists(), "empty": None,
           "rows": None, "downloaded_at": None, "age_hours": None}
    if not out["exists"]:
        return out
    try:
        df = pd.read_parquet(src.odds_snapshot)
    except Exception as exc:  # noqa: BLE001
        log.warning("unreadable odds snapshot: %s", exc)
        return out

    out["rows"] = int(len(df))
    out["empty"] = df.empty
    if "downloaded_at" in df.columns:
        stamp = pd.to_datetime(df["downloaded_at"], errors="coerce", utc=True).max()
        if pd.notna(stamp):
            out["downloaded_at"] = _iso(stamp)
            out["age_hours"] = float(
                (pd.Timestamp.now(tz="UTC") - stamp).total_seconds() / 3600
            )
    return out


def freshness(src: Sources | None = None) -> dict:
    """What /api/health answers: is there data, and how old is it."""
    src = src or default_sources()
    snap = load(src)
    return {
        "updated_at": snap.updated_at,
        "predictions_log_exists": src.predictions_log.exists(),
        "rounds_closed": int(snap.closed["source_file"].nunique())
                         if not snap.closed.empty else 0,
        "matches_logged": int(len(snap.predictions)),
        "seasons": seasons(src),
    }
