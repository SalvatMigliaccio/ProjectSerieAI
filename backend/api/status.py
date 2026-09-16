"""
What `/api/status` answers: is the machine running, and what happens next.

THE POINT OF THIS ENDPOINT. The frontend must be able to show that the system
works without being able to command it. So everything here is observation:
what the scheduler recorded, what state the current round is in, how old the
odds snapshot is. There is no endpoint that makes any of it happen — that is
the whole design, not a limitation.

TWO SOURCES, AND WHY THEY ARE DIFFERENT IN KIND
  logs/last_run.json   what the scheduler did. Written by scripts/run_task.cmd,
                       never parsed out of prose (see last_run.py).
  the data itself      which round is current, its state, snapshot age. These
                       are facts on disk and cannot drift out of sync with
                       reality the way a log line can.

If `last_run.json` is missing — first install, or the task never fired — the
answer is "no recorded run", which is information, not an error.
"""

from __future__ import annotations

import logging

from . import store

# `last_run` is imported inside the functions, not here: the scheduler invokes
# it as `python -m backend.api.last_run`, and a module already imported by the
# package would make runpy print a RuntimeWarning into every scheduler log —
# a line that looks like a failure in a file whose whole job is to say whether
# something failed.

log = logging.getLogger("api.status")

# Older than this, the snapshot no longer describes the upcoming round.
SNAPSHOT_STALE_HOURS = 24 * 7


def _current_round(season: str, src: store.Sources | None) -> tuple[dict | None, list[str]]:
    """
    The round the commands would act on now, from the project's state machine.

    Uses `rounds.da_predire` / `rounds.da_chiudere` rather than a second
    calendar of our own: a divergence between what the API announces and what
    `predict_round` actually does would be worse than no announcement.
    """
    warnings: list[str] = []
    states = store.round_states(season, src)
    if states.empty:
        warnings.append(
            "round states unavailable: the calendar or matches_master is missing. "
            "Run 'python -m src.normalize --build'.")
        return None, warnings

    from src.rounds import da_chiudere, da_predire

    to_predict = da_predire(states)
    to_close = da_chiudere(states)

    if to_predict is not None:
        row = to_predict
        action = {
            "command": "predict_round",
            "matchday": int(row["matchday"]),
            "reason": f"{int(row['n_predicibili'])} matches can still be predicted",
        }
    elif to_close is not None:
        row = to_close
        action = {
            "command": "close_round",
            "matchday": int(row["matchday"]),
            "reason": "every match has a result and the round is not archived yet",
        }
    else:
        # Nothing to do is a normal state. Name the round anyway, or the
        # frontend cannot tell "all good" from "no data".
        #
        # The current round is the next one still ahead, NOT simply the first
        # that is not closed: rounds played before the track record existed
        # stay 'giocata' forever with no predictions to archive, and picking
        # them would make the API announce matchday 1 in September.
        upcoming = states[~states["stato"].isin(("chiusa", "giocata"))]
        row = upcoming.iloc[0] if not upcoming.empty else states.iloc[-1]
        reason = ("waiting for the odds of the upcoming round"
                  if str(row["stato"]) == "futura"
                  else "nothing to do: no match is predictable or closable right now")
        action = {
            "command": None,
            "matchday": int(row["matchday"]),
            "reason": reason,
        }

    return {
        "matchday": int(row["matchday"]),
        "status": store.ROUND_STATUS.get(str(row["stato"])),
        "action": action,
    }, warnings


def status(src: store.Sources | None = None) -> dict:
    """The whole payload for /api/status."""
    from . import last_run

    src = src or store.default_sources()
    fresh = store.freshness(src)
    seasons = fresh["seasons"]
    season = seasons[-1] if seasons else None

    warnings: list[str] = []
    current = None
    if season is not None:
        current, warnings = _current_round(season, src)

    snapshot = store.odds_snapshot(src)
    if snapshot["exists"] and snapshot.get("empty"):
        warnings.append(
            "odds snapshot is empty: the round has not been published yet. "
            "football-data publishes Friday by 17:00 UK and Tuesday by 13:00.")
    elif snapshot.get("age_hours") is not None and snapshot["age_hours"] > SNAPSHOT_STALE_HOURS:
        warnings.append(
            f"odds snapshot is {snapshot['age_hours'] / 24:.1f} days old: it only "
            f"ever covers the imminent round and gets overwritten.")

    runs = last_run.read()
    for command in last_run.COMMANDS:
        entry = runs.get(command)
        if entry is None:
            warnings.append(f"no recorded run of {command} yet")
        elif entry.get("ok") is False:
            warnings.append(
                f"last {command} failed with exit code {entry.get('exit_code')}: "
                f"see logs/{entry.get('log')}")

    return {
        "season": season,
        "updated_at": fresh["updated_at"],
        "current_round": None if current is None else current["matchday"],
        "current_round_status": None if current is None else current["status"],
        "last_runs": [runs[c] for c in last_run.COMMANDS if c in runs],
        "odds_snapshot": snapshot,
        "next_action": (current["action"] if current else
                        {"command": None, "matchday": None,
                         "reason": "no season data available"}),
        "warnings": warnings,
    }


def health(src: store.Sources | None = None) -> dict:
    """
    Cheap liveness plus the one thing worth knowing: how fresh the data is.

    `degraded` means the server is up but has nothing to serve — a fresh clone
    without a track record, or a missing log. It is not an error status: the
    frontend should render an empty state, not a crash.
    """
    fresh = store.freshness(src)
    ok = fresh["predictions_log_exists"] and fresh["matches_logged"] > 0
    return {
        "status": "ok" if ok else "degraded",
        "updated_at": fresh["updated_at"],
        "seasons": fresh["seasons"],
        "predictions_log_exists": fresh["predictions_log_exists"],
        "matches_logged": fresh["matches_logged"],
        "rounds_closed": fresh["rounds_closed"],
    }
