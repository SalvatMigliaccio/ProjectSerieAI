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

import tempfile
import threading
import time
from pathlib import Path

import pytest

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
    orig_registra, orig_raw = ingest.registra_coppe, ingest.RAW
    orig_save = pd.DataFrame.to_parquet
    ingest.sd.FBref = FBrefFinto
    ingest.SEASONS = ["1415", "1516", "2122", "2223", "2324"]
    ingest.registra_coppe = lambda: ["UEFA-Conference League"]
    # RAW dirottata: senza, `ingest_cups` leggerebbe il file vero per capire
    # cosa aveva gia', e il test dipenderebbe dai dati sul disco.
    with tempfile.TemporaryDirectory() as tmp:
        ingest.RAW = Path(tmp)
        pd.DataFrame.to_parquet = lambda self, *a, **k: None
        try:
            out = ingest.ingest_cups()
        finally:
            ingest.sd.FBref, ingest.SEASONS = orig_fb, orig_seasons
            ingest.registra_coppe, ingest.RAW = orig_registra, orig_raw
            pd.DataFrame.to_parquet = orig_save

    assert len(out) == len(VIVE), (
        f"{len(out)} stagioni raccolte invece di {len(VIVE)}: una stagione "
        f"mancante si e' portata via anche quelle che c'erano"
    )
    print("7. coppe: una stagione assente non ne uccide altre   ok")


def test_un_giro_parziale_non_cancella_le_coppe_gia_prese() -> None:
    """
    Se una stagione non arriva, quella gia' nel file resta — audit del
    22 settembre 2026.

    E' GIA' SUCCESSO, LO STESSO GIORNO IN CUI LA CONFERENCE E' ARRIVATA.
    Lo stage scriveva `to_parquet` con il solo risultato del giro corrente:
    quel giro ha finalmente preso la Conference League (837 partite) e nello
    stesso momento ha perso la Champions 2026/27 per un CAPTCHA di FBref. Il
    file restava perfettamente plausibile — 4833 righe, tre coppe — e la
    perdita si vedeva solo contandole una per una.
    """
    import pandas as pd

    from goalmodel import ingest

    PRESE_ORA = {"2122", "2223"}

    class FBrefACaptcha:
        def __init__(self, leagues, seasons):
            self.stagione = seasons[0]

        def read_schedule(self):
            if self.stagione not in PRESE_ORA:
                raise TimeoutError("CAPTCHA")     # non "non esisteva"
            return pd.DataFrame({"game": [f"g{self.stagione}"],
                                 "league": ["UEFA-Conference League"],
                                 "season": [self.stagione]})

    orig_fb, orig_seasons = ingest.sd.FBref, ingest.SEASONS
    orig_registra, orig_raw = ingest.registra_coppe, ingest.RAW
    ingest.sd.FBref = FBrefACaptcha
    ingest.SEASONS = ["2122", "2223", "2324"]
    ingest.registra_coppe = lambda: ["UEFA-Conference League"]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ingest.RAW = Path(tmp)
            # Il file "di ieri": ha anche la 2324, che oggi non arrivera'.
            gia = pd.DataFrame({"game": ["g2122", "g2223", "g2324"],
                                "league": ["UEFA-Conference League"] * 3,
                                "season": ["2122", "2223", "2324"]})
            gia.to_parquet(Path(tmp) / "fbref_cups_schedule.parquet", index=False)

            out = ingest.ingest_cups()
    finally:
        ingest.sd.FBref, ingest.SEASONS = orig_fb, orig_seasons
        ingest.registra_coppe, ingest.RAW = orig_registra, orig_raw

    stagioni = set(out["season"].astype(str))
    assert stagioni == {"2122", "2223", "2324"}, (
        f"stagioni nel file: {sorted(stagioni)}. La 2324 non e' arrivata in "
        f"questo giro, ma c'era: un giro parziale non deve cancellarla."
    )
    print("8. coppe: un giro parziale non cancella lo storico   ok")


