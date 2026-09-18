"""
The league table, computed from played matches.

WHY THIS IS NOT A PREDICTION, AND WHY THAT MATTERS. Everything else the API
serves is the model talking. This is arithmetic on results that already
happened: three points a win, one a draw. It sits on the landing page next to
the predictions precisely because the two must not be confused — one is a fact,
the other is a probability.

TIE-BREAKING IS THE PART THAT GOES WRONG QUIETLY. Serie A does not separate
tied teams by goal difference: it uses the head-to-head results between them
first, and goal difference only after. Ordering by points and goal difference
would produce a table that looks right, is published as right, and is wrong in
exactly the weeks when the standings are interesting. So the head-to-head
mini-table is computed here:

    1. points
    2. points in the matches between the tied teams
    3. goal difference in those matches
    4. overall goal difference
    5. goals scored

With four rounds played, ties are the normal case rather than the exception,
which is why it is worth the twenty lines.

READ ONLY. Reads `data/interim/matches_master.parquet` and writes nothing.
"""

from __future__ import annotations

import logging
from itertools import combinations

import pandas as pd

from . import store

log = logging.getLogger("api.standings")

WIN, DRAW = 3, 1


def _blank(team: str) -> dict:
    return {"team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0, "points": 0}


def _accumulate(played: pd.DataFrame) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for row in played.itertuples(index=False):
        home = table.setdefault(row.home_team, _blank(row.home_team))
        away = table.setdefault(row.away_team, _blank(row.away_team))
        goals_home, goals_away = int(row.FTHG), int(row.FTAG)

        for side, scored, conceded in ((home, goals_home, goals_away),
                                       (away, goals_away, goals_home)):
            side["played"] += 1
            side["goals_for"] += scored
            side["goals_against"] += conceded

        if goals_home > goals_away:
            home["won"] += 1
            away["lost"] += 1
            home["points"] += WIN
        elif goals_home < goals_away:
            away["won"] += 1
            home["lost"] += 1
            away["points"] += WIN
        else:
            home["drawn"] += 1
            away["drawn"] += 1
            home["points"] += DRAW
            away["points"] += DRAW
    return table


def _head_to_head(teams: set[str], played: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """Punti e differenza reti nei soli scontri fra le squadre a pari punti."""
    points = dict.fromkeys(teams, 0)
    diff = dict.fromkeys(teams, 0)

    subset = played[played["home_team"].isin(teams) & played["away_team"].isin(teams)]
    for row in subset.itertuples(index=False):
        goals_home, goals_away = int(row.FTHG), int(row.FTAG)
        diff[row.home_team] += goals_home - goals_away
        diff[row.away_team] += goals_away - goals_home
        if goals_home > goals_away:
            points[row.home_team] += WIN
        elif goals_home < goals_away:
            points[row.away_team] += WIN
        else:
            points[row.home_team] += DRAW
            points[row.away_team] += DRAW
    return {team: (points[team], diff[team]) for team in teams}


def _recent_form(played: pd.DataFrame, last: int = 5) -> dict[str, list[str]]:
    """
    Gli ultimi risultati di ogni squadra, dal piu' vecchio al piu' recente.

    E' il "trend e forma" della pagina, e va preso in ORDINE DI DATA: dai
    totali di vittorie e pareggi della classifica non si ricava, perche' la
    sequenza e' proprio l'informazione che interessa. Quattro vittorie non
    dicono se la squadra sta salendo o rientrando.
    """
    righe = []
    for row in played.itertuples(index=False):
        goals_home, goals_away = int(row.FTHG), int(row.FTAG)
        esito_casa = "W" if goals_home > goals_away else "L" if goals_home < goals_away else "D"
        esito_fuori = "W" if goals_away > goals_home else "L" if goals_away < goals_home else "D"
        righe.append((row.date, row.home_team, esito_casa))
        righe.append((row.date, row.away_team, esito_fuori))

    frame = pd.DataFrame(righe, columns=["date", "team", "outcome"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    return {
        str(team): group["outcome"].tolist()[-last:]
        for team, group in frame.groupby("team", sort=False)
    }


def _ordered(table: dict[str, dict], played: pd.DataFrame) -> list[dict]:
    rows = list(table.values())
    for row in rows:
        row["goal_difference"] = row["goals_for"] - row["goals_against"]

    by_points: dict[int, list[dict]] = {}
    for row in rows:
        by_points.setdefault(row["points"], []).append(row)

    ordered: list[dict] = []
    for points in sorted(by_points, reverse=True):
        group = by_points[points]
        if len(group) == 1:
            ordered.extend(group)
            continue
        names = {row["team"] for row in group}
        h2h = _head_to_head(names, played)
        group.sort(key=lambda r: (h2h[r["team"]][0], h2h[r["team"]][1],
                                  r["goal_difference"], r["goals_for"]),
                   reverse=True)
        ordered.extend(group)

    for position, row in enumerate(ordered, start=1):
        row["position"] = position
    return ordered


def standings(season: str, src: store.Sources | None = None) -> dict | None:
    """La classifica della stagione. `None` se non c'e' nessuna partita giocata."""
    src = src or store.default_sources()
    if not src.master.exists():
        raise FileNotFoundError(
            f"{src.master.name} is missing: run 'python -m src.normalize --build'")

    master = pd.read_parquet(src.master)
    code = store.season_in(season)
    played = master[(master["season"].astype(str) == code) & master["FTR"].notna()]
    played = played.dropna(subset=["FTHG", "FTAG"])
    if played.empty:
        return None

    rows = _ordered(_accumulate(played), played)

    form = _recent_form(played)
    for row in rows:
        row["form"] = form.get(row["team"], [])

    # Controllo di coerenza: i gol fatti in totale devono uguagliare i subiti.
    # Se un giorno non tornasse, il difetto sta nell'accumulo e non nei dati,
    # e questo lo direbbe subito invece di pubblicare una tabella sbagliata.
    scored = sum(row["goals_for"] for row in rows)
    conceded = sum(row["goals_against"] for row in rows)
    if scored != conceded:  # pragma: no cover - invariante aritmetica
        log.error("classifica incoerente: %d gol fatti contro %d subiti", scored, conceded)

    return {
        "season": store.season_out(code),
        "matches_played": int(len(played)),
        "teams": len(rows),
        "last_match_date": store._iso(pd.to_datetime(played["date"]).max()),
        "tie_break": "punti, scontri diretti, differenza reti negli scontri "
                     "diretti, differenza reti generale, gol fatti",
        "table": rows,
    }
