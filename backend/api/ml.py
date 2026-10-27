"""
The machine-learning model (M5) on a matchday, for the dashboard.

WHAT M5 IS, AND WHY IT IS SHOWN AS A SECOND OPINION. M5 is the LightGBM
anchored to the market: it starts from the opening odds and corrects them with
the form features. On the test set (1140 matches) it is indistinguishable from
the market, so the production model — the one in the track record — stays M1.
This module shows what M5 would say on every market, next to the market, so
the dashboard can display it; it never feeds a metric of the track record.

WHERE THE NUMBERS COME FROM. `goalmodel.experiments.predici_gbm` writes one CSV
per matchday in `experiments/output/` with the two goal expectations of M5 and
of the market. Every market is RECOMPUTED here from those two numbers, through
`models.baseline.all_markets` — the same score matrix the weekly M5 report and
M1 use. Saving market probabilities would freeze a choice that is meant to be
revisited, and give two copies of one fact that can drift apart.

WHY THIS DOES NOT IMPORT `goalmodel.experiments`. The project rule is that
experiments import everything and are imported by nothing. The CSV file is the
contract between the two; its columns are checked below.

READ ONLY, NO MODELS. The file is read, a score matrix is summed. LightGBM is
never loaded: the model ran on the machine that wrote the file.

Results and kickoff times come from `store.round_matches`, so a match shows
the same score, provisional or archived, as everywhere else on the dashboard.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import store
from .selections import resolve

log = logging.getLogger("api.ml")

MODEL = "M5 — LightGBM ancorato al mercato"

# Market groups, in display order. Inside a group the alternatives exclude each
# other (or, for double chance, are the three possible pairs): the most likely
# one according to M5 is the group's pick.
GROUPS: list[tuple[str, list[str]]] = [
    ("1X2", ["1", "X", "2"]),
    ("Doppia chance", ["1X", "X2", "12"]),
    ("Gol / No gol", ["gol-gol", "no gol"]),
    ("Under / Over 1.5", ["under 1.5", "over 1.5"]),
    ("Under / Over 2.5", ["under 2.5", "over 2.5"]),
    ("Under / Over 3.5", ["under 3.5", "over 3.5"]),
]

LABELS = {"gol-gol": "Gol", "no gol": "No gol"}

# The CSV columns this module reads. Checked before use: a renamed column in
# the experiment would otherwise surface as a KeyError deep in a request.
REQUIRED = {"home_team", "away_team", "lam casa M5", "lam fuori M5",
            "lam casa mkt", "lam fuori mkt", "timestamp_prediction"}


def _path(src: store.Sources, code: str, matchday: int):
    return src.ml_predictions / f"previsioni_m5_{code}_{matchday:02d}.csv"


def available(season: str, src: store.Sources | None = None) -> list[int]:
    """The matchdays of a season that have an M5 file."""
    src = src or store.default_sources()
    code = store.season_in(season)
    if not src.ml_predictions.is_dir():
        return []
    out = []
    for path in src.ml_predictions.glob(f"previsioni_m5_{code}_*.csv"):
        try:
            out.append(int(path.stem.rsplit("_", 1)[1]))
        except ValueError:
            continue
    return sorted(out)


def round_ml(season: str, matchday: int, src: store.Sources | None = None) -> dict | None:
    """M5 on one matchday, every market, or None if the file does not exist."""
    from goalmodel import config
    from goalmodel.models.baseline import all_markets, fair_odds

    src = src or store.default_sources()
    code = store.season_in(season)
    path = _path(src, code, matchday)
    if not path.exists():
        return None

    df = pd.read_csv(path)
    missing = REQUIRED - set(df.columns)
    if missing:
        log.warning("%s lacks columns %s: not served", path.name, sorted(missing))
        return None
    df = df.reset_index(drop=True)

    ml = all_markets(df["lam casa M5"].to_numpy(float),
                     df["lam fuori M5"].to_numpy(float), rho=config.DC_RHO)
    mk = all_markets(df["lam casa mkt"].to_numpy(float),
                     df["lam fuori mkt"].to_numpy(float), rho=config.DC_RHO)

    records = {(r["home_team"], r["away_team"]): r
               for r in store.round_matches(season, matchday, src=src)}

    matches = []
    for i, row in df.iterrows():
        rec = records.get((row["home_team"], row["away_team"]), {})
        gh, ga = rec.get("goals_home"), rec.get("goals_away")
        played = gh is not None and ga is not None

        markets = []
        for group, names in GROUPS:
            pick = max(names, key=lambda n: ml.loc[i, n])
            for name in names:
                p, q = float(ml.loc[i, name]), float(mk.loc[i, name])
                odds = float(fair_odds(np.array([p]))[0])
                markets.append({
                    "group": group,
                    "market": name,
                    "label": LABELS.get(name, name),
                    "p_ml": p,
                    "p_market": q,
                    "difference": p - q,
                    "fair_odds_ml": odds if np.isfinite(odds) else None,
                    "pick": name == pick,
                    "won": (resolve(name, gh, ga, rec.get("actual_outcome"))
                            if played else None),
                })

        divergence = max(abs(float(ml.loc[i, n]) - float(mk.loc[i, n]))
                         for n in ("1", "X", "2"))
        kickoff = rec.get("kickoff_utc")
        if kickoff is None and pd.notna(row.get("kickoff_utc")):
            kickoff = store._iso(pd.Timestamp(row["kickoff_utc"]))
        matches.append({
            "matchday": matchday,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "kickoff_utc": kickoff,
            "status": rec.get("status"),
            "goals_home": gh,
            "goals_away": ga,
            "lambda_home_ml": float(row["lam casa M5"]),
            "lambda_away_ml": float(row["lam fuori M5"]),
            "lambda_home_market": float(row["lam casa mkt"]),
            "lambda_away_market": float(row["lam fuori mkt"]),
            "max_divergence_1x2": divergence,
            "markets": markets,
        })

    matches.sort(key=lambda m: (m["kickoff_utc"] or "", m["home_team"]))
    written = pd.to_datetime(df["timestamp_prediction"], utc=True, errors="coerce").min()
    return {
        "season": store.season_out(code),
        "matchday": matchday,
        "model": MODEL,
        "written_at": store._iso(written) if pd.notna(written) else None,
        "matches": matches,
    }


def lambdas(season: str, src: store.Sources | None = None) -> dict:
    """
    M5's goal expectations for every matchday of a season that has a file,
    keyed by (matchday, home, away) — the shape `selections.picks` takes to
    build the machine-learning picks with the same rule as M1's.
    """
    src = src or store.default_sources()
    code = store.season_in(season)
    out: dict = {}
    for matchday in available(season, src):
        df = pd.read_csv(_path(src, code, matchday))
        if REQUIRED - set(df.columns):
            log.warning("matchday %d: M5 file lacks columns, skipped", matchday)
            continue
        for _, row in df.iterrows():
            out[(matchday, row["home_team"], row["away_team"])] = (
                float(row["lam casa M5"]), float(row["lam fuori M5"]))
    return out


def track_record(season: str, src: store.Sources | None = None) -> dict:
    """
    M5 against M1 on the same played matches: RPS per match, per round, overall.

    THE SAME METRIC AND THE SAME METHOD AS THE PROJECT'S. RPS from
    `evaluation.evaluate.rps`, and the paired difference with
    `cluster_bootstrap` on the matchday — the comparison CLAUDE.md prescribes,
    because two models scored on the SAME matches share their difficulty and
    the row-by-row difference removes it. Two separate averages would not.

    ONLY MATCHES BOTH MODELS PRICED, both before kick-off. A row M1 wrote after
    the start (status `invalid`) is out of the track record, and therefore out
    of this comparison as well.

    An honest denominator: with a handful of rounds the interval is wide and
    the payload says how many rounds it rests on, so the page can say "too
    early" instead of announcing a winner.
    """
    import numpy as np
    from goalmodel.evaluation.evaluate import cluster_bootstrap, outcome_index, rps

    src = src or store.default_sources()
    records = {(r["home_team"], r["away_team"]): r for r in store.matches(season, src=src)}

    rows: list[dict] = []
    for matchday in available(season, src):
        payload = round_ml(season, matchday, src)
        if payload is None:
            continue
        for m in payload["matches"]:
            rec = records.get((m["home_team"], m["away_team"]))
            if (rec is None or rec["status"] not in (store.STATUS_PLAYED, store.STATUS_RESOLVED)
                    or rec["actual_outcome"] is None
                    or None in (rec["p_home"], rec["p_draw"], rec["p_away"])):
                continue
            p5 = {mk["market"]: mk["p_ml"] for mk in m["markets"]}
            rows.append({
                "matchday": matchday, "home_team": m["home_team"], "away_team": m["away_team"],
                "goals_home": rec["goals_home"], "goals_away": rec["goals_away"],
                "outcome": rec["actual_outcome"],
                "p1": [rec["p_home"], rec["p_draw"], rec["p_away"]],
                "p5": [p5["1"], p5["X"], p5["2"]],
            })

    if not rows:
        return {"season": store.season_out(store.season_in(season)), "matches": 0,
                "rounds": [], "per_match": [], "rps_classic": None, "rps_ai": None,
                "difference": None, "ci_low": None, "ci_high": None, "ai_better": 0,
                "classic_better": 0, "n_rounds": 0}

    y = outcome_index([r["outcome"] for r in rows])
    r1 = rps(np.array([r["p1"] for r in rows], dtype=float), y)
    r5 = rps(np.array([r["p5"] for r in rows], dtype=float), y)
    days = np.array([r["matchday"] for r in rows])
    diff = r5 - r1

    rounds = []
    for d in sorted(set(days.tolist())):
        mask = days == d
        rounds.append({"matchday": int(d), "matches": int(mask.sum()),
                       "rps_classic": float(r1[mask].mean()), "rps_ai": float(r5[mask].mean())})

    # The bootstrap needs more than one cluster to mean anything.
    ci = cluster_bootstrap(diff, days.astype(str)) if len(rounds) > 1 else None

    return {
        "season": store.season_out(store.season_in(season)),
        "matches": len(rows),
        "n_rounds": len(rounds),
        "rps_classic": float(r1.mean()),
        "rps_ai": float(r5.mean()),
        # Positive = the AI did WORSE: RPS is minimised.
        "difference": float(diff.mean()),
        "ci_low": ci["ic_basso"] if ci else None,
        "ci_high": ci["ic_alto"] if ci else None,
        # A tie within a ten-thousandth is a tie, not a win for anyone.
        "ai_better": int((diff < -1e-4).sum()),
        "classic_better": int((diff > 1e-4).sum()),
        "rounds": rounds,
        "per_match": [
            {"matchday": r["matchday"], "home_team": r["home_team"], "away_team": r["away_team"],
             "goals_home": r["goals_home"], "goals_away": r["goals_away"],
             "rps_classic": float(a), "rps_ai": float(b)}
            for r, a, b in zip(rows, r1, r5, strict=True)
        ],
    }
