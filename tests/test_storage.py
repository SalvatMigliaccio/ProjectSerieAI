"""
`storage` e `pg`: lo stesso dato, ovunque stia (ADR 0006).

Due famiglie di test.

SENZA DATABASE: le tre modalita' di `storage` su file temporanei — dove
scrive, cosa legge, cosa dice quando il dato manca, e che le guardie lo
chiudano davvero.

CON IL DATABASE (`requires_db`): il contratto che decide tutta la migrazione.
Un DataFrame che passa da Postgres deve tornare IDENTICO a come tornerebbe da
un giro su parquet: stesse colonne, stessi dtype, stessi bit. I casi scelti
sono quelli che una conversione sbaglia senza dirlo — NaN contro NA in un
`Float64`, -0.0, l'infinito, la stringa vuota contro NULL, il timestamp con
fuso, la colonna annidata di WhoScored. Poi i vincoli: la quadrupla duplicata
deve far fallire la scrittura e lasciare intatta la versione precedente.

Gli schemi sono `test_*` (`GOALMODEL_PG_PREFISSO`, impostato da conftest) e
vengono cancellati alla fine: i dati veri nel database non si toccano.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd
import pytest

from goalmodel import archivio, pg, storage


@pytest.fixture
def cartelle(monkeypatch, tmp_path):
    for livello in storage.CARTELLE:
        monkeypatch.setitem(storage.CARTELLE, livello, tmp_path / livello)
    return tmp_path


def _campione() -> pd.DataFrame:
    """Un frame con ogni tipo che i dati veri contengono, e i loro casi limite."""
    n = 6
    return pd.DataFrame({
        "league": ["ITA-Serie A"] * n,
        "season": pd.Series(["2627", "2627", "2526", "2526", "1415", "1415"], dtype="str"),
        "home_team": [f"Casa{i}" for i in range(n)],
        "away_team": [f"Fuori{i}" for i in range(n)],
        "float": [0.1 + 0.2, -0.0, math.inf, -math.inf, np.nan, 5e-324],
        # In pandas 3 un NaN dentro un Float64 e' NA gia' alla costruzione.
        "Float64": pd.array([1 / 3, None, 7.0, 2.5, -0.0, 1e308], dtype="Float64"),
        "Int64": pd.array([1, None, -3, 2**53 + 1, 0, None], dtype="Int64"),
        "int": np.arange(n, dtype="int64") - 2,
        "bool": [True, False, True, False, True, False],
        "boolean": pd.array([True, None, False, True, None, False], dtype="boolean"),
        "testo": pd.Series(["a", "", None, 'con "virgolette", e virgola', "à è ì", "a\nb"],
                           dtype="str"),
        "string": pd.array(["x", None, "", "y", "z", "w"], dtype="string"),
        "date": pd.to_datetime(["2026-08-22 20:45", "2026-08-23", None,
                                "2014-08-30 18:00:00.123456", "2026-11-08 12:30", "2000-01-01"],
                               format="ISO8601"),
        "downloaded_at": pd.to_datetime(["2026-11-06 18:16:56.5", None, "2026-03-29 01:30",
                                         "2026-10-25 02:30", "2026-01-01", "2026-07-01"],
                                        utc=True, format="ISO8601"),
        "vuota": [None] * n,
        "B365>2.5": [1.9, 2.0, np.nan, 1.75, 2.1, 1.66],
        "incidents": [[{"minute": "12", "type": 16, "playerName": "Kvara"}], [], None,
                      [{"minute": "90+3", "type": 16, "playerName": None}],
                      [{"minute": "1", "type": 1, "playerName": "x"},
                       {"minute": "2", "type": 2, "playerName": "y"}], []],
    })


def _via_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Il riferimento: cio' che la pipeline leggeva prima della migrazione."""
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return pd.read_parquet(io.BytesIO(buf.getvalue()))


# ---------------------------------------------------------------------------
# Senza database
# ---------------------------------------------------------------------------

def test_parquet_scrive_e_legge_dove_dice(cartelle) -> None:
    df = _campione().drop(columns=["incidents"])
    storage.scrivi("processed", "features_prova", df)
    assert (cartelle / "processed" / "features_prova.parquet").exists()
    assert storage.esiste("processed", "features_prova")
    assert archivio.differenze(storage.leggi("processed", "features_prova"), _via_parquet(df)) == []
    assert list(storage.leggi("processed", "features_prova", columns=["float"]).columns) == ["float"]


