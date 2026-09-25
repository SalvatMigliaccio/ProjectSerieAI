"""
Il registro dei blocchi di feature: un elenco solo, davvero.

PERCHE' ESISTE (audit B2). L'elenco dei blocchi viveva in due posti che non si
parlavano: `evaluate.load_dataset` ne caricava quattro, `rounds.ricostruisci`
ne ricostruiva tre. Mancava `players`, e il risultato era un parquet che
invecchiava a ogni giornata mentre continuava a essere dato in pasto ai
modelli — con merge `how="left"`, quindi NaN sulle righe nuove, che LightGBM
accetta senza fiatare.

Nessun errore, nessun avviso. Per questo la garanzia va verificata da fuori:
non che il codice sia scritto bene oggi, ma che CHI CARICA e CHI RICOSTRUISCE
scorrano la stessa lista.

    python -m tests.test_registry
"""

from __future__ import annotations

import pandas as pd
import pytest

from goalmodel import config
from goalmodel.features import registry


def test_stessa_lista_per_costruire_e_per_caricare() -> None:
    """
    Il cuore di B2: `ricostruisci` deve costruire ESATTAMENTE i blocchi che
    `load_dataset` carica. Si verifica eseguendo davvero `ricostruisci` con i
    costruttori sostituiti da registratori.
    """
    from goalmodel import normalize
    from goalmodel.prediction import rounds

    costruiti: list[str] = []

    def registratore(nome):
        def fn(*a, **k):
            costruiti.append(nome)
            return pd.DataFrame()
        return fn

    finti = tuple(
        registry.Blocco(b.nome, registratore(b.nome), b.comando,
                        b.obbligatorio, b.descrizione)
        for b in registry.BLOCCHI
    )
    orig_blocchi = registry.BLOCCHI
    orig_build = normalize.cmd_build
    registry.BLOCCHI = finti
    normalize.cmd_build = lambda *a, **k: None
    try:
        rounds.ricostruisci()
    finally:
        registry.BLOCCHI = orig_blocchi
        normalize.cmd_build = orig_build

    assert set(costruiti) == {b.nome for b in orig_blocchi}, (
        f"ricostruisci costruisce {sorted(costruiti)} ma il registro dichiara "
        f"{sorted(b.nome for b in orig_blocchi)}: e' di nuovo B2"
    )
    print("1. ricostruisci copre tutti i blocchi del registro   ok")


def test_blocco_obbligatorio_assente_solleva() -> None:
    """Senza forma o mercato non c'e' modello: l'assenza dev'essere fatale."""
    finto = registry.Blocco("features_inesistente", lambda *a, **k: None,
                            "goalmodel niente", obbligatorio=True,
                            descrizione="blocco finto")
    orig = registry.BLOCCHI
    registry.BLOCCHI = (finto,)
    try:
        with pytest.raises(FileNotFoundError, match="obbligatorio"):
            registry.unisci(pd.DataFrame(columns=config.JOIN_KEYS))
    finally:
        registry.BLOCCHI = orig
    print("2. blocco obbligatorio assente: solleva             ok")


def test_blocco_facoltativo_assente_viene_saltato() -> None:
    """
    Un blocco facoltativo assente e' uno stato legittimo — le coppe e le ~24
    ore di scraping sono ingestion opzionali — e va segnalato, non fatale.
    """
    finto = registry.Blocco("features_inesistente", lambda *a, **k: None,
                            "goalmodel niente", obbligatorio=False,
                            descrizione="blocco finto")
    base = pd.DataFrame({k: ["x"] for k in config.JOIN_KEYS})
    orig = registry.BLOCCHI
    registry.BLOCCHI = (finto,)
    try:
        out = registry.unisci(base)
    finally:
        registry.BLOCCHI = orig
    assert len(out) == 1, "il blocco saltato non deve cambiare le righe"
    print("3. blocco facoltativo assente: saltato con avviso   ok")


def test_ogni_blocco_ha_un_comando_che_esiste() -> None:
    """
    Il comando suggerito quando un blocco manca dev'essere un comando VERO.
    Un messaggio che nomina un comando inesistente manda a cercare nel posto
    sbagliato, ed e' gia' successo dopo il riordino dei moduli.
    """
    from goalmodel.cli import COMANDI

    for b in registry.BLOCCHI:
        assert b.comando.startswith("goalmodel "), b.comando
        sotto = b.comando.split()[1]
        assert sotto in COMANDI, (
            f"il blocco '{b.nome}' suggerisce '{b.comando}', ma "
            f"'{sotto}' non e' un comando del CLI"
        )
    print("4. ogni blocco suggerisce un comando esistente      ok")


def main() -> None:
    test_stessa_lista_per_costruire_e_per_caricare()
    test_blocco_obbligatorio_assente_solleva()
    test_blocco_facoltativo_assente_viene_saltato()
    test_ogni_blocco_ha_un_comando_che_esiste()
    print("\ntutti i controlli sul registro dei blocchi superati")


if __name__ == "__main__":
    main()