def test_missing_salva_il_calendario_che_serve_ai_giocatori() -> None:
    """
    Lo stage `missing` deve produrre ANCHE il modo di agganciare cio' che
    scarica.

    PERCHE' ESISTE. `features/players.py` traduce il `game_id` di WhoScored
    nella quadrupla leggendo `data/raw/whoscored/schedule_*.parquet`: il nome
    della partita non basta, perche' contiene trattini anche dentro i nomi
    squadra ("Inter Milan-AC Milan") e il parsing si romperebbe in silenzio.

    Quei file non li scriveva nessuno. Lo stage leggeva il calendario, lo
    teneva in memoria per ricavarne gli id e lo buttava; `carica_assenze`
    falliva su una cartella inesistente. Il blocco giocatori risultava
    costruibile solo finche' quei parquet erano avanzati da una versione
    precedente del codice — e `data/` non e' versionata, quindi in un clone
    pulito, o dopo una pulizia, ore di scraping non sarebbero bastate.
    """
    import time as _time

    import pandas as pd

    from goalmodel import ingest

    # Le quattro colonne che `players.carica_assenze` legge da questi file.
    RICHIESTE = ["season", "game_id", "home_team", "away_team"]

    class WhoScoredFinto:
        def __init__(self, leagues, seasons, headless=True):
            self.stagione = seasons[0]

        def read_schedule(self):
            return pd.DataFrame({
                "season": [self.stagione] * 2,
                "game_id": [111, 222],
                "home_team": ["Inter Milan", "AC Milan"],
                "away_team": ["AC Milan", "Inter Milan"],
            }).set_index("season")      # come soccerdata: la chiave e' nell'indice

        def read_missing_players(self, match_id):
            return pd.DataFrame({"game_id": match_id, "team": ["Inter Milan"] * len(match_id),
                                 "player": ["Tizio"] * len(match_id),
                                 "reason": ["injured"] * len(match_id)})

    orig = (ingest.sd.WhoScored, ingest.SEASONS, ingest.RAW,
            ingest.applica_locale_whoscored, _time.sleep)
    ingest.sd.WhoScored = WhoScoredFinto
    ingest.SEASONS = ["2324"]
    ingest.applica_locale_whoscored = lambda: None
    _time.sleep = lambda _s: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ingest.RAW = Path(tmp)
            ingest.ingest_missing()

            atteso = Path(tmp) / "whoscored" / "schedule_2324.parquet"
            assert atteso.exists(), (
                "lo stage non ha salvato il calendario: senza, il game_id non "
                "si traduce nella quadrupla e il blocco giocatori non si "
                "costruisce, per quante ore si scarichi"
            )
            cal = pd.read_parquet(atteso)
            mancanti = [c for c in RICHIESTE if c not in cal.columns]
            assert not mancanti, f"al calendario salvato mancano {mancanti}"
    finally:
        (ingest.sd.WhoScored, ingest.SEASONS, ingest.RAW,
         ingest.applica_locale_whoscored, _time.sleep) = orig

    print("9. missing: salva il calendario che serve ai giocatori   ok")


def test_ogni_stage_invocabile_e_anche_eseguibile() -> None:
    """
    Uno stage che si puo' chiedere dalla riga di comando deve partire.

    E' GIA' SUCCESSO, ED ERA INVISIBILE. `shots` stava in STAGES ma non in
    ORDER: argparse lo accettava come scelta valida, poi
    `[s for s in ORDER if s in chiesti]` lo buttava via, e `esegui([])` moriva
    con "max_workers must be greater than 0" — un messaggio che non nomina lo
    stage, non nomina ORDER, e manda a cercare il problema nei thread invece
    che in una lista.

    Il modulo ora rifiuta di importarsi se i due insiemi divergono; questo test
    lo verifica dall'esterno, perche' la guardia potrebbe essere tolta.
    """
    from goalmodel import ingest

    assert set(ingest.ORDER) == set(ingest.STAGES), (
        f"solo in STAGES: {sorted(set(ingest.STAGES) - set(ingest.ORDER))}, "
        f"solo in ORDER: {sorted(set(ingest.ORDER) - set(ingest.STAGES))}"
    )
    # E ognuno deve avere un host dichiarato, o finirebbe su un semaforo suo
    # senza che nessuno l'abbia deciso.
    senza_host = [s for s in ingest.STAGES if s not in ingest.HOST_DI_STAGE]
    assert not senza_host, f"stage senza host dichiarato: {senza_host}"

    with pytest.raises(ValueError, match="nessuno stage"):
        ingest.esegui([])

    print("10. ogni stage invocabile e' anche eseguibile          ok")


def test_le_stagioni_lunghe_partono_da_quelle_che_contano() -> None:
    """
    L'ordine di scaricamento segue il valore, non il calendario.

    PERCHE' E' UN TEST E NON UNA CONVENZIONE. Uno scraping da 15 ore e' stato
    fermato dopo tre stagioni, e le tre erano 1415 (burn-in, esclusa
    dall'addestramento per definizione), 1516 e 1617: nessuna entra nella
    misura di un blocco, quindi quelle ore non permettevano di decidere
    niente. Non c'era nessun errore da vedere — solo l'ordine cronologico che
    sembrava ovvio.

    Le due cose che devono restare vere: nessuna stagione persa per strada, e
    il test set davanti a tutto.
    """
    from goalmodel import config

    assert sorted(config.SEASONS_PRIORITA) == sorted(config.SEASONS), (
        "SEASONS_PRIORITA non e' una permutazione di SEASONS: "
        f"in piu' {sorted(set(config.SEASONS_PRIORITA) - set(config.SEASONS))}, "
        f"mancanti {sorted(set(config.SEASONS) - set(config.SEASONS_PRIORITA))}"
    )

    quante = len(config.TEST_SEASONS)
    assert config.SEASONS_PRIORITA[:quante] == config.TEST_SEASONS, (
        f"le prime {quante} scaricate sono {config.SEASONS_PRIORITA[:quante]}, "
        f"non il test set {config.TEST_SEASONS}"
    )
    dopo = config.SEASONS_PRIORITA[quante:quante + len(config.VALIDATION_SEASONS)]
    assert dopo == config.VALIDATION_SEASONS, (
        f"dopo il test dovrebbe venire la validazione {config.VALIDATION_SEASONS}, non {dopo}")

    # E gli stage lunghi devono davvero usarlo: un default cronologico
    # rimasto indietro annullerebbe tutto senza dare errore.
    import inspect

    from goalmodel import ingest
    for nome in ("ingest_missing", "ingest_player_stats"):
        sorgente = inspect.getsource(getattr(ingest, nome))
        assert "config.SEASONS_PRIORITA" in sorgente, (
            f"{nome} non usa config.SEASONS_PRIORITA: tornerebbe all'ordine "
            f"cronologico e le prime ore andrebbero sulle stagioni sbagliate")

    print("11. gli stage lunghi partono dal test set             ok")
