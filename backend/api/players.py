"""
Top scorers and assist leaders, from Understat's shots.

A FACT, LIKE THE TABLE. Goals and assists already happened; nothing here is a
prediction. It sits on the season screen next to the table for the same reason
the table does.

WHY UNDERSTAT AND NOT FBREF. `fbref_player_season` has goals and assists per
player, but the ingestion that writes it lags weeks behind (three matchdays on
24 October 2026), while `understat_shots` is refreshed with the shots stage.
Checked on 2024/25: 936 goals on both sources, and the 80 penalties match too.

THREE THINGS ABOUT THE SOURCE THAT ARE EASY TO GET WRONG
  * Own goals are shots with `result == "Own Goal"`, credited to the player
    who scored them. They are not his goals and are excluded.
  * Penalties have a NULL `situation`, not "Penalty" — the 601 rows with null
    situation all have xG 0.7613, Understat's penalty value.
  * An assist is the `assist_player` of a goal: the last pass before it.
    Understat records it on 70% of goals, the rest are unassisted or from set
    pieces taken directly. It may differ by one or two from the official
    league count, which also credits won penalties in some seasons.

Team names go through `manual/team_name_map.json`, the same map the results
chain uses: Understat writes "AC Milan" and "Parma Calcio 1913".

READ ONLY. Reads one parquet, writes nothing.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from goalmodel import config

from . import store

log = logging.getLogger("api.players")

COLUMNS = ["league", "season", "game_id", "team", "player", "assist_player", "xg",
           "situation", "result"]


@lru_cache(maxsize=2)
def _shots(stat: tuple, path_str: str) -> pd.DataFrame:
    from pathlib import Path

    from goalmodel import normalize as nz

    df = pd.read_parquet(Path(path_str), columns=COLUMNS)
    df = df[df["league"].isin(config.LEAGUES_PRODUZIONE)].copy()
    mapping = nz.load_name_map()
    df["team"] = nz.clean_name(df["team"]).map(lambda x: mapping.get(x, x))
    df["season"] = df["season"].astype(str)
    return df.reset_index(drop=True)


def _ranked(frame: pd.DataFrame, value: str, limit: int) -> list[dict]:
    """Ordered by `value`; equal values share a position, as in a league table."""
    frame = frame.sort_values([value, "player"], ascending=[False, True]).head(limit)
    rows = []
    for row in frame.to_dict("records"):
        row["position"] = 1 + int((frame[value] > row[value]).sum())
        rows.append(row)
    return rows


def leaders(season: str, limit: int = 10, src: store.Sources | None = None) -> dict | None:
    """Scorers and assist men of the season. `None` if there is no shot at all."""
    src = src or store.default_sources()
    path = src.understat_shots
    if not path.exists():
        log.warning("no shots at %s: run 'goalmodel ingest --stage shots'", path)
        return None

    code = store.season_in(season)
    shots = _shots(store._stat(path), str(path))
    shots = shots[shots["season"] == code]
    if shots.empty:
        return None

    goals = shots[shots["result"] == "Goal"]
    scorers = (
        goals.assign(penalty=goals["situation"].isna())
        .groupby(["player", "team"], as_index=False)
        .agg(goals=("result", "size"), penalties=("penalty", "sum"))
    )
    # xG over every shot, not only goals: it is what says whether the tally
    # is finishing or volume.
    xg = shots.groupby(["player", "team"], as_index=False).agg(
        shots=("result", "size"), xg=("xg", "sum"))
    scorers = scorers.merge(xg, on=["player", "team"], how="left", validate="1:1")
    scorers["penalties"] = scorers["penalties"].astype(int)
    scorers["xg"] = scorers["xg"].astype(float).round(2)

    assisted = shots[shots["assist_player"].notna()]
    assists = (
        assisted[assisted["result"] == "Goal"]
        .groupby(["assist_player", "team"], as_index=False)
        .agg(assists=("result", "size"))
    )
    # xA: the xG of every shot the player set up, scored or not.
    xa = assisted.groupby(["assist_player", "team"], as_index=False).agg(
        key_passes=("result", "size"), xa=("xg", "sum"))
    assists = assists.merge(xa, on=["assist_player", "team"], how="left", validate="1:1")
    assists = assists.rename(columns={"assist_player": "player"})
    assists["xa"] = assists["xa"].astype(float).round(2)

    return {
        "season": store.season_out(code),
        "matches": int(shots["game_id"].nunique()),
        "source": "Understat",
        "scorers": _ranked(scorers, "goals", limit),
        "assists": _ranked(assists, "assists", limit),
    }
