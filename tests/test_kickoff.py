"""
Il calcio d'inizio calcolato da fbref coincide con quello di football-data?

PERCHE' QUESTO TEST ESISTE
Per un periodo `predict.kickoff()` ha letto l'orario di fbref come se fosse
UTC. E' ora LOCALE DELLO STADIO: per la Serie A, ora italiana. L'errore
spostava il fischio d'inizio di due ore in avanti d'estate, e l'assert che
doveva impedire una previsione tardiva la lasciava passare. Due righe del
registro furono scritte a partita gia' iniziata senza che niente protestasse.

Un errore di fuso non produce eccezioni: produce una data valida e sbagliata.
L'unico modo di accorgersene e' confrontare due fonti indipendenti che
descrivono lo stesso istante in fusi diversi.

Uso:
    python -m tests.test_kickoff
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.normalize import (  # noqa: E402
    apply_name_map, load_name_map, load_raw, normalize_season,
)
from src.predict import kickoff  # noqa: E402

KEYS = ["league", "season", "home_team", "away_team"]


def test_fuso_sintetico() -> None:
    """Casi noti a mano, senza dipendere dai dati scaricati."""
    df = pd.DataFrame({
        "league": ["ITA-Serie A"] * 4,
        "date": pd.to_datetime(["2026-09-06", "2026-01-10", "2026-09-06", "2026-01-10"]),
        "time": ["20:45", "20:45", None, None],
    })
    ko = kickoff(df)

    # Settembre: ora legale, Roma = UTC+2 -> 20:45 locali sono 18:45 UTC.
    assert ko.iloc[0] == pd.Timestamp("2026-09-06 18:45", tz="UTC"), ko.iloc[0]
    # Gennaio: ora solare, Roma = UTC+1 -> 20:45 locali sono 19:45 UTC.
    assert ko.iloc[1] == pd.Timestamp("2026-01-10 19:45", tz="UTC"), ko.iloc[1]
    # Senza orario si prende mezzanotte locale, che in UTC cade la sera prima:
    # e' il limite prudente, anticipa il fischio invece di posticiparlo.
    assert ko.iloc[2] == pd.Timestamp("2026-09-05 22:00", tz="UTC"), ko.iloc[2]
    assert ko.iloc[3] == pd.Timestamp("2026-01-09 23:00", tz="UTC"), ko.iloc[3]
    print("1. conversione fuso, casi noti                       ok")

    # La regressione specifica: se qualcuno rimettesse tz_localize('UTC'),
    # questo scatterebbe.
    assert ko.iloc[0] != pd.Timestamp("2026-09-06 20:45", tz="UTC"), \
        "l'orario di fbref e' stato letto come UTC: e' ora locale"
    print("2. l'orario NON e' interpretato come UTC             ok")


def test_contro_football_data(soglia: float = 0.98) -> None:
    """
    Le due fonti devono descrivere lo stesso istante.

    fbref pubblica in ora locale dello stadio, football-data in ora del Regno
    Unito. Convertite entrambe in UTC devono coincidere. E' il controllo che
    ha scoperto il difetto, e resta l'unico capace di riscoprirlo.
    """
    master = config.INTERIM / "matches_master.parquet"
    if not master.exists():
        print("   (saltato: manca matches_master.parquet)")
        return

    sched = normalize_season(apply_name_map(load_raw("fbref_schedule"), load_name_map()))
    sched = sched[sched["league"].isin(config.LEAGUES)].dropna(subset=["time"])
    sched["kickoff"] = kickoff(sched)
    sched["season"] = sched["season"].astype(str)

    fd = pd.read_parquet(master)[KEYS + ["date"]].rename(columns={"date": "fd"})
    fd["season"] = fd["season"].astype(str)
    j = sched[KEYS + ["kickoff"]].merge(fd, on=KEYS, how="inner")
    # football-data mette mezzanotte quando non ha l'orario: quelle righe non
    # dicono niente sul fuso e vanno escluse.
    j = j[j["fd"].dt.time != pd.Timestamp("00:00").time()]
    if len(j) < 100:
        print(f"   (saltato: solo {len(j)} partite con orario su entrambe)")
        return

    fd_utc = (j["fd"].dt.tz_localize("Europe/London", ambiguous=True,
                                     nonexistent="shift_forward").dt.tz_convert("UTC"))
    scarto_min = (j["kickoff"] - fd_utc).dt.total_seconds().abs() / 60
    quota = (scarto_min < 1).mean()

    print(f"3. contro football-data: {quota:.2%} coincide al minuto "
          f"su {len(j)} partite   {'ok' if quota >= soglia else 'FALLITO'}")
    assert quota >= soglia, (
        f"solo il {quota:.1%} degli orari coincide fra le due fonti "
        f"(soglia {soglia:.0%}). Se e' calato di colpo, e' cambiato il fuso "
        f"di una delle due fonti: controlla config.LEAGUE_TIMEZONE."
    )

    # Lo scarto residuo dev'essere spostamento di orario, non un offset fisso.
    residuo = scarto_min[scarto_min >= 1]
    if len(residuo):
        assert residuo.median() > 5, (
            "gli scarti residui sono tutti piccoli e sistematici: sembra un "
            "errore di fuso, non partite spostate"
        )
    print(f"4. gli scarti residui sono spostamenti, non offset   ok "
          f"({len(residuo)} partite)")


def test_ordine_previsione_fischio() -> None:
    """
    Sul registro reale: nessuna previsione valida puo' seguire il fischio.

    Non fallisce sulle due righe storiche note — sono conservate di proposito —
    ma verifica che `flag_post_kickoff` continui a riconoscerle.
    """
    from src.backtest_log import flag_post_kickoff, load_log

    try:
        log = load_log()
    except FileNotFoundError:
        print("   (saltato: registro non ancora creato)")
        return

    marcato = flag_post_kickoff(log)
    tardive = int(marcato["post_kickoff"].sum())
    print(f"5. registro: {len(marcato)} righe, {tardive} riconosciute tardive  ok")
    assert "post_kickoff" in marcato.columns, "il controllo non ha prodotto la colonna"

    valide = marcato[~marcato["post_kickoff"]]
    noto = valide["kickoff"].notna()
    if noto.any():
        assert (valide.loc[noto, "timestamp_prediction"]
                < valide.loc[noto, "kickoff"]).all(), \
            "una riga considerata valida e' successiva al calcio d'inizio"
    print("6. le righe valide precedono tutte il fischio        ok")


def main() -> None:
    test_fuso_sintetico()
    test_contro_football_data()
    test_ordine_previsione_fischio()
    print("\ntutti i controlli sul calcio d'inizio superati")


if __name__ == "__main__":
    main()
