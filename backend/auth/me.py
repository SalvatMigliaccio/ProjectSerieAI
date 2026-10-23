"""
The signed-in user's own data: favourite team and "My Prediction".

THE SECOND WRITABLE PREFIX, AND WHAT IT DOES NOT CHANGE (docs/adr/0004).
Until now only /api/auth could write. /api/me writes too — rows of the user's
own, in Postgres, next to the accounts. The guarantee that mattered is intact:
nothing reachable over HTTP touches the track record, which stays files
written by `predict_round` and `close_round` on the local machine.

A PICK IS ONLY MADE BEFORE KICK-OFF, AND THE SERVER DECIDES WHEN THAT IS. The
same principle as the registry: a prediction is worth comparing only if it
was written by someone who did not know the result. Creating or deleting a
pick checks the kickoff in the match files against the server's clock; a
match without a known kickoff cannot be picked at all, because "unknown" is
not "later".

WHAT A PICK IS. One market of one match, from the same catalogue the
dashboard prices (`models.baseline.all_markets`). For each, the model's
probability and fair odds are recomputed from the two goal expectations in
the registry — never stored — so the comparison follows the model exactly as
the dashboard shows it. There is no score: the page compares, it does not rank.

READS THE TRACK RECORD THROUGH `backend.api.store`, which is read-only. The
import goes from the auth package to the api package for data, as the
selections already do; the reverse (api importing this) happens only in
`create_app`, to mount the router.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .deps import CurrentUser, Db, require_permission
from .models import UserPrediction, now
from .ratelimit import prediction_limiter

log = logging.getLogger("auth.me")

router = APIRouter(prefix="/api/me", tags=["me"])

# A ceiling per match. Ten covers every sensible combination (an outcome, a
# double chance, a goals line, both teams to score...); past that it is not
# an opinion any more, it is the whole catalogue.
MAX_PICKS_PER_MATCH = 10

# The markets left out of the catalogue the page offers, as the dashboard
# leaves them out: lines nobody plays (over 0.5, under 4.5) and the two
# "team scores" markets whose labels would need the team names to make sense.
EXCLUDED = {"over 0.5", "under 0.5", "over 4.5", "under 4.5", "casa segna", "fuori segna"}

GROUPS: list[tuple[str, list[str]]] = [
    ("Esito finale", ["1", "X", "2"]),
    ("Doppia chance", ["1X", "X2", "12"]),
    ("Gol / No gol", ["gol-gol", "no gol"]),
    ("Under / Over 1.5", ["under 1.5", "over 1.5"]),
    ("Under / Over 2.5", ["under 2.5", "over 2.5"]),
    ("Under / Over 3.5", ["under 3.5", "over 3.5"]),
]
LABELS = {"gol-gol": "Gol", "no gol": "No gol"}


@lru_cache(maxsize=1)
def markets() -> tuple[str, ...]:
    """The markets a user may pick: every one the model prices, minus EXCLUDED."""
    import numpy as np

    from goalmodel.models.baseline import all_markets

    priced = all_markets(np.array([1.4]), np.array([1.1])).columns
    offered = tuple(m for _, names in GROUPS for m in names)
    missing = set(offered) - set(priced)
    if missing:  # pragma: no cover - a renamed market must fail loudly
        raise RuntimeError(f"markets offered but not priced by the model: {sorted(missing)}")
    return offered


def _probabilities(lam_home: float, lam_away: float) -> dict[str, float]:
    import numpy as np

    from goalmodel import config
    from goalmodel.models.baseline import all_markets

    row = all_markets(np.array([lam_home]), np.array([lam_away]), rho=config.DC_RHO).iloc[0]
    return {m: float(row[m]) for m in markets()}


def _parse(iso: str | None) -> dt.datetime | None:
    if not iso:
        return None
    try:
        at = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=dt.UTC)


def _season_code(season: str) -> str:
    from backend.api import store
    try:
        return store.season_in(season)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown season '{season}'") from exc


def _outcome(gh: int | None, ga: int | None) -> str | None:
    if gh is None or ga is None:
        return None
    return "H" if gh > ga else "A" if ga > gh else "D"


def _records(code: str) -> dict[tuple[str, str], dict]:
    """
    Every match of the season, by (home, away): the calendar, and over it what
    the track record knows.

    THE CALENDAR FIRST, so a round exists as soon as the league has published
    it — not only once Friday's odds are in and the model has priced it. A
    match the model has not priced yet has no goal expectations (`lambda_*`
    null): it can be picked, and its probabilities appear the day the model
    writes them, recomputed for the picks already made.
    """
    from backend.api import store

    out: dict[tuple[str, str], dict] = {}
    for f in store.fixtures(code):
        out[(f["home_team"], f["away_team"])] = {
            **f, "lambda_home": None, "lambda_away": None,
            "actual_outcome": _outcome(f["goals_home"], f["goals_away"]),
        }
    for r in store.matches(code):
        key = (r["home_team"], r["away_team"])
        out[key] = {**out.get(key, {}), **r}
    return out


def _pickable(records: dict[tuple[str, str], dict], at: dt.datetime) -> set[tuple[str, str]]:
    """
    The matches a user may pick now: not started, and in the NEXT ROUND.

    The next round is the lowest matchday that still has most of its matches
    ahead: a single postponed match of an old round must not make that round
    "next" and hide the real one. Matches the model has already priced stay
    pickable whatever their round — a postponed match with odds is a match
    like any other.

    Not the whole season ahead: a pick made in October on a match in May would
    be a guess about a team that does not exist yet, and the comparison with
    the model — priced the week of the match — would be meaningless.
    """
    upcoming = [r for r in records.values() if _is_open(r, at)]
    if not upcoming:
        return set()
    per_round: dict[int, int] = {}
    for r in upcoming:
        per_round[r["matchday"]] = per_round.get(r["matchday"], 0) + 1
    full = [d for d, n in per_round.items() if n >= 5]
    next_round = min(full) if full else min(per_round)
    return {(r["home_team"], r["away_team"]) for r in upcoming
            if r["matchday"] == next_round or r.get("lambda_home") is not None}


def _is_open(record: dict, at: dt.datetime) -> bool:
    kickoff = _parse(record.get("kickoff_utc"))
    return kickoff is not None and at < kickoff and record.get("goals_home") is None


def _limit(request: Request, user_id: uuid.UUID) -> None:
    key = str(user_id)
    if not prediction_limiter.allow(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many requests: wait a few minutes before trying again",
            headers={"Retry-After": str(prediction_limiter.retry_after(key))},
        )


# --- favourite team -----------------------------------------------------------

class Preferences(BaseModel):
    favorite_team: str | None = Field(default=None, max_length=64)


@router.get("/preferences")
def get_preferences(user: CurrentUser) -> Preferences:
    return Preferences(favorite_team=user.favorite_team)


@router.post("/preferences")
def set_preferences(payload: Preferences, request: Request, user: CurrentUser) -> Preferences:
    """
    Save the favourite team, or clear it with `null`.

    The name must be a team of the current season, as the track record spells
    it. A free string would be stored and never match a single fixture, and
    the page would highlight nothing with no error anywhere.
    """
    _limit(request, user.id)
    team = (payload.favorite_team or "").strip() or None
    if team is not None:
        from backend.api import store

        from goalmodel import config

        # The calendar, not the track record: all twenty teams are in it from
        # the first matchday, predicted or not.
        teams = {name for r in [*store.fixtures(config.CURRENT_SEASON),
                                *store.matches(config.CURRENT_SEASON)]
                 for name in (r["home_team"], r["away_team"])}
        if team not in teams:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"'{team}' is not a team of the current season")
    user.favorite_team = team
    return Preferences(favorite_team=team)


# --- My Prediction ------------------------------------------------------------

class MarketOption(BaseModel):
    market: str
    label: str
    group: str
    # Null until the model has priced the match (Friday's odds).
    probability: float | None
    fair_odds: float | None
    picked: bool


class OpenMatch(BaseModel):
    """
    A match that can still be picked, with the model's price on every market
    once the model has one. `priced` is false between the close of the
    previous round and Friday's odds: the match is open, its numbers are not.
    """

    season: str
    matchday: int
    home_team: str
    away_team: str
    kickoff_utc: str
    priced: bool
    lambda_home: float | None
    lambda_away: float | None
    markets: list[MarketOption]


class Pick(BaseModel):
    id: str
    season: str
    matchday: int
    home_team: str
    away_team: str
    kickoff_utc: str | None
    market: str
    label: str
    model_probability: float | None
    fair_odds: float | None
    created_at: str
    locked: bool
    goals_home: int | None
    goals_away: int | None
    won: bool | None


class Summary(BaseModel):
    """
    The comparison without a score.

    `mean_model_probability` is what the model expected of the resolved picks
    on average; `hit_rate` is what actually happened. Side by side they say
    whether the user's picks did better or worse than the model thought they
    would — the one honest comparison with no points table.
    """

    picks: int
    pending: int
    resolved: int
    won: int
    hit_rate: float | None
    mean_model_probability: float | None


class MyPredictions(BaseModel):
    summary: Summary
    picks: list[Pick]


class NewPick(BaseModel):
    season: str = Field(max_length=9)
    home_team: str = Field(max_length=64)
    away_team: str = Field(max_length=64)
    market: str = Field(max_length=16)


def _fair(p: float | None) -> float | None:
    return round(1.0 / p, 2) if p and p > 0 else None


def _label(market: str) -> str:
    return LABELS.get(market, market)


def _group(market: str) -> str:
    return next(g for g, names in GROUPS if market in names)


@router.get("/predictions/{season}/open",
            dependencies=[Depends(require_permission("picks:read"))])
def open_matches(season: str, user: CurrentUser, db: Db) -> list[OpenMatch]:
    """
    Matches not kicked off yet in the next round, with the model's price on
    every market where it exists.

    Behind `picks:read`, like the unplayed rounds on the dashboard: the price
    of a match that has not started is the product, here as there.
    """
    code = _season_code(season)
    at = now()
    mine = {(p.home_team, p.away_team, p.market) for p in db.scalars(
        select(UserPrediction).where(UserPrediction.user_id == user.id,
                                     UserPrediction.season == code))}
    records = _records(code)
    out = []
    for home, away in _pickable(records, at):
        r = records[(home, away)]
        priced = r.get("lambda_home") is not None and r.get("lambda_away") is not None
        probs = _probabilities(r["lambda_home"], r["lambda_away"]) if priced else {}
        out.append(OpenMatch(
            season=r["season"], matchday=r["matchday"], home_team=home, away_team=away,
            kickoff_utc=r["kickoff_utc"], priced=priced,
            lambda_home=r.get("lambda_home"), lambda_away=r.get("lambda_away"),
            markets=[MarketOption(market=m, label=_label(m), group=_group(m),
                                  probability=probs.get(m), fair_odds=_fair(probs.get(m)),
                                  picked=(home, away, m) in mine)
                     for m in markets()],
        ))
    out.sort(key=lambda m: (m.kickoff_utc, m.home_team))
    return out


@router.get("/predictions/{season}")
def my_predictions(season: str, user: CurrentUser, db: Db) -> MyPredictions:
    """Every pick of the season, newest match first, resolved against the result."""
    from backend.api.selections import resolve

    code = _season_code(season)
    records = _records(code)
    at = now()
    rows = db.scalars(select(UserPrediction).where(
        UserPrediction.user_id == user.id, UserPrediction.season == code)).all()

    picks: list[Pick] = []
    for p in rows:
        r = records.get((p.home_team, p.away_team), {})
        lam_h, lam_a = r.get("lambda_home"), r.get("lambda_away")
        # Recomputed every time: a pick made before Friday gets the model's
        # probability as soon as the model writes one.
        prob = (_probabilities(lam_h, lam_a)[p.market]
                if lam_h is not None and lam_a is not None else None)
        gh, ga = r.get("goals_home"), r.get("goals_away")
        played = gh is not None and ga is not None
        picks.append(Pick(
            id=str(p.id), season=r.get("season") or p.season, matchday=p.matchday,
            home_team=p.home_team, away_team=p.away_team,
            kickoff_utc=r.get("kickoff_utc"), market=p.market, label=_label(p.market),
            model_probability=prob, fair_odds=_fair(prob),
            created_at=p.created_at.isoformat(),
            locked=not _is_open(r, at) if r else True,
            goals_home=gh, goals_away=ga,
            won=resolve(p.market, gh, ga, r.get("actual_outcome")) if played else None,
        ))
    picks.sort(key=lambda k: (k.kickoff_utc or "", k.home_team, k.market), reverse=True)

    resolved = [k for k in picks if k.won is not None]
    probs = [k.model_probability for k in resolved if k.model_probability is not None]
    summary = Summary(
        picks=len(picks),
        pending=len(picks) - len(resolved),
        resolved=len(resolved),
        won=sum(1 for k in resolved if k.won),
        hit_rate=(sum(1 for k in resolved if k.won) / len(resolved)) if resolved else None,
        mean_model_probability=(sum(probs) / len(probs)) if probs else None,
    )
    return MyPredictions(summary=summary, picks=picks)


@router.post("/predictions", status_code=status.HTTP_201_CREATED)
def add_prediction(payload: NewPick, request: Request, user: CurrentUser, db: Db) -> dict:
    """
    Record one pick. 409 once the match has started, or if it is already there.

    The kickoff is read from the calendar, here, now. The request carries no
    time at all, so there is nothing a client can send to make a late pick
    look early. The model need not have priced the match yet: picks open with
    the round, not with the odds.
    """
    _limit(request, user.id)
    code = _season_code(payload.season)
    if payload.market not in markets():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"unknown market '{payload.market}'")
    records = _records(code)
    key = (payload.home_team, payload.away_team)
    record = records.get(key)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "match not found")
    at = now()
    if not _is_open(record, at):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the match has started: picks are closed")
    if key not in _pickable(records, at):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "picks open with the round: this match is not in the next one")

    count = len(db.scalars(select(UserPrediction.id).where(
        UserPrediction.user_id == user.id, UserPrediction.season == code,
        UserPrediction.home_team == payload.home_team,
        UserPrediction.away_team == payload.away_team)).all())
    if count >= MAX_PICKS_PER_MATCH:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"at most {MAX_PICKS_PER_MATCH} picks per match")

    row = UserPrediction(user_id=user.id, season=code, matchday=record["matchday"],
                         home_team=payload.home_team, away_team=payload.away_team,
                         market=payload.market)
    try:
        # A savepoint, so a duplicate does not poison the request transaction.
        with db.begin_nested():
            db.add(row)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "pick already recorded") from exc
    return {"id": str(row.id)}


@router.post("/predictions/{pick_id}/delete")
def delete_prediction(pick_id: uuid.UUID, request: Request, user: CurrentUser,
                      db: Db) -> dict:
    """
    Withdraw a pick, before kick-off only.

    After it, a deletion is how a losing pick would disappear from the
    comparison: the same reason the track record is append-only. Another
    account's pick answers 404, like one that does not exist.
    """
    _limit(request, user.id)
    row = db.scalar(select(UserPrediction).where(
        UserPrediction.id == pick_id, UserPrediction.user_id == user.id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pick not found")
    record = _records(row.season).get((row.home_team, row.away_team))
    if record is None or not _is_open(record, now()):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "the match has started: the pick stays")
    db.delete(row)
    return {"deleted": 1}
