"""
Le fondamenta dell'autenticazione: identificatori e configurazione.

Nessun database e nessuna rete: qui si verificano le due cose che sbagliate
non danno errore ma danno un sistema insicuro — un id prevedibile e una
configurazione di produzione lasciata ai valori di sviluppo.
"""

from __future__ import annotations

import time

import pytest
from backend.auth import ids
from backend.auth.settings import CHIAVE_DI_ESEMPIO, Impostazioni
from pydantic import SecretStr


@pytest.fixture(params=[True, False], ids=["nativa", "ripiego"])
def uuid7(request, monkeypatch):
    """Le due implementazioni devono rispettare lo stesso contratto."""
    if request.param and not ids._NATIVA:
        pytest.skip("questo Python non ha uuid.uuid7 nativa")
    monkeypatch.setattr(ids, "_NATIVA", request.param)
    return ids.uuid7


def test_uuid7_rispetta_la_rfc(uuid7) -> None:
    u = uuid7()
    assert u.version == 7, f"versione {u.version}, non 7"
    assert (u.int >> 62) & 0b11 == 0b10, "bit di variante non conformi a RFC 9562"


def test_uuid7_non_collide(uuid7) -> None:
    generati = {uuid7() for _ in range(5000)}
    assert len(generati) == 5000, "collisione su 5000 id"


def test_uuid7_cresce_nel_tempo(uuid7) -> None:
    """
    E' l'unica proprieta' per cui si sceglie v7 invece di v4.

    Se venisse a mancare, gli inserimenti tornerebbero a cadere a caso
    nell'indice e la tabella si frammenterebbe — senza nessun sintomo
    visibile finche' non e' grande.
    """
    primo = uuid7()
    time.sleep(0.01)
    assert str(uuid7()) > str(primo), "un id generato dopo non ordina dopo"


def test_la_configurazione_di_sviluppo_si_dichiara_insicura() -> None:
    """
    Il caso pericoloso e' che funzioni e basta.

    Una chiave di firma lasciata a quella di esempio non rompe niente: il
    sistema parte, la gente entra, e i token sono falsificabili da chiunque
    abbia letto il repository.
    """
    problemi = Impostazioni(_env_file=None).controlla_produzione()
    assert any("SECRET_KEY" in p for p in problemi)
    assert any("COOKIE_SECURE" in p for p in problemi)


def test_una_configurazione_buona_non_da_falsi_allarmi() -> None:
    buona = Impostazioni(
        _env_file=None,
        secret_key=SecretStr("x" * 64),
        cookie_secure=True,
        smtp_host="smtp.provider.example",
        smtp_security="starttls",
        public_url="https://ainaples.example",
    )
    assert buona.controlla_produzione() == []
    assert buona.mittente == "AI Naples <no-reply@ainaples.local>"


def test_smtp_in_chiaro_verso_un_host_remoto_e_un_problema() -> None:
    """Verso Mailpit in locale no; verso un provider vero si'."""
    locale = Impostazioni(_env_file=None, smtp_host="127.0.0.1", smtp_security="none")
    assert not any("chiaro" in p for p in locale.controlla_produzione())

    remoto = Impostazioni(_env_file=None, smtp_host="smtp.example.com", smtp_security="none")
    assert any("chiaro" in p for p in remoto.controlla_produzione())


def test_smtp_security_rifiuta_valori_ignoti() -> None:
    with pytest.raises(ValueError, match="smtp_security"):
        Impostazioni(_env_file=None, smtp_security="forse")


def test_la_chiave_di_esempio_e_davvero_quella_del_file() -> None:
    """
    Se `.env.example` cambia e questa costante no, il controllo smette di
    riconoscere la chiave di esempio e non protegge piu' niente.
    """
    from pathlib import Path
    esempio = Path(__file__).resolve().parent.parent / ".env.example"
    assert CHIAVE_DI_ESEMPIO in esempio.read_text(encoding="utf-8"), (
        "CHIAVE_DI_ESEMPIO non compare piu' in .env.example: il controllo "
        "sulla chiave non riconoscerebbe piu' il valore di default"
    )