def test_sottocartelle_e_glob(cartelle) -> None:
    df = pd.DataFrame({"season": ["1516"], "game_id": [1]})
    for stagione in ("1516", "1617"):
        storage.scrivi("raw", f"whoscored/schedule_{stagione}", df)
    storage.scrivi("raw", "whoscored_missing", df)
    assert storage.nomi("raw", "whoscored/schedule_*") == [
        "whoscored/schedule_1516", "whoscored/schedule_1617"]


def test_dato_assente_e_uno_stato(cartelle) -> None:
    assert not storage.esiste("raw", "mai_scritto")
    assert storage.leggi_opzionale("raw", "mai_scritto") is None
    with pytest.raises(storage.DatoAssente):
        storage.leggi("raw", "mai_scritto")
    # Stessa famiglia di prima: chi catturava FileNotFoundError continua a farlo.
    assert issubclass(storage.DatoAssente, FileNotFoundError)


def test_modalita_sconosciuta_solleva(monkeypatch) -> None:
    monkeypatch.setenv(storage.VARIABILE, "s3")
    with pytest.raises(ValueError, match="non valida"):
        storage.modalita()


def test_le_guardie_chiudono_storage(cartelle) -> None:
    storage.vieta_scritture("prova")
    with pytest.raises(storage.ScritturaVietata):
        storage.scrivi("raw", "qualcosa", pd.DataFrame({"a": [1]}))
    assert not (cartelle / "raw" / "qualcosa.parquet").exists()


def test_la_guardia_degli_esperimenti_chiude_storage(cartelle) -> None:
    from goalmodel import experiments

    experiments.proteggi_produzione()
    with pytest.raises(storage.ScritturaVietata):
        storage.scrivi("processed", "features_form", pd.DataFrame({"a": [1]}))


def test_la_guardia_dell_api_chiude_storage(cartelle) -> None:
    pytest.importorskip("fastapi")
    from backend import api

    api._guard_writes()
    with pytest.raises(storage.ScritturaVietata):
        storage.scrivi("interim", "matches_master", pd.DataFrame({"a": [1]}))


def test_da_percorso_riconosce_solo_i_dati(cartelle) -> None:
    assert storage.da_percorso(cartelle / "raw" / "fbref_schedule.parquet") == ("raw", "fbref_schedule")
    assert storage.da_percorso(cartelle / "raw" / "whoscored" / "schedule_1516.parquet") == (
        "raw", "whoscored/schedule_1516")
    assert storage.da_percorso(cartelle / "altrove.parquet") is None


def test_differenze_vede_i_bit() -> None:
    """Il comparatore della verifica non deve essere piu' indulgente del test di produzione."""
    a = pd.DataFrame({"x": [0.0, np.nan]})
    assert archivio.differenze(a, pd.DataFrame({"x": [-0.0, np.nan]})) != []
    f = pd.DataFrame({"x": pd.array([0.0, None], dtype="Float64")})
    g = pd.DataFrame({"x": pd.array([None, 0.0], dtype="Float64")})
    assert archivio.differenze(f, f.copy()) == []
    assert archivio.differenze(f, g) != [], "un NA spostato e' un valore diverso"


# ---------------------------------------------------------------------------
# Con il database
# ---------------------------------------------------------------------------

@pytest.fixture
def db_pulito():
    """Schemi `test_*` vuoti prima, cancellati dopo."""
    def cancella() -> None:
        with pg.connessione() as conn, conn.cursor() as cur:
            for s in (*pg.LIVELLI, pg.SCHEMA_META):
                cur.execute(f'DROP SCHEMA IF EXISTS "{pg.prefisso()}{s}" CASCADE')
            conn.commit()

    assert pg.prefisso() == "test_", "conftest deve isolare i test dagli schemi veri"
    cancella()
    yield
    cancella()


@pytest.mark.requires_db
def test_postgres_restituisce_cio_che_restituirebbe_il_parquet(db_pulito) -> None:
    df = _campione()
    pg.scrivi("processed", "campione", df)
    letto = pg.leggi("processed", "campione")
    assert archivio.differenze(letto, _via_parquet(df)) == []


@pytest.mark.requires_db
def test_sottoinsieme_di_colonne_nell_ordine_chiesto(db_pulito) -> None:
    df = _campione()
    pg.scrivi("raw", "campione", df)
    colonne = ["downloaded_at", "Float64", "league"]
    assert archivio.differenze(pg.leggi("raw", "campione", colonne),
                               _via_parquet(df)[colonne]) == []
    with pytest.raises(KeyError):
        pg.leggi("raw", "campione", ["non_esiste"])


