"""
The seven endpoints. All GET, all thin: they call `store`, validate, return.

WHY THE ROUTES ARE THIN. Every rule about the data — precedence of the closed
round file, the null contract, the status vocabulary — lives in `store.py`. If
a route did any of it, there would be two answers to the same question and the
one the frontend sees would depend on which URL it asked.

ERROR CODES, AND WHY NOT "NEVER 500"
  404  the thing asked for does not exist: unknown season, empty round.
       The message names what was asked and what exists.
  503  the data should be there but has not been built yet. The message names
       the command that builds it.
  500  a real bug. It stays a 500 and gets logged. Turning an unexpected
       exception into a polite 404 would hide the defect for weeks, and the
       frontend would render "no data" on a broken server.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth.deps import Viewer, may_see_upcoming, require_permission

from . import (
    schemas,
    store,
)
from . import (
    selections as selections_mod,
)
from . import (
    standings as standings_mod,
)
from . import (
    status as status_mod,
)

log = logging.getLogger("api.routes")

# TWO ROUTERS, AND THE LINE BETWEEN THEM IS THE PRODUCT.
#
# `router` still requires `data:read` and now holds only `/api/status`: the
# scheduler's last runs and the snapshot's age are operational detail, not
# content. A router-level dependency is used rather than one decorator per
# route because it cannot be forgotten when a route is added, and forgetting
# it is the whole risk.
#
# `public` answers without a session. It carries the history — a season's
# figures, the rounds, the track record, the table — plus the four routes that
# can contain matches which have NOT been played, which they filter per viewer.
#
# WHY THE HISTORY IS FREE. Everything on it already happened: the result is in
# the newspaper, and what the project adds is the prediction written before
# kick-off next to it. That is the proof, and proof withheld convinces nobody
# (`docs/MODELLO_DI_BUSINESS.md`, section 5). What is worth money is the round
# that has not kicked off yet, and that is exactly what `picks:read` now gates.
router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_permission("data:read"))],
)

public = APIRouter(prefix="/api")

VALID_STATUS = (store.STATUS_PREDICTED, store.STATUS_RESOLVED, store.STATUS_INVALID)


def played(match: dict) -> bool:
    """
    Whether the match has a result, which is the whole free/paid boundary.

    Deliberately the RESULT and not the round's state. A round stays open while
    one match is postponed, and under a rule keyed on the round those nine
    played matches would stay behind the paywall for weeks — while the
    postponed one, which is the only thing still worth paying for, would come
    out with them the moment the round closed. The result per match is the
    honest line: once it is known, the prediction can only be checked, and
    checking it is what the free tier is for.
    """
    return match.get("goals_home") is not None


# The free window, in days. Everything played inside it is public; older
# matches need a session, and so does anything not yet played.
#
# THE FLOOR IS NOT OPTIONAL. Serie A stops for three months in summer, and a
# bare 30-day window would empty the public pages exactly when a visitor has
# time to read them — an empty site is a broken site to anyone who does not
# know the calendar. So the most recent played matchday is always free, however
# long ago it was.
FREE_WINDOW_DAYS = 30


def _free_from(matches: list[dict]) -> tuple[dt.datetime | None, int | None]:
    """The window's start, and the matchday that stays free regardless."""
    kickoffs = [m["kickoff_utc"] for m in matches if played(m) and m.get("kickoff_utc")]
    if not kickoffs:
        return None, None
    last = max(kickoffs)
    newest = max((m["matchday"] for m in matches
                  if m.get("kickoff_utc") == last and m.get("matchday") is not None),
                 default=None)
    return dt.datetime.fromisoformat(last) - dt.timedelta(days=FREE_WINDOW_DAYS), newest


def only_played(matches: list[dict], user) -> list[dict]:
    """
    Everything for a subscriber; the recent, played part for everyone else.

    The window counts back from the LAST MATCH IN THE DATA, not from now. Two
    reasons, and the second is the one that bites: a window anchored to the
    clock makes the response change without the data changing, which no cache
    and no test can reason about — and during the summer break it would tick
    the whole season out of view one matchday at a time.
    """
    if may_see_upcoming(user):
        return matches
    start, newest = _free_from(matches)
    if start is None:
        return [m for m in matches if played(m)]
    return [m for m in matches
            if played(m)
            and (m.get("matchday") == newest
                 or (m.get("kickoff_utc") or "") >= start.isoformat())]


# The selection payloads carry their matches in named lists rather than being
# one. Filtering by key rather than by walking the dict keeps an added key OUT
# of the free tier by default: a new list of selections that nobody remembered
# to name here simply does not reach an anonymous caller.
SELECTION_LISTS = ("most_probable", "with_min_odds", "selections")


def _visible(payload: dict, user) -> dict:
    if may_see_upcoming(user):
        return payload
    out = dict(payload)
    for key in SELECTION_LISTS:
        if isinstance(out.get(key), list):
            out[key] = only_played(out[key], user)
    # `hits` and `hits_denominator` need no adjustment, and that is a property
    # rather than an oversight: both count RESOLVED rows, which are exactly the
    # ones kept above. A match with no result cannot be a hit and is not in the
    # denominator, so the figures still describe the list that ships.
    return out


