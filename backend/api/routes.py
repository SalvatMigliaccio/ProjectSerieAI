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

import logging

from fastapi import APIRouter, HTTPException, Query

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

router = APIRouter(prefix="/api")

VALID_STATUS = (store.STATUS_PREDICTED, store.STATUS_RESOLVED, store.STATUS_INVALID)


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


@router.get("/season/{season}", response_model=schemas.SeasonSummary,
            summary="Season headline figures")
def get_season(season: str) -> dict:
    _known_season(season)
    summary = store.season_summary(season)
    if summary is None:  # pragma: no cover - _known_season already 404s
        raise HTTPException(status_code=404, detail=f"season '{season}' not found")
    return summary


@router.get("/rounds/{season}", response_model=list[schemas.Round],
            summary="Every round with its state and archived RPS")
def get_rounds(season: str) -> list[dict]:
    _known_season(season)
    return store.rounds(season)


@router.get("/rounds/{season}/{matchday}", response_model=list[schemas.Match],
            summary="Matches of one round")
def get_round(season: str, matchday: int) -> list[dict]:
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
    return matches


@router.get("/matches/{season}", response_model=list[schemas.Match],
            summary="All matches of a season, filterable")
def get_matches(
    season: str,
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
    return store.matches(season, team=team, status=status,
                         date_from=date_from, date_to=date_to)


@router.get("/track-record/{season}", response_model=schemas.TrackRecord,
            summary="Cumulative RPS series and calibration")
def get_track_record(season: str) -> dict:
    _known_season(season)
    record = store.track_record(season)
    if record is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"season '{season}' not found")
    return record


@router.get("/standings/{season}", response_model=schemas.Standings,
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


@router.get("/picks/{season}", response_model=schemas.Picks,
            summary="The canonical M1 selections — no threshold to move")
def get_picks(season: str, matchday: int | None = Query(None, ge=1, le=38)) -> dict:
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
    return selections_mod.picks(season, matchday=matchday)


@router.get("/selections/{season}", response_model=schemas.Selections,
            summary="Markets priced under a ceiling on the odds")
def get_selections(
    season: str,
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
    return selections_mod.selections(
        season, max_odds=max_odds, min_odds=min_odds, matchday=matchday)


@router.get("/status", response_model=schemas.Status,
            summary="Last scheduler runs, current round, snapshot age")
def get_status() -> dict:
    return status_mod.status()


@router.get("/health", response_model=schemas.Health,
            summary="Liveness and data freshness")
def get_health() -> dict:
    return status_mod.health()