@pytest.mark.requires_db
def test_frame_vuoto(db_pulito) -> None:
    """Lo snapshot delle quote fra un turno e l'altro ha zero righe: e' normale."""
    df = _campione().drop(columns=["incidents"]).iloc[0:0]
    pg.scrivi("raw", "fixtures_odds", df)
    assert archivio.differenze(pg.leggi("raw", "fixtures_odds"), _via_parquet(df)) == []


@pytest.mark.requires_db
def test_ogni_scrittura_alza_la_versione(db_pulito) -> None:
    df = pd.DataFrame({"a": [1.0]})
    assert pg.versione("processed", "x") is None
    v1 = pg.scrivi("processed", "x", df)
    v2 = pg.scrivi("processed", "x", df)
    assert v2 > v1 and pg.versione("processed", "x") == v2


def _master(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "league": ["ITA-Serie A"] * n, "season": ["2627"] * n,
        "home_team": [f"C{i}" for i in range(n)], "away_team": [f"F{i}" for i in range(n)],
        "date": pd.to_datetime(["2026-08-22"] * n),
        "FTHG": [1.0] * n, "FTAG": [0.0] * n, "FTR": ["H"] * n,
    })


@pytest.mark.requires_db
def test_la_quadrupla_duplicata_non_entra_e_la_tabella_resta(db_pulito) -> None:
    import psycopg

    buono = _master()
    v = pg.scrivi("interim", "matches_master", buono)
    doppio = pd.concat([buono, buono.iloc[[0]]], ignore_index=True)
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.scrivi("interim", "matches_master", doppio)
    assert pg.versione("interim", "matches_master") == v
    assert archivio.differenze(pg.leggi("interim", "matches_master"), _via_parquet(buono)) == []


@pytest.mark.requires_db
def test_la_chiave_nulla_non_entra(db_pulito) -> None:
    import psycopg

    rotto = _master()
    rotto.loc[1, "league"] = None
    with pytest.raises(psycopg.errors.NotNullViolation):
        pg.scrivi("interim", "matches_master", rotto)


@pytest.mark.requires_db
def test_lo_spareggio_entra_il_doppione_vero_no(db_pulito) -> None:
    """Spezia-Verona 2022/23: due partite, una con `week` nullo. Regola n.5."""
    import psycopg

    base = _master(2).assign(week=pd.array([25, 26], dtype="Int64"))
    spareggio = base.iloc[[0]].assign(week=pd.array([None], dtype="Int64"))
    pg.scrivi("raw", "fbref_schedule", pd.concat([base, spareggio], ignore_index=True))
    doppione = base.iloc[[0]].assign(week=pd.array([38], dtype="Int64"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.scrivi("raw", "fbref_schedule", pd.concat([base, doppione], ignore_index=True))


@pytest.mark.requires_db
def test_un_tipo_ignoto_solleva_prima_di_scrivere(db_pulito) -> None:
    df = pd.DataFrame({"x": pd.Categorical(["a", "b"])})
    with pytest.raises(pg.TipoNonSupportato):
        pg.scrivi("processed", "categorie", df)
    assert pg.versione("processed", "categorie") is None


@pytest.mark.requires_db
def test_storage_in_modalita_postgres_non_tocca_i_file(db_pulito, cartelle, monkeypatch) -> None:
    monkeypatch.setenv(storage.VARIABILE, "postgres")
    df = _campione()
    storage.scrivi("processed", "features_prova", df)
    assert not (cartelle / "processed" / "features_prova.parquet").exists()
    assert storage.esiste("processed", "features_prova")
    assert storage.nomi("processed", "features_*") == ["features_prova"]
    assert archivio.differenze(storage.leggi("processed", "features_prova"), _via_parquet(df)) == []
    assert storage.impronta("processed", "features_prova")[-1] == pg.versione(
        "processed", "features_prova")


@pytest.mark.requires_db
def test_doppio_scrive_entrambi_e_legge_il_file(db_pulito, cartelle, monkeypatch) -> None:
    monkeypatch.setenv(storage.VARIABILE, "doppio")
    df = _campione()
    storage.scrivi("raw", "matches", df.drop(columns=["incidents"]))
    assert (cartelle / "raw" / "matches.parquet").exists()
    assert pg.versione("raw", "matches") is not None


def test_doppio_con_postgres_giu_scrive_il_file_e_prosegue(cartelle, monkeypatch, caplog) -> None:
    monkeypatch.setenv(storage.VARIABILE, "doppio")
    monkeypatch.setenv(pg.VARIABILE_URL, "postgresql://nessuno:x@127.0.0.1:1/nulla")
    storage.scrivi("raw", "matches", pd.DataFrame({"a": [1]}))
    assert (cartelle / "raw" / "matches.parquet").exists()
    assert "NON in Postgres" in caplog.text
