"""
Genera dati sintetici in data/raw che riproducono la struttura delle fonti
reali, inclusi i disallineamenti di nome tipici. Serve a testare normalize.py
senza dover scaricare nulla.

    python -m tests.make_fixtures
"""

import itertools
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402

rng = np.random.default_rng(42)

# Nomi come li scrive football-data.co.uk (tabella base)
TEAMS_BASE = [
    "Napoli", "Inter", "Milan", "Juventus", "Roma", "Lazio",
    "Atalanta", "Fiorentina", "Bologna", "Verona",
]

# Come li scrive Understat. Casi deliberatamente difficili:
#  - "Internazionale" vs "Inter": difflib da solo NON lo trovava (ratio 0.53)
#  - prefissi societari: AC, AS, SSC
#  - accenti: Hellas Verona con accento fittizio
#  - il caso pericoloso: Roma e Lazio sono entrambe di Roma, e "AS Roma"
#    non deve finire su "Lazio"
TEAMS_UNDERSTAT = {
    "Inter": "Internazionale",
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "Napoli": "SSC Nàpoli",
    "Verona": "Hellas Verona FC",
    "Juventus": "Juventus FC",
}

SEASONS = ["2425", "2526"]
LEAGUE = "ITA-Serie A"


def build() -> None:
    base_rows, us_rows = [], []
    start = datetime(2024, 8, 20)

    for season in SEASONS:
        day = start
        for home, away in itertools.permutations(TEAMS_BASE, 2):
            day += timedelta(hours=30)
            hg, ag = int(rng.poisson(1.5)), int(rng.poisson(1.2))

            base_rows.append({
                "league": LEAGUE, "season": season, "date": day,
                "home_team": home, "away_team": away,
                "FTHG": hg, "FTAG": ag,
                "FTR": "H" if hg > ag else ("A" if ag > hg else "D"),
                "HS": int(rng.poisson(13)), "AS": int(rng.poisson(11)),
                "B365H": round(float(rng.uniform(1.3, 6)), 2),
                "B365D": round(float(rng.uniform(3, 5)), 2),
                "B365A": round(float(rng.uniform(1.3, 8)), 2),
                "referee": f"Arbitro{rng.integers(1, 12)}",
            })

            us_rows.append({
                "league": LEAGUE,
                # formato stagione diverso: il codice deve uniformarlo
                "season": f"20{season[:2]}-20{season[2:]}",
                # data sfasata di un giorno: fuso orario
                "date": day + timedelta(days=1),
                "home_team": TEAMS_UNDERSTAT.get(home, home),
                "away_team": TEAMS_UNDERSTAT.get(away, away),
                "home_xg": round(float(rng.uniform(0.4, 3.2)), 2),
                "away_xg": round(float(rng.uniform(0.3, 2.8)), 2),
                "home_np_xg": round(float(rng.uniform(0.3, 3.0)), 2),
                "away_np_xg": round(float(rng.uniform(0.2, 2.6)), 2),
                "home_ppda": round(float(rng.uniform(6, 20)), 2),
                "away_ppda": round(float(rng.uniform(6, 20)), 2),
                "home_deep_completions": int(rng.poisson(8)),
                "away_deep_completions": int(rng.poisson(6)),
                "home_expected_points": round(float(rng.uniform(0, 3)), 2),
                "away_expected_points": round(float(rng.uniform(0, 3)), 2),
            })

    pd.DataFrame(base_rows).to_parquet(config.RAW / "matches.parquet", index=False)
    pd.DataFrame(us_rows).to_parquet(
        config.RAW / "understat_team_match.parquet", index=False
    )
    print(f"fixture: {len(base_rows)} partite base, {len(us_rows)} righe understat")
    print(f"disallineamenti deliberati: {list(TEAMS_UNDERSTAT.values())}")


if __name__ == "__main__":
    build()