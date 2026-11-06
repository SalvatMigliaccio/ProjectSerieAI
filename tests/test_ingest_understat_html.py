"""
Una risposta Understat che non e' JSON e' un errore di RETE, non un bug.

Il 6 novembre 2026 Understat ha servito la sua pagina d'errore HTML al posto
del JSON. soccerdata l'ha scritta in cache e poi `json.load` e' fallito con
`JSONDecodeError`; `rounds.passo` non la riconosceva come errore di rete e
`predict_round` si fermava a due ore dal primo fischio.

Si verificano tre cose: l'errore diventa `ConnectionError`, la cache viene
ripulita SOLO dei file non JSON, e un errore che non e' di decodifica passa
com'era — la traduzione non deve nascondere i bug veri.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from goalmodel.ingest import risposta_understat, scarta_cache_non_json
from goalmodel.prediction import rounds

HTML = "<!DOCTYPE html>\n<html><head><title>Understat.com</title></head></html>"


def _cache(tmp_path: Path) -> Path:
    (tmp_path / "league_1_season_2026.json").write_text('{"dates": []}', encoding="utf-8")
    (tmp_path / "match_1.json").write_text('  [1, 2]', encoding="utf-8")
    (tmp_path / "league_2_season_2026.json").write_text(HTML, encoding="utf-8")
    (tmp_path / "match_2.json").write_text("   \n<html>", encoding="utf-8")
    (tmp_path / "match_3.json").write_bytes(b"")
    (tmp_path / "note.txt").write_text("<non e' json ma non e' .json>", encoding="utf-8")
    return tmp_path


def test_scarta_solo_i_file_non_json(tmp_path: Path) -> None:
    cartella = _cache(tmp_path)
    tolti = {f.name for f in scarta_cache_non_json(cartella)}
    assert tolti == {"league_2_season_2026.json", "match_2.json", "match_3.json"}
    rimasti = {f.name for f in cartella.iterdir()}
    assert rimasti == {"league_1_season_2026.json", "match_1.json", "note.txt"}


def test_json_illeggibile_diventa_errore_di_rete(tmp_path: Path) -> None:
    us = SimpleNamespace(data_dir=_cache(tmp_path))
    with pytest.raises(ConnectionError, match="non e' JSON") as info, risposta_understat(us):
        json.loads(HTML)
    assert isinstance(info.value.__cause__, json.JSONDecodeError)
    assert not (tmp_path / "league_2_season_2026.json").exists()
    assert (tmp_path / "league_1_season_2026.json").exists()


def test_altri_errori_non_vengono_tradotti(tmp_path: Path) -> None:
    us = SimpleNamespace(data_dir=_cache(tmp_path))
    with pytest.raises(TypeError), risposta_understat(us):
        raise TypeError("bug nostro")
    # Nessuna pulizia: la cache si tocca solo quando la risposta era illeggibile.
    assert (tmp_path / "league_2_season_2026.json").exists()


def test_passo_non_fatale_prosegue(tmp_path: Path) -> None:
    """La catena completa: dentro `rounds.passo` il comando va avanti."""
    us = SimpleNamespace(data_dir=_cache(tmp_path))

    def stage() -> None:
        with risposta_understat(us):
            json.loads(HTML)

    assert rounds.passo("1b", "xG e PPDA Understat", stage, fatale=False) is False
