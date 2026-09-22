"""
Selections: every market the model prices, filtered by a ceiling on the odds.

WHAT THIS ANSWERS. "Show me the picks that pay under 1.20" — across all the
markets the project already computes, not just 1X2: double chance, over/under
on five lines, both-teams-to-score, and each team to score.

IT REUSES `report.selezioni`, IT DOES NOT REIMPLEMENT IT. That function already
feeds the terminal table of `predict_round` and the weekly HTML report: it
calls `models.baseline.all_markets` on the stored lambdas, drops the trivial
markets, attaches the readable label and the book's price where the register
has one. A second implementation here would drift from it, and the first
symptom would be a dashboard that disagrees with the Friday terminal output.

THE MARKETS COME FROM THE LAMBDAS, NOT FROM NEW DATA. The track record stores
`lambda_home` and `lambda_away` for every prediction, and every market is a
different sum over the same score matrix — so the full set can be rebuilt
exactly for any past round, without touching production and without an
ingestion.

TWO THINGS THE PAYLOAD IS CAREFUL ABOUT, BECAUSE THEY ARE MEASURED FACTS
  - `book_odds` is null on everything except 1X2. football-data publishes no
    price for double chance, over/under or goal markets, and estimating one by
    applying an average margin would be a made-up number wearing the clothes
    of a measured one. The real price comes from the reader's bookmaker.
  - filtering by odds moves variance, not edge. M1's probabilities come from
    the book's own line with the margin removed, so the expected value is
    minus the margin on every selection at every threshold. The endpoint
    returns hits with their denominator so the sample size stays visible.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import store

log = logging.getLogger("api.selections")

# Market names as produced by `models.baseline.all_markets`. Kept here only to
# resolve the outcome; the list of markets themselves is never duplicated.
BASE_OUTCOMES = {
    "1": ("H",),
    "X": ("D",),
    "2": ("A",),
    "1X": ("H", "D"),
    "12": ("H", "A"),
    "X2": ("D", "A"),
}


def resolve(market: str, goals_home: int, goals_away: int, outcome: str | None) -> bool:
    """
    Did this selection win?

    Raises on an unknown market on purpose: `all_markets` is allowed to grow,
    and a new market must fail loudly here instead of quietly reporting "not
    won" on a bet that won. `tests/test_api.py` walks every market the function
    produces and asserts this one can resolve it.
    """
    if market in BASE_OUTCOMES:
        return outcome in BASE_OUTCOMES[market]

    total = goals_home + goals_away
    if market.startswith("over "):
        return total > float(market.split()[1])
    if market.startswith("under "):
        return total < float(market.split()[1])
    if market == "gol-gol":
        return goals_home > 0 and goals_away > 0
    if market == "no gol":
        return not (goals_home > 0 and goals_away > 0)
    if market == "casa segna":
        return goals_home > 0
    if market == "fuori segna":
        return goals_away > 0

    raise KeyError(
        f"unknown market '{market}': add it to backend/api/selections.resolve, "
        f"or the dashboard will misreport whether that bet won"
    )


def _frame(records: list[dict]) -> pd.DataFrame:
    """The columns `report.selezioni` expects, from the store's match records."""
    return pd.DataFrame([
        {
            "home_team": record["home_team"],
            "away_team": record["away_team"],
            "kickoff": record["kickoff_utc"],
            "lambda_home": record["lambda_home"],
            "lambda_away": record["lambda_away"],
            "odds_home": record["odds_home"],
            "odds_draw": record["odds_draw"],
            "odds_away": record["odds_away"],
        }
        for record in records
    ])


def _best_per_match(rows: list[dict]) -> list[dict]:
    """
    Una selezione per partita: la piu' probabile dentro la banda.

    PERCHE' E' LA VISTA CHE HA PIU' SENSO. L'elenco completo mette in cima
    sempre le stesse quasi-certezze, e la stessa partita compare cinque volte
    con cinque mercati diversi. Qui ogni partita pesa una volta sola, e la
    scelta e' il miglior compromesso fra quanto e' probabile e quanto paga —
    che e' esattamente la domanda di chi deve decidere cosa giocare.

    A PARITA' DI PROBABILITA' VINCE LA QUOTA PIU' ALTA. Capita fra mercati
    equivalenti (12 e over 1.5 possono coincidere al millesimo): il criterio e'
    dichiarato qui invece di dipendere dall'ordine in cui `all_markets` emette
    le colonne, che non e' un criterio.

    NON SI SALVA NEL REGISTRO, SI RICALCOLA. Il registro conserva i due lambda,
    e da quelli ogni mercato si ricostruisce esatto: congelare "la migliore"
    significherebbe non poter piu' cambiare il criterio sulle giornate gia'
    chiuse — e il criterio e' proprio la cosa che si vorra' ritoccare.
    """
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (row["matchday"], row["home_team"], row["away_team"])
        current = best.get(key)
        if current is None or (row["probability"], row["fair_odds"]) > (
            current["probability"], current["fair_odds"]
        ):
            best[key] = row
    return sorted(best.values(), key=lambda r: (-r["matchday"], -r["probability"]))


def _counters(rows: list[dict]) -> tuple[int, int]:
    """Risolte e vinte, contando solo le righe valide (scritte prima del fischio)."""
    scored = [r for r in rows if r["won"] is not None and r["valid"]]
    return len(scored), sum(1 for r in scored if r["won"])


