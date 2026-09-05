"""
Il walk-forward non lascia entrare il futuro nell'addestramento.

E' la regola non negoziabile numero 1 del progetto. Un leakage non produce
errori: produce metriche migliori. E' l'unico difetto che si auto-premia, e
per questo va verificato da fuori invece che sperare negli assert interni.

Il test costruisce un dataset sintetico dove OGNI partita ha una data e un
risultato noti, e verifica per ogni blocco che il training contenga solo
partite anteriori. Poi prova a rompere la regola di proposito e controlla che
l'harness se ne accorga.

Uso:
    python -m tests.test_leakage
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.evaluate import walk_forward  # noqa: E402
from src.models.baseline import Model, PRED_COLS  # noqa: E402


class Spia(Model):
    """
    Non predice: registra cosa ha visto in addestramento.

    E' il modo per ispezionare l'harness dall'interno senza modificarlo: se
    una partita futura finisse nel training, questo modello la vedrebbe.
    """

    name = "spia"

    def __init__(self) -> None:
        self.visto: list[dict] = []

    def fit(self, train: pd.DataFrame) -> "Spia":
        self._train = train
        return self

    def predict(self, test: pd.DataFrame) -> pd.DataFrame:
        self.visto.append({
            "cutoff": test["date"].min(),
            "train_max": self._train["date"].max() if len(self._train) else pd.NaT,
            "n_train": len(self._train),
            "chiavi_train": set(zip(self._train["season"], self._train["home_team"],
                                    self._train["away_team"])),
            "chiavi_test": set(zip(test["season"], test["home_team"], test["away_team"])),
        })
        out = pd.DataFrame(np.nan, index=test.index, columns=PRED_COLS)
        out["p_home"], out["p_draw"], out["p_away"] = 1 / 3, 1 / 3, 1 / 3
        return out


def _finto(n_stagioni: int = 3, squadre: int = 8) -> pd.DataFrame:
    """
    Calendario sintetico: ogni squadra incontra ogni altra, una giornata a
    settimana, con una partita rinviata di proposito in ogni stagione.
    Il rinvio e' il caso che distingue il taglio per data da quello per
    giornata: appartiene a una giornata vecchia ma si gioca molto dopo.
    """
    rng = np.random.default_rng(0)
    nomi = [f"T{i}" for i in range(squadre)]
    righe = []
    for s in range(n_stagioni):
        stagione = f"{20+s}{21+s}"
        inizio = pd.Timestamp(f"20{20+s}-09-01")
        giornata = 0
        for i in range(squadre - 1):
            giornata += 1
            data = inizio + pd.Timedelta(weeks=giornata)
            for j in range(squadre // 2):
                casa, fuori = nomi[j], nomi[squadre - 1 - j]
                if casa == fuori:
                    continue
                righe.append({
                    "league": "ITA-Serie A", "season": stagione,
                    "home_team": casa, "away_team": fuori,
                    "matchday": giornata, "date": data,
                    "FTHG": rng.poisson(1.5), "FTAG": rng.poisson(1.2),
                })
            nomi = [nomi[0]] + [nomi[-1]] + nomi[1:-1]
        # una partita della giornata 2 rinviata a fine stagione
        for r in righe:
            if r["season"] == stagione and r["matchday"] == 2:
                r["date"] = inizio + pd.Timedelta(weeks=squadre + 4)
                break

    df = pd.DataFrame(righe)
    df["FTR"] = np.where(df["FTHG"] > df["FTAG"], "H",
                         np.where(df["FTHG"] < df["FTAG"], "A", "D"))
    return df.sort_values("date").reset_index(drop=True)


def test_nessun_futuro_nel_training() -> None:
    df = _finto()
    stagioni = sorted(df["season"].unique())
    spia = Spia()
    walk_forward(df, [spia], test_seasons=[stagioni[-1]], exclude_seasons=[])

    assert spia.visto, "l'harness non ha prodotto blocchi"
    for b in spia.visto:
        if b["n_train"] == 0:
            continue
        assert b["train_max"] < b["cutoff"], (
            f"training fino a {b['train_max']} per un blocco che inizia "
            f"il {b['cutoff']}: c'e' futuro nell'addestramento"
        )
        assert not (b["chiavi_train"] & b["chiavi_test"]), \
            "una partita del blocco di test e' anche nel training"
    print(f"1. {len(spia.visto)} blocchi, nessun futuro nel training      ok")


def test_il_training_cresce() -> None:
    df = _finto()
    stagioni = sorted(df["season"].unique())
    spia = Spia()
    walk_forward(df, [spia], test_seasons=[stagioni[-1]], exclude_seasons=[])
    n = [b["n_train"] for b in spia.visto]
    assert all(a <= b for a, b in zip(n, n[1:])), \
        f"il training non e' monotono crescente: {n}"
    print(f"2. training da {n[0]} a {n[-1]} righe, monotono          ok")


def test_partita_rinviata() -> None:
    """
    Una partita rinviata non deve finire nel training di se stessa.

    Con il taglio per giornata succederebbe: appartiene alla giornata 2 ma si
    gioca a fine stagione, quindi sarebbe gia' "passata" per ogni blocco
    successivo al secondo, incluso il proprio.
    """
    df = _finto()
    stagioni = sorted(df["season"].unique())
    ultima = df[df["season"] == stagioni[-1]]
    rinviata = ultima.loc[ultima["matchday"] == 2].sort_values("date").iloc[-1]

    spia = Spia()
    walk_forward(df, [spia], test_seasons=[stagioni[-1]], exclude_seasons=[])
    chiave = (rinviata["season"], rinviata["home_team"], rinviata["away_team"])
    for b in spia.visto:
        if chiave in b["chiavi_test"]:
            assert chiave not in b["chiavi_train"], \
                "la partita rinviata e' nel training del proprio blocco"
    print(f"3. partita rinviata ({rinviata['date'].date()}) mai in "
          f"training di se stessa  ok")


def test_leakage_deliberato_viene_visto() -> None:
    """
    Se si sposta una partita di test nel passato senza toglierla dal test,
    l'harness deve accorgersene. Serve a dimostrare che i controlli mordono:
    un test anti-leakage che passa sempre non prova niente.
    """
    df = _finto()
    stagioni = sorted(df["season"].unique())
    ultima = df["season"] == stagioni[-1]
    # duplico una partita di test datandola nel passato: ora e' in entrambi
    riga = df[ultima].iloc[5].copy()
    riga["date"] = df["date"].min()
    rotto = pd.concat([df, pd.DataFrame([riga])], ignore_index=True)

    try:
        walk_forward(rotto, [Spia()], test_seasons=[stagioni[-1]], exclude_seasons=[])
    except AssertionError as exc:
        print(f"4. leakage deliberato intercettato: {str(exc)[:44]}...  ok")
        return
    raise AssertionError(
        "il leakage deliberato NON e' stato intercettato: i controlli "
        "dell'harness non mordono"
    )


def main() -> None:
    import logging
    logging.getLogger("evaluate").setLevel(logging.WARNING)
    test_nessun_futuro_nel_training()
    test_il_training_cresce()
    test_partita_rinviata()
    test_leakage_deliberato_viene_visto()
    print("\ntutti i controlli anti-leakage superati")


if __name__ == "__main__":
    main()
