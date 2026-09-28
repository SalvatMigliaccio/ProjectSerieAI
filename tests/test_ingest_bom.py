"""
Il BOM di football-data, che costa una stagione intera senza dire niente.

soccerdata sceglie l'encoding dalla STAGIONE: `UTF-8-SIG` da 2425 in poi,
`latin-1` prima. `E0_2122.csv` ha il BOM pur essendo del 2021/22, quindi in
latin-1 la prima colonna si chiama `ï»¿Div` invece di `Div` — e siccome
soccerdata rinomina `Div` in `league`, quelle 380 partite restano senza lega.
Una chiave nulla sparisce da ogni merge senza sollevare niente.
"""

from __future__ import annotations

import pandas as pd

from goalmodel.ingest import BOM, _togli_bom

INTESTAZIONE = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
RIGA = "E0,13/08/2021,Brentford,Arsenal,2,0\n"


def test_il_bom_rinomina_la_colonna_della_lega(tmp_path) -> None:
    """Il difetto, prima della correzione: e' la colonna che cambia nome."""
    csv = tmp_path / "E0_2122.csv"
    csv.write_bytes(BOM + (INTESTAZIONE + RIGA).encode("utf-8"))

    letto = pd.read_csv(csv, encoding="latin-1")
    assert "Div" not in letto.columns, \
        "senza BOM questo test non sta provando niente"
    assert letto.columns[0].endswith("Div")


def test_togliere_il_bom_restituisce_la_colonna(tmp_path) -> None:
    csv = tmp_path / "E0_2122.csv"
    csv.write_bytes(BOM + (INTESTAZIONE + RIGA).encode("utf-8"))

    assert _togli_bom(tmp_path) == 1
    assert pd.read_csv(csv, encoding="latin-1").columns[0] == "Div"


def test_e_idempotente(tmp_path) -> None:
    """Un file gia' pulito non viene riscritto: rilanciare l'ingest e' normale."""
    csv = tmp_path / "I1_2526.csv"
    csv.write_bytes((INTESTAZIONE + RIGA).encode("utf-8"))
    prima = csv.read_bytes()

    assert _togli_bom(tmp_path) == 0
    assert csv.read_bytes() == prima


def test_non_tocca_un_bom_a_meta_file(tmp_path) -> None:
    """
    Solo l'inizio del file. Quei tre byte in mezzo a una riga sono dati —
    improbabili, ma toglierli silenziosamente sarebbe corruzione.
    """
    csv = tmp_path / "F1_1819.csv"
    corpo = (INTESTAZIONE + "E0,13/08/2021,A,B,1,").encode("utf-8") + BOM + b"0\n"
    csv.write_bytes(corpo)

    assert _togli_bom(tmp_path) == 0
    assert csv.read_bytes() == corpo
