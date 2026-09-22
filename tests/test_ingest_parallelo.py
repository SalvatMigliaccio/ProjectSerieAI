"""
Lo scheduler dell'ingestion rispetta i limiti di frequenza per host.

PERCHE' ESISTE. Il parallelismo qui non serve a essere veloci a ogni costo:
serve a far lavorare insieme host DIVERSI senza mai aumentare le richieste
viste da uno stesso host. Se quella garanzia salta, non succede niente di
visibile — il download va anzi piu' in fretta — finche' FBref o WhoScored non
rispondono 429 o bloccano l'IP, giorni dopo e senza spiegazioni.

E' esattamente un difetto che si auto-premia, come il leakage: va verificato
da fuori invece di fidarsi della lettura del codice.

Niente rete: gli stage sono finti e si limitano a dormire e a registrare
quando sono entrati e usciti.

    python -m tests.test_ingest_parallelo
"""

from __future__ import annotations

import threading
import time

from goalmodel import ingest


class Spia:
    """Registra gli intervalli di esecuzione di ogni stage finto."""

    def __init__(self) -> None:
        self.intervalli: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def stage(self, nome: str, durata: float = 0.10):
        def fn():
            inizio = time.monotonic()
            time.sleep(durata)
            with self._lock:
                self.intervalli[nome] = (inizio, time.monotonic())
        return fn

    def sovrapposti(self, a: str, b: str) -> bool:
        (ia, fa), (ib, fb) = self.intervalli[a], self.intervalli[b]
        return ia < fb and ib < fa


def _prepara(monkey_stages: dict, monkey_host: dict):
    """Sostituisce STAGES e HOST_DI_STAGE, restituendo i valori originali."""
    orig = (dict(ingest.STAGES), dict(ingest.HOST_DI_STAGE))
    ingest.STAGES.clear()
    ingest.STAGES.update(monkey_stages)
    ingest.HOST_DI_STAGE.clear()
    ingest.HOST_DI_STAGE.update(monkey_host)
    return orig


def _ripristina(orig) -> None:
    stages, host = orig
    ingest.STAGES.clear()
    ingest.STAGES.update(stages)
    ingest.HOST_DI_STAGE.clear()
    ingest.HOST_DI_STAGE.update(host)


def test_stesso_host_mai_in_parallelo() -> None:
    """Due stage sullo stesso host devono restare in fila."""
    spia = Spia()
    orig = _prepara(
        {"a": spia.stage("a"), "b": spia.stage("b")},
        {"a": "fbref", "b": "fbref"},
    )
    try:
        esiti = ingest.esegui(["a", "b"], parallelo=True)
    finally:
        _ripristina(orig)

    assert esiti == {"a": None, "b": None}
    assert not spia.sovrapposti("a", "b"), (
        "due stage su FBref si sono sovrapposti: il limite di 7 s per "
        "richiesta verrebbe visto dal server come 2 richieste ogni 7 s"
    )
    print("1. stesso host: esecuzione seriale                   ok")


def test_host_diversi_in_parallelo() -> None:
    """Host diversi devono invece sovrapporsi, o il parallelismo non c'e'."""
    spia = Spia()
    orig = _prepara(
        {"a": spia.stage("a", 0.25), "b": spia.stage("b", 0.25)},
        {"a": "fbref", "b": "understat"},
    )
    try:
        ingest.esegui(["a", "b"], parallelo=True)
    finally:
        _ripristina(orig)

    assert spia.sovrapposti("a", "b"), (
        "due host distinti NON si sono sovrapposti: il parallelismo non sta "
        "facendo niente"
    )
    print("2. host diversi: esecuzione sovrapposta              ok")


def test_no_parallel_resta_seriale() -> None:
    """Con --no-parallel niente si sovrappone, qualunque sia l'host."""
    spia = Spia()
    orig = _prepara(
        {"a": spia.stage("a"), "b": spia.stage("b")},
        {"a": "fbref", "b": "understat"},
    )
    try:
        ingest.esegui(["a", "b"], parallelo=False)
    finally:
        _ripristina(orig)

    assert not spia.sovrapposti("a", "b"), "--no-parallel ha comunque parallelizzato"
    print("3. --no-parallel: esecuzione seriale                 ok")


def test_uno_stage_rotto_non_ferma_gli_altri() -> None:
    """Una fonte giu' non deve buttare via il lavoro delle altre."""
    spia = Spia()

    def esplode():
        raise ConnectionError("503 dal sito")

    orig = _prepara(
        {"rotto": esplode, "sano": spia.stage("sano")},
        {"rotto": "football-data", "sano": "understat"},
    )
    try:
        esiti = ingest.esegui(["rotto", "sano"], parallelo=True)
    finally:
        _ripristina(orig)

    assert isinstance(esiti["rotto"], ConnectionError)
    assert esiti["sano"] is None
    assert "sano" in spia.intervalli, "lo stage sano non e' stato eseguito"
    print("4. uno stage fallito, gli altri proseguono           ok")


def test_ogni_stage_ha_un_host_dichiarato() -> None:
    """
    Uno stage senza host finirebbe su un semaforo tutto suo e girerebbe in
    parallelo a chiunque, limite di frequenza compreso. Si controlla qui,
    perche' aggiungere uno stage e scordare la mappa non da' nessun errore.
    """
    senza = sorted(set(ingest.STAGES) - set(ingest.HOST_DI_STAGE))
    assert not senza, f"stage senza host dichiarato in HOST_DI_STAGE: {senza}"

    ignoti = sorted(set(ingest.HOST_DI_STAGE.values()) - set(ingest.LIMITE_PER_HOST))
    assert not ignoti, f"host senza limite dichiarato in LIMITE_PER_HOST: {ignoti}"
    print("5. ogni stage ha host e limite dichiarati            ok")


def main() -> None:
    test_stesso_host_mai_in_parallelo()
    test_host_diversi_in_parallelo()
    test_no_parallel_resta_seriale()
    test_uno_stage_rotto_non_ferma_gli_altri()
    test_ogni_stage_ha_un_host_dichiarato()
    print("\ntutti i controlli sullo scheduler superati")


if __name__ == "__main__":
    main()
