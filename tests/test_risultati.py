"""
La catena delle fonti dei risultati, su dati finti in memoria.

Nessun file toccato: le tre fonti si sostituiscono con funzioni che
restituiscono DataFrame, e il registro delle giornate chiuse sta in una
cartella temporanea. Controlla le quattro cose che, sbagliate, non darebbero
nessun errore visibile:

    1. football-data vince sempre dove c'e'
    2. il ripiego riempie solo i buchi, nell'ordine Understat -> FBref
    3. una partita non finita (is_result falso) non diventa un risultato
    4. riconcilia avvisa quando l'archiviato differisce dall'ufficiale

Uso: python -m tests.test_risultati
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from src import config, risultati as R

L, S = "ITA-Serie A", "2627"


def _riga(casa, fuori, gh, ga, fonte):
    return {"league": L, "season": S, "home_team": casa, "away_team": fuori,
            "date": pd.Timestamp("2026-09-20"), "FTHG": float(gh), "FTAG": float(ga),
            "FTR": "H" if gh > ga else "A" if gh < ga else "D", "fonte_risultato": fonte}


def _con_fonti(fd, us, fb):
    """Sostituisce le tre fonti per la durata di una chiamata."""
    originali = (R._da_football_data, R._da_understat, R._da_fbref)
    R._da_football_data = lambda: pd.DataFrame(fd, columns=R.COLONNE)
    R._da_understat = lambda: pd.DataFrame(us, columns=R.COLONNE)
    R._da_fbref = lambda: pd.DataFrame(fb, columns=R.COLONNE)
    return originali


def _ripristina(originali):
    R._da_football_data, R._da_understat, R._da_fbref = originali


def test_precedenza() -> None:
    orig = _con_fonti(
        fd=[_riga("Roma", "Inter", 0, 3, R.FOOTBALL_DATA)],          # ufficiale
        us=[_riga("Roma", "Inter", 2, 1, R.UNDERSTAT),               # campo: ignorato
            _riga("Milan", "Lecce", 3, 0, R.UNDERSTAT)],             # buco: riempito
        fb=[_riga("Milan", "Lecce", 9, 9, R.FBREF),                  # gia' coperto
            _riga("Parma", "Genoa", 2, 1, R.FBREF)],                 # solo FBref
    )
    try:
        out = R.risultati().set_index(["home_team", "away_team"])
    finally:
        _ripristina(orig)

    assert out.loc[("Roma", "Inter"), "fonte_risultato"] == R.FOOTBALL_DATA
    assert out.loc[("Roma", "Inter"), "FTHG"] == 0, "football-data deve vincere"
    assert out.loc[("Milan", "Lecce"), "fonte_risultato"] == R.UNDERSTAT
    assert out.loc[("Milan", "Lecce"), "FTHG"] == 3, "Understat prima di FBref"
    assert out.loc[("Parma", "Genoa"), "fonte_risultato"] == R.FBREF
    assert len(out) == 3, "una riga per partita"
    print("1-2. precedenza football-data > Understat > FBref        ok")


def test_partita_in_corso() -> None:
    """`_da_understat` scarta le partite con is_result falso."""
    import src.normalize as nz

    grezzo = pd.DataFrame([
        {"league": L, "season": S, "home_team": "Roma", "away_team": "Inter",
         "date": "2026-09-20", "home_goals": 1, "away_goals": 0, "is_result": False},
        {"league": L, "season": S, "home_team": "Milan", "away_team": "Lecce",
         "date": "2026-09-20", "home_goals": 3, "away_goals": 0, "is_result": True},
    ])
    orig = nz.load_raw
    nz.load_raw = lambda nome: grezzo.copy()
    try:
        out = R._da_understat()
    finally:
        nz.load_raw = orig
    squadre = set(out["home_team"])
    assert squadre == {"Milan"}, f"una partita in corso non e' un risultato: {squadre}"
    print("3.   partita in corso (1-0 al 30') scartata                ok")


def test_riconcilia() -> None:
    with tempfile.TemporaryDirectory() as cartella:
        tmp = Path(cartella)
        (tmp / "rounds").mkdir()
        pd.DataFrame([
            {**_riga("Sassuolo", "Pescara", 2, 1, R.UNDERSTAT)},   # campo
            {**_riga("Milan", "Lecce", 3, 0, R.UNDERSTAT)},        # coincide
        ]).to_csv(tmp / "rounds" / "round_2627_05.csv", index=False)

        orig_tr = config.TRACK_RECORD
        config.TRACK_RECORD = tmp
        orig = _con_fonti(
            fd=[_riga("Sassuolo", "Pescara", 0, 3, R.FOOTBALL_DATA),   # a tavolino
                _riga("Milan", "Lecce", 3, 0, R.FOOTBALL_DATA)],
            us=[], fb=[])
        try:
            avvisi = R.riconcilia()
        finally:
            _ripristina(orig)
            config.TRACK_RECORD = orig_tr

    assert len(avvisi) == 1, avvisi
    assert "Sassuolo-Pescara" in avvisi[0] and "0-3" in avvisi[0]
    print("4.   riconcilia segnala il 2-1 del campo contro lo 0-3 a tavolino   ok")


def main() -> None:
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and callable(fn):
            fn()
    print("\ntutti i controlli sulla catena dei risultati superati")


if __name__ == "__main__":
    main()
