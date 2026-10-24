"""
Expected points per team, from the production model's probabilities.

WHAT IT IS. For every played match the log holds p_home, p_draw, p_away,
written before kick-off. A team's expected points for that match are
3 * p(win) + 1 * p(draw); averaged over its matches they say how many points
a game the model thought it would take. Next to the points actually taken,
the gap is the luck-or-surprise reading of the table: a team far above its
expected points is winning more than its prices said.

ONLY PLAYED MATCHES. The upcoming round's probabilities are what
`picks:read` gates; summed into a per-team figure they would still leak it.
On played matches everything here is history, public like the track record.

THE PROJECTION IS A SIMPLIFICATION, AND SAYS SO. Points so far plus the
model's average per game times the games left. It ignores who the remaining
opponents are — the model has no prices for matches whose odds are not out
yet, and inventing them would be a forecast the project never registered.
The field is named `projected_points`, not "predicted": it is an
extrapolation of the season's expected rate, nothing more.

READ ONLY, NO MODELS: probabilities come from the log through `store`.
"""

from __future__ import annotations

from . import standings as standings_mod
from . import store


def expected_points(season: str, src: store.Sources | None = None) -> dict | None:
    """Points vs expected points per team. `None` if nothing is played."""
    table = standings_mod.standings(season, src)
    if table is None:
        return None

    per_team: dict[str, dict] = {}
    for match in store.matches(season, src=src):
        if match["goals_home"] is None or match["p_home"] is None:
            continue
        p_home, p_draw, p_away = match["p_home"], match["p_draw"], match["p_away"]
        for team, p_win, scored, conceded in (
            (match["home_team"], p_home, match["goals_home"], match["goals_away"]),
            (match["away_team"], p_away, match["goals_away"], match["goals_home"]),
        ):
            row = per_team.setdefault(team, {"matches": 0, "xpts": 0.0, "pts": 0})
            row["matches"] += 1
            row["xpts"] += 3 * p_win + p_draw
            row["pts"] += 3 if scored > conceded else 1 if scored == conceded else 0

    total_games = 2 * (table["teams"] - 1)
    rows = []
    for standing in table["table"]:
        team = standing["team"]
        mine = per_team.get(team)
        if not mine or mine["matches"] == 0:
            xppg = None
            pts_predicted = None
        else:
            xppg = mine["xpts"] / mine["matches"]
            pts_predicted = mine["pts"]
        left = max(total_games - standing["played"], 0)
        rows.append({
            "team": team,
            "position": standing["position"],
            "played": standing["played"],
            "points": standing["points"],
            "points_per_game": round(standing["points"] / standing["played"], 2)
            if standing["played"] else None,
            "predicted_matches": mine["matches"] if mine else 0,
            "expected_points": round(mine["xpts"], 2) if mine else None,
            "points_in_predicted": pts_predicted,
            "expected_points_per_game": round(xppg, 2) if xppg is not None else None,
            "projected_points": round(standing["points"] + xppg * left, 1)
            if xppg is not None else None,
        })

    rows.sort(key=lambda r: (r["projected_points"] is None, -(r["projected_points"] or 0)))
    return {
        "season": table["season"],
        "total_games": total_games,
        "model_version": store.PRODUCTION_MODEL,
        "table": rows,
    }