def _known_season(season: str) -> str:
    """Resolve '2026-27' or '2627' to the public form, or 404 naming what exists."""
    try:
        if store.has_season(season):
            return store.season_out(store.season_in(season))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"track record not available yet: {exc}. Run "
                   f"'python -m goalmodel.prediction.predict_round' to create it.",
        ) from exc
    available = store.seasons()
    raise HTTPException(
        status_code=404,
        detail=(f"season '{season}' has nothing in the track record. "
                f"Available: {', '.join(available) if available else 'none yet'}"),
    )


@public.get("/season/{season}", response_model=schemas.SeasonSummary,
            summary="Season headline figures")
def get_season(season: str) -> dict:
    _known_season(season)
    summary = store.season_summary(season)
    if summary is None:  # pragma: no cover - _known_season already 404s
        raise HTTPException(status_code=404, detail=f"season '{season}' not found")
    return summary


@public.get("/rounds/{season}", response_model=list[schemas.Round],
            summary="Every round with its state and archived RPS")
def get_rounds(season: str) -> list[dict]:
    _known_season(season)
    return store.rounds(season)


@public.get("/rounds/{season}/{matchday}", response_model=list[schemas.Match],
            summary="Matches of one round")
def get_round(season: str, matchday: int, user: Viewer = None) -> list[dict]:
    _known_season(season)
    matches = store.round_matches(season, matchday)
    if not matches:
        known = sorted({r["matchday"] for r in store.rounds(season)
                        if r["matches_predicted"]})
        raise HTTPException(
            status_code=404,
            detail=(f"round {matchday} of season '{season}' has no recorded "
                    f"predictions. Rounds with predictions: "
                    f"{', '.join(map(str, known)) if known else 'none yet'}"),
        )
    return only_played(matches, user)


@public.get("/matches/{season}", response_model=list[schemas.Match],
            summary="All matches of a season, filterable")
def get_matches(
    season: str,
    user: Viewer = None,
    team: str | None = Query(None, description="Home or away, exact name"),
    status: str | None = Query(None, description="predicted | resolved | invalid"),
    date_from: str | None = Query(None, alias="from", description="YYYY-MM-DD, inclusive"),
    date_to: str | None = Query(None, alias="to", description="YYYY-MM-DD, inclusive"),
) -> list[dict]:
    _known_season(season)
    if status is not None and status not in VALID_STATUS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown status '{status}'. Valid values: {', '.join(VALID_STATUS)}",
        )
    return only_played(
        store.matches(season, team=team, status=status,
                      date_from=date_from, date_to=date_to),
        user)


@public.get("/track-record/{season}", response_model=schemas.TrackRecord,
            summary="Cumulative RPS series and calibration")
def get_track_record(season: str) -> dict:
    _known_season(season)
    record = store.track_record(season)
    if record is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"season '{season}' not found")
    return record


@public.get("/standings/{season}", response_model=schemas.Standings,
            summary="League table from played matches")
def get_standings(season: str) -> dict:
    """
    The table, computed from results — a fact, not a forecast.

    Tied teams are separated head-to-head first, as Serie A does, and not by
    goal difference: the shortcut produces a table that looks right and is
    wrong exactly when the standings matter.
    """
    table = standings_mod.standings(season)
    if table is None:
        raise HTTPException(
            status_code=404,
            detail=f"no played match for season '{season}': the table is empty",
        )
    return table


@public.get("/picks/{season}", response_model=schemas.Picks,
            summary="The canonical M1 selections — no threshold to move")
def get_picks(season: str, matchday: int | None = Query(None, ge=1, le=38),
              user: Viewer = None) -> dict:
    """
    The main line, and it takes no odds parameter on purpose.

    These are the selections `predict_round` prints on Friday and the weekly
    report shows, with `config.QUOTA_MINIMA_SELEZIONE` as the declared floor.
    The band explorer (`/api/selections`) sits beside them as an adjustable
    second reading: if a threshold chosen in the interface could redefine the
    main line, the track record would be measuring one thing while the
    dashboard advertised another.
    """
    _known_season(season)
    return _visible(selections_mod.picks(season, matchday=matchday), user)


@public.get("/selections/{season}", response_model=schemas.Selections,
            summary="Markets priced under a ceiling on the odds")
def get_selections(
    season: str,
    user: Viewer = None,
    max_odds: float = Query(1.40, gt=1.0, le=100.0,
                            description="Ceiling on the FAIR odds, 1/p"),
    min_odds: float | None = Query(
        1.30, gt=1.0, le=100.0,
        description="Floor on the fair odds. Below it the market pays too "
                    "little to be worth a bet; the band is the useful filter"),
    matchday: int | None = Query(None, ge=1, le=38),
) -> dict:
    _known_season(season)
    if min_odds is not None and min_odds > max_odds:
        raise HTTPException(
            status_code=404,
            detail=f"min_odds ({min_odds}) is above max_odds ({max_odds}): "
                   f"that band is empty",
        )
    return _visible(
        selections_mod.selections(
            season, max_odds=max_odds, min_odds=min_odds, matchday=matchday),
        user)


@router.get("/status", response_model=schemas.Status,
            summary="Last scheduler runs, current round, snapshot age")
def get_status() -> dict:
    return status_mod.status()


@public.get("/health", response_model=schemas.Health,
            summary="Liveness and data freshness")
def get_health() -> dict:
    return status_mod.health()
