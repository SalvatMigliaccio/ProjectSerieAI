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


def test_host_libero_non_aspetta_un_worker() -> None:
    """
    Tre stage su due host: quello con l'host libero deve partire SUBITO.

    E' il caso che ha gia' sbagliato una volta. Con `max_workers` pari al
    numero di host, il secondo stage dello stesso host si prende un worker e
    ci resta BLOCCATO sul semaforo, e il terzo — il cui host e' libero —
    aspetta in coda che si liberi un posto. Un worker fermo occupa comunque
    il suo posto nel pool.

    Nel caso reale significava far partire le quindici ore di WhoScored dopo
    le nove di FBref invece che insieme: ventiquattro ore al posto di quindici.
    """
    spia = Spia()
    orig = _prepara(
        {"fb1": spia.stage("fb1", 0.30),
         "fb2": spia.stage("fb2", 0.05),
         "ws": spia.stage("ws", 0.05)},
        {"fb1": "fbref", "fb2": "fbref", "ws": "whoscored"},
    )
    try:
        ingest.esegui(["fb1", "fb2", "ws"], parallelo=True)
    finally:
        _ripristina(orig)

    assert spia.sovrapposti("fb1", "ws"), (
        "lo stage su host libero ha aspettato: un worker bloccato su un "
        "semaforo sta occupando il suo posto nel pool"
    )
    assert not spia.sovrapposti("fb1", "fb2"), "due stage FBref si sono sovrapposti"
    print("3. host libero: parte subito, non aspetta un worker   ok")


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
    print("4. --no-parallel: esecuzione seriale                 ok")


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
    print("5. uno stage fallito, gli altri proseguono           ok")


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
    print("6. ogni stage ha host e limite dichiarati            ok")


def main() -> None:
    test_stesso_host_mai_in_parallelo()
    test_host_diversi_in_parallelo()
    test_host_libero_non_aspetta_un_worker()
    test_no_parallel_resta_seriale()
    test_uno_stage_rotto_non_ferma_gli_altri()
    test_ogni_stage_ha_un_host_dichiarato()
    test_coppa_sopravvive_a_una_stagione_mancante()
    print("\ntutti i controlli sullo scheduler superati")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Isolamento degli errori nell'ingestion delle coppe
# ---------------------------------------------------------------------------

def test_coppa_sopravvive_a_una_stagione_mancante() -> None:
    """
    Una stagione in cui la competizione non esisteva non deve portarsi via
    le stagioni in cui esisteva.

    E' GIA' SUCCESSO. La Conference League non esiste prima del 2021/22 e
    soccerdata solleva `KeyError: '1415'` invece di restituire vuoto: con il
    `try` messo per competizione invece che per stagione, l'eccezione si
    portava via l'intera coppa. Il file conteneva 4140 partite di sola
    Champions ed Europa, zero di Conference, e quel 4140 era finito nella
    documentazione come se le coppe fossero tre.
    """
    import pandas as pd

    from goalmodel import ingest

    VIVE = {"2122", "2223", "2324"}

    class FBrefFinto:
        def __init__(self, leagues, seasons):
            self.stagione = seasons[0]

        def read_schedule(self):
            if self.stagione not in VIVE:
                raise KeyError(self.stagione)      # come fa soccerdata
            return pd.DataFrame({"game": [f"g{self.stagione}"]})

    orig_fb, orig_seasons = ingest.sd.FBref, ingest.SEASONS
    orig_registra = ingest.registra_coppe
    orig_save = pd.DataFrame.to_parquet
    ingest.sd.FBref = FBrefFinto
    ingest.SEASONS = ["1415", "1516", "2122", "2223", "2324"]
    ingest.registra_coppe = lambda: ["UEFA-Conference League"]
    pd.DataFrame.to_parquet = lambda self, *a, **k: None
    try:
        out = ingest.ingest_cups()
    finally:
        ingest.sd.FBref, ingest.SEASONS = orig_fb, orig_seasons
        ingest.registra_coppe = orig_registra
        pd.DataFrame.to_parquet = orig_save

    assert len(out) == len(VIVE), (
        f"{len(out)} stagioni raccolte invece di {len(VIVE)}: una stagione "
        f"mancante si e' portata via anche quelle che c'erano"
    )
    print("7. coppe: una stagione assente non ne uccide altre   ok")