def _rows(season: str, matchday: int | None, src: store.Sources | None) -> tuple[list[dict], int]:
    """
    Every non-trivial market of every predicted match, with its outcome.

    Shared by the two readings below — the canonical picks and the band
    explorer — so they can never disagree about what a market is worth or
    whether it won.
    """
    from goalmodel.reporting.sezioni import selezioni

    records = store.matches(season, src=src)
    if matchday is not None:
        records = [r for r in records if r["matchday"] == matchday]
    usable = [r for r in records
              if r["lambda_home"] is not None and r["lambda_away"] is not None]
    if not usable:
        return [], 0

    table = selezioni(_frame(usable), minimo=0.0)
    by_teams = {(r["home_team"], r["away_team"]): r for r in usable}

    rows: list[dict] = []
    for _, row in table.iterrows():
        home, away = str(row["partita"]).split(" - ", 1)
        record = by_teams.get((home, away))
        if record is None:  # pragma: no cover - the frame is built from these
            continue

        market = str(row["mercato_raw"])
        played = record["goals_home"] is not None and record["goals_away"] is not None
        rows.append({
            "matchday": record["matchday"],
            "kickoff_utc": record["kickoff_utc"],
            "home_team": home,
            "away_team": away,
            "market": market,
            "market_label": str(row["mercato"]),
            "probability": float(row["probabilita"]),
            "fair_odds": float(row["quota_equa"]),
            "book_odds": store._opt_float(row.get("quota_book")),
            "status": record["status"],
            "valid": record["valid"],
            "goals_home": record["goals_home"],
            "goals_away": record["goals_away"],
            "won": (resolve(market, record["goals_home"], record["goals_away"],
                            record["actual_outcome"]) if played else None),
        })
    return rows, len(usable)


def picks(season: str, matchday: int | None = None,
          src: store.Sources | None = None) -> dict:
    """
    The canonical selections: exactly what M1 has produced since day one.

    THIS ENDPOINT TAKES NO THRESHOLD, AND THAT IS THE POINT. The main line has
    to be the one the project declared, not one a query string can move: it is
    the same rule `predict_round` prints on Friday and the weekly report shows,
    with `config.QUOTA_MINIMA_SELEZIONE` as the floor. The band explorer next
    to it is a second, adjustable reading — never a replacement, or the track
    record would be measuring one thing while the dashboard advertises another.

    Two readings, both already defined in `report.py`:
      most_probable    the most likely market of each match, whatever it pays
      with_min_odds    the most likely among those paying at least the declared
                       floor — the best compromise between odds and probability
    """
    from goalmodel import config

    rows, total = _rows(season, matchday, src)
    floor = float(config.QUOTA_MINIMA_SELEZIONE)

    most_probable = _best_per_match(rows)
    payable = _best_per_match([r for r in rows if r["fair_odds"] >= floor])

    probable_resolved, probable_won = _counters(most_probable)
    payable_resolved, payable_won = _counters(payable)

    return {
        "season": store.season_out(store.season_in(season)),
        "matchday": matchday,
        "min_odds": floor,
        "matches_total": total,
        "most_probable": most_probable,
        "most_probable_resolved": probable_resolved,
        "most_probable_won": probable_won,
        "with_min_odds": payable,
        "with_min_odds_resolved": payable_resolved,
        "with_min_odds_won": payable_won,
    }


def selections(
    season: str,
    max_odds: float,
    min_odds: float | None = None,
    matchday: int | None = None,
    src: store.Sources | None = None,
) -> dict:
    """
    Selections whose FAIR odds sit inside the band, newest round first.

    The filter is on the fair odds — `1/p` — because it is the only price that
    exists for every market. The book's price exists for 1X2 alone, and where
    it is missing the field stays null rather than being invented.

    THE TRIVIAL MARKETS ARE NEVER RETURNED, and there is no flag to ask for
    them: `report.selezioni` drops them upstream, so a parameter here would
    have changed nothing while looking like it worked. They are named in
    `excluded_markets` so the omission is visible rather than silent — at a
    1.20 ceiling they would otherwise be three quarters of the list, and
    "at least one goal" is not a bet anyone is offered a real price on.

    THIS IS THE SECONDARY READING. The main line is `picks()`, which uses the
    threshold the project declared and takes no parameters. A band chosen in
    the interface must never become the thing the track record is judged on.
    """
    from goalmodel.reporting.sezioni import TRIVIALI

    everything, total = _rows(season, matchday, src)
    rows = [r for r in everything
            if r["fair_odds"] <= max_odds
            and (min_odds is None or r["fair_odds"] >= min_odds)]
    rows.sort(key=lambda r: (-r["matchday"], r["fair_odds"]))

    # Only rows that count towards a hit rate: played, and written before
    # kickoff. The two rows of round 3 that were logged late have a result but
    # never enter any average — here as everywhere else.
    resolved, won = _counters(rows)
    best = _best_per_match(rows)
    best_resolved, best_won = _counters(best)

    # Copertura: una banda stretta lascia partite senza nessun mercato dentro.
    # Va detto, non omesso: 1.40-1.50 copre 12 partite su 18, e chi guarda solo
    # l'elenco crederebbe che le altre sei non avessero previsioni.
    covered = {(r["matchday"], r["home_team"], r["away_team"]) for r in rows}

    return {
        "season": store.season_out(store.season_in(season)),
        "max_odds": max_odds,
        "min_odds": min_odds,
        "matchday": matchday,
        "excluded_markets": sorted(TRIVIALI),
        "count": len(rows),
        "resolved": resolved,
        "won": won,
        "matches_total": total,
        "matches_covered": len(covered),
        "best_resolved": best_resolved,
        "best_won": best_won,
        "selections": rows,
        "best_per_match": best,
    }
