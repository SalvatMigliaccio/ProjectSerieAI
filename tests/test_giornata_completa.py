"""
Un comando per fase: `predict-round` e `close-round` aggiornano anche
marcatori e M5.

Due cose possono rompersi senza nessun sintomo, ed e' gia' successo a
entrambe:
- i tiri della stagione in corso restavano quelli del primo download, perche'
  lo stage saltava le coppie (lega, stagione) gia' nel file. Il 27 ottobre 2026
  i marcatori della dashboard erano fermi alla giornata 7;
- M5 era agganciato solo dallo script dello scheduler, e la riga che lo
  lanciava non e' stata eseguita per settimane (manca `call`, 23 ottobre).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest

from goalmodel import config, ingest
from goalmodel.prediction import close_round, predict_round, rounds

PREDICTION = Path(predict_round.__file__).resolve().parent


# --- lo stage dei tiri -------------------------------------------------------------

def _tiri(lega: str, stagione: str, n: int, marca: str) -> pd.DataFrame:
    return pd.DataFrame({"league": [lega] * n, "season": [stagione] * n,
                         "player": [marca] * n, "xg": [0.1] * n})


class _UnderstatFinto:
    """Risponde con tiri marcati per (lega, stagione); registra cosa gli si chiede."""

    chieste: ClassVar[list[tuple[str, str]]] = []
    rotte: ClassVar[set[str]] = set()

    def __init__(self, leagues, seasons):
        self.lega, self.stagione = leagues[0], str(seasons[0])
        _UnderstatFinto.chieste.append((self.lega, self.stagione))

    def read_shot_events(self) -> pd.DataFrame:
        if self.stagione in _UnderstatFinto.rotte:
            raise ConnectionError("understat giu'")
        return _tiri(self.lega, self.stagione, 5, "nuovo").drop(columns=["league", "season"])


@pytest.fixture
def understat(monkeypatch, tmp_path):
    _UnderstatFinto.chieste = []
    _UnderstatFinto.rotte = set()
    monkeypatch.setattr(ingest, "RAW", tmp_path)
    monkeypatch.setattr(ingest.sd, "Understat", _UnderstatFinto)
    in_corso = ingest.season_from_date(pd.Timestamp.now())
    pd.concat([_tiri("ITA-Serie A", "1920", 3, "vecchio"),
               _tiri("ITA-Serie A", in_corso, 2, "vecchio")]).to_parquet(
        tmp_path / "understat_shots.parquet", index=False)
    return tmp_path, in_corso


def test_la_stagione_in_corso_si_riscarica_e_sostituisce_le_righe_vecchie(understat) -> None:
    cartella, in_corso = understat
    ingest.ingest_understat_shots(seasons=["1920", in_corso], leagues=["ITA-Serie A"])

    assert _UnderstatFinto.chieste == [("ITA-Serie A", in_corso)], \
        "una stagione conclusa gia' nel file non si riscarica; quella in corso si'"
    out = pd.read_parquet(cartella / "understat_shots.parquet")
    corrente = out[out["season"].astype(str) == in_corso]
    assert len(corrente) == 5 and set(corrente["player"]) == {"nuovo"}, \
        "le righe vecchie della stagione in corso vanno sostituite, non sommate"
    assert len(out[out["season"].astype(str) == "1920"]) == 3


def test_un_download_fallito_non_cancella_i_tiri_che_cerano(understat) -> None:
    cartella, in_corso = understat
    _UnderstatFinto.rotte = {in_corso}
    ingest.ingest_understat_shots(seasons=[in_corso], leagues=["ITA-Serie A"])

    out = pd.read_parquet(cartella / "understat_shots.parquet")
    assert len(out[out["season"].astype(str) == in_corso]) == 2, \
        "senza un download riuscito i tiri della stagione restano quelli di prima"


# --- i due comandi -----------------------------------------------------------------

@pytest.mark.parametrize("modulo", [predict_round, close_round])
def test_entrambi_i_comandi_aggiornano_i_tiri(modulo) -> None:
    assert "shots" in modulo.STAGE, \
        f"{modulo.__name__} non aggiorna i marcatori: la dashboard resta indietro"


def test_lo_stage_dei_tiri_chiede_solo_la_stagione_in_corso_della_produzione(monkeypatch) -> None:
    chiamate: list[dict] = []
    monkeypatch.setattr(ingest, "ingest_understat_shots", lambda **kw: chiamate.append(kw))
    assert rounds.aggiorna_dati(("shots",), skip_ingest=False) == []
    assert chiamate == [{"seasons": [ingest.season_from_date(pd.Timestamp.now())],
                         "leagues": list(config.LEAGUES_PRODUZIONE)}], \
        "dentro un comando di giornata, cinque leghe e tredici stagioni sono ore"


def test_m5_parte_in_un_processo_suo_con_python_del_venv(monkeypatch) -> None:
    visti: list[list[str]] = []

    class _Esito:
        returncode = 0

    monkeypatch.setattr(rounds.subprocess, "run",
                        lambda cmd, check: visti.append(cmd) or _Esito())
    assert rounds.lancia_m5(["--matchday", "9"]) == 0
    assert visti == [[sys.executable, "-m", "goalmodel.experiments.predici_gbm",
                      "--matchday", "9"]]


def test_un_m5_che_non_parte_non_ferma_il_comando(monkeypatch) -> None:
    def esplode(cmd, check):
        raise OSError("python sparito")

    monkeypatch.setattr(rounds.subprocess, "run", esplode)
    assert rounds.lancia_m5(["--verifica"]) == -1


def test_la_produzione_non_importa_gli_esperimenti() -> None:
    """
    M5 si lancia per NOME, e la ragione non e' solo la regola dei livelli.

    `predici_gbm` installa `proteggi_produzione()`, che sostituisce `to_csv` e
    `to_parquet` nel processo: importato da `predict_round` resterebbe accesa
    sul registro. Questo legge gli import veri dei moduli di produzione.
    """
    for file in PREDICTION.glob("*.py"):
        albero = ast.parse(file.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            nomi = ([a.name for a in nodo.names] if isinstance(nodo, ast.Import)
                    else [nodo.module or ""] if isinstance(nodo, ast.ImportFrom) else [])
            for nome in nomi:
                assert "experiments" not in nome, \
                    f"{file.name} importa {nome}: gli esperimenti si lanciano, non si importano"
