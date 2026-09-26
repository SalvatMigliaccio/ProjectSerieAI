"""
Quello che torna dalla rete e' ostile finche' non e' verificato — audit B5.

PERCHE' ESISTE. `ingest._leggi_csv` legge con `encoding="latin-1"`, che non
fallisce **mai** sulla decodifica: qualunque sequenza di byte diventa una
stringa valida. Una pagina di errore HTML o un captcha servito al posto del
file entra quindi in `read_csv` come dati buoni, e arriva fino al controllo
delle colonne — che e' la difesa giusta, ma e' l'ultima, non la prima.

Qui si finge il server: nessuna rete, nessun file scaricato.

    python -m tests.test_confine_rete
"""

import io
import urllib.request

import pytest

from goalmodel import ingest


class FintaRisposta(io.BytesIO):
    """Il minimo che `urlopen` restituisce: un corpo e un content-type."""

    def __init__(self, corpo: bytes, tipo: str) -> None:
        super().__init__(corpo)
        self._tipo = tipo
        self.headers = self

    def get_content_type(self) -> str:
        return self._tipo

    def __enter__(self) -> "FintaRisposta":
        return self

    def __exit__(self, *_) -> bool:
        return False


def _con_risposta(monkeypatch, corpo: bytes, tipo: str) -> None:
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: FintaRisposta(corpo, tipo))


def test_una_pagina_html_non_passa_per_csv(monkeypatch) -> None:
    _con_risposta(monkeypatch, b"<html><body>captcha</body></html>", "text/html")
    with pytest.raises(ConnectionError, match="content-type"):
        ingest._leggi_csv("https://esempio.invalid/dati.csv")


def test_una_risposta_enorme_non_finisce_in_memoria(monkeypatch) -> None:
    _con_risposta(monkeypatch, b"x" * (ingest.MAX_BYTES_CSV + 10), "text/csv")
    with pytest.raises(ConnectionError, match="MB"):
        ingest._leggi_csv("https://esempio.invalid/dati.csv")


def test_un_csv_vero_passa(monkeypatch) -> None:
    """Il confine non deve fermare anche il caso buono."""
    _con_risposta(monkeypatch, b"Div,HomeTeam,AwayTeam\nI1,Napoli,Inter\n", "text/csv")
    df = ingest._leggi_csv("https://esempio.invalid/dati.csv")
    assert list(df.columns) == ["Div", "HomeTeam", "AwayTeam"]
    assert df.iloc[0]["HomeTeam"] == "Napoli"


def test_un_percorso_locale_non_passa_dalla_rete(tmp_path, monkeypatch) -> None:
    """
    Un file su disco si legge, e senza toccare `urlopen`.

    Conta perche' `urlopen` apre anche `file://`: se il ramo locale sparisse,
    un percorso che arriva da configurazione verrebbe aperto come URL.
    """
    def esplodi(*a, **k):
        raise AssertionError("un percorso locale e' passato dalla rete")

    monkeypatch.setattr(urllib.request, "urlopen", esplodi)
    csv = tmp_path / "dati.csv"
    csv.write_text("Div,HomeTeam\nI1,Napoli\n", encoding="latin-1")
    assert ingest._leggi_csv(csv).iloc[0]["HomeTeam"] == "Napoli"


def main() -> None:
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
