"""
Il track record in Postgres (ADR 0007, fase 2).

Le proprieta' che contano sono quelle che il CSV garantiva per processo e che
il database deve garantire da solo, anche contro un bug dell'applicazione:
nessuna riga si cambia o si toglie, nessuna previsione entra dopo il fischio,
l'ora la decide il server, e una manomissione rompe la catena delle impronte.
Ognuna e' provata andando a sbatterci contro, non solo leggendo il caso buono.

Tutto negli schemi `test_*` (conftest), mai nel `track_record` vero.
"""

from __future__ import annotations

import pandas as pd
import pytest
from goalmodel import pg, registro, storage
from goalmodel.prediction import predict as predict_mod

pytestmark = pytest.mark.requires_db


@pytest.fixture
def db(monkeypatch):
    """Registro in Postgres, schema `test_track_record` vuoto prima e dopo."""
    assert pg.prefisso() == "test_", "conftest deve isolare i test dagli schemi veri"

    def cancella() -> None:
        with pg.connessione() as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{pg.prefisso()}{registro.SCHEMA}" CASCADE')
            conn.commit()

    cancella()
    monkeypatch.setenv(storage.VARIABILE, "postgres")
    yield
    cancella()


def _riga(home="Napoli", away="Lecce", model="M1 market-only (lambda)", ore=48) -> tuple:
    ko = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=ore)
    rec = pd.DataFrame([{
        "timestamp_prediction": "1999-01-01T00:00:00Z",   # ignorato: l'ora e' del server
        "model_version": model, "league": "ITA-Serie A", "season": "2627",
        "matchday": 12, "match_date": ko.strftime("%Y-%m-%d"),
        "home_team": home, "away_team": away,
        "lambda_home": 1.6031, "lambda_away": 0.9345,
        "p_home": 0.59118, "p_draw": 0.22354, "p_away": 0.18528,
        "p_over25": 0.5293, "p_btts": 0.51271,
        "odds_home": 1.62, "odds_draw": 3.75, "odds_away": 5.5, "odds_source": "B365-fixtures",
    }])
    return rec, pd.Series([ko])


def _sql(istruzione: str, params=()) -> list:
    s = registro._s()
    with pg.connessione() as conn, conn.cursor() as cur:
        cur.execute(istruzione.format(s=s), params)
        righe = cur.fetchall() if cur.description else []
        conn.commit()
    return righe


# --- scrittura ----------------------------------------------------------------

def test_una_previsione_entra_una_volta_con_l_ora_del_server(db) -> None:
    rec, ko = _riga()
    prima = pd.Timestamp.now(tz="UTC")
    assert registro.aggiungi(rec, ko) == 1
    assert registro.aggiungi(rec, ko) == 0, "stessa partita e stesso modello: non si duplica"

    letto = registro.leggi()
    assert len(letto) == 1
    ts = pd.Timestamp(letto["timestamp_prediction"].iloc[0])
    assert ts >= prima.floor("s"), "l'ora scritta dal client (1999) e' stata ignorata"
    # I numeri tornano identici a come li avrebbe letti pd.read_csv.
    assert letto["p_home"].iloc[0] == 0.59118 and letto["lambda_home"].iloc[0] == 1.6031


def test_dopo_il_fischio_il_database_rifiuta(db) -> None:
    rec, _ = _riga()
    passato = pd.Series([pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=1)])
    with pytest.raises(registro.RegistroRifiutato, match="calcio d'inizio"):
        registro.aggiungi(rec, passato)
    with pytest.raises(registro.RegistroRifiutato):
        registro.aggiungi(rec, pd.Series([pd.NaT]))
    assert registro.leggi().empty


@pytest.mark.parametrize("istruzione", [
    "UPDATE {s}.previsioni SET p_home = 0.9",
    "DELETE FROM {s}.previsioni",
    "TRUNCATE {s}.previsioni",
])
def test_nessuna_riga_si_cambia_ne_si_toglie(db, istruzione) -> None:
    registro.aggiungi(*_riga())
    with pytest.raises(Exception, match="append-only"):
        _sql(istruzione)
    assert len(registro.leggi()) == 1


def test_anche_l_archivio_delle_giornate_e_append_only(db) -> None:
    d = pd.DataFrame([{c: None for c in registro.COLONNE_GIORNATA}])
    d = d.assign(season="2627", matchday=11, home_team="Napoli", away_team="Lecce",
                 model_version="M1", valida=True, rps=0.2, rps_mercato=0.21, azzeccato=1.0)
    registro.archivia_giornata(d, "2627", 11)
    assert registro.giornata_chiusa("2627", 11)
    assert not registro.giornata_chiusa("2627", 12)
    with pytest.raises(Exception, match="append-only"):
        _sql("UPDATE {s}.giornate SET rps = 0")
    rie = registro.calcola_riepilogo(registro.leggi_giornate())
    assert rie.iloc[0]["file"] == "round_2627_11.csv" and rie.iloc[0]["rps"] == 0.2


# --- catena -------------------------------------------------------------------

def test_una_manomissione_rompe_la_catena(db) -> None:
    registro.aggiungi(*_riga("Napoli", "Lecce"))
    registro.aggiungi(*_riga("Inter", "Milan"))
    assert not [p for p in registro.verifica() if p.startswith(("previsione", "catena"))]

    # Chi ha i permessi spegne i trigger, ritocca una probabilita' e li riaccende.
    _sql("ALTER TABLE {s}.previsioni DISABLE TRIGGER USER")
    _sql("UPDATE {s}.previsioni SET p_home = 0.99 WHERE home_team = 'Napoli'")
    _sql("ALTER TABLE {s}.previsioni ENABLE TRIGGER USER")
    assert any("impronta non corrisponde" in p for p in registro.verifica())


def test_una_riga_tolta_lascia_un_anello_orfano(db) -> None:
    for home, away in (("Napoli", "Lecce"), ("Inter", "Milan"), ("Roma", "Lazio")):
        registro.aggiungi(*_riga(home, away))
    _sql("ALTER TABLE {s}.previsioni DISABLE TRIGGER USER")
    _sql("DELETE FROM {s}.previsioni WHERE home_team = 'Inter'")
    _sql("ALTER TABLE {s}.previsioni ENABLE TRIGGER USER")
    assert any("non ci sono piu'" in p for p in registro.verifica())


# --- import dello storico -----------------------------------------------------

def test_lo_storico_vero_si_importa_identico(db) -> None:
    """I CSV di track_record/ in Postgres, e la verifica che coincidano."""
    if not registro.FILE_REGISTRO.exists():
        pytest.skip("nessun registro su file in questo clone")
    esito = registro.importa()
    assert esito["previsioni"] == len(pd.read_csv(registro.FILE_REGISTRO))
    assert registro.verifica() == []
    assert registro.importa()["previsioni"] == 0, "rilanciato non duplica"

    # Stesse righe che load_log darebbe dal file, colonna per colonna.
    dal_db = registro.leggi()
    dal_file = pd.read_csv(registro.FILE_REGISTRO)
    chiave = ["model_version", "season", "home_team", "away_team"]
    for x in (dal_db, dal_file):
        x["season"] = x["season"].astype(str)
    a = dal_db.sort_values(chiave).reset_index(drop=True)
    b = dal_file.sort_values(chiave).reset_index(drop=True)
    pd.testing.assert_frame_equal(a[registro.COLONNE_REGISTRO], b[registro.COLONNE_REGISTRO],
                                  check_dtype=False)


def test_dopo_la_prima_previsione_live_l_import_si_chiude(db) -> None:
    if not registro.FILE_REGISTRO.exists():
        pytest.skip("nessun registro su file in questo clone")
    registro.aggiungi(*_riga())
    with pytest.raises(registro.RegistroRifiutato, match="import chiuso"):
        registro.importa()


# --- la pipeline ----------------------------------------------------------------

def test_append_log_in_postgres_non_tocca_il_file(db) -> None:
    """`predict.append_log` sul registro vero: va nel database, il CSV resta com'e'."""
    prima = registro.FILE_REGISTRO.stat().st_mtime_ns if registro.FILE_REGISTRO.exists() else None
    ko = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=2)
    preds = pd.DataFrame([{
        "league": "ITA-Serie A", "season": "2627", "matchday": 12, "date": ko,
        "home_team": "Napoli", "away_team": "Lecce", "kickoff": ko,
        "lambda_home": 1.6, "lambda_away": 0.9, "p_home": 0.6, "p_draw": 0.22,
        "p_away": 0.18, "p_over25": 0.5, "p_btts": 0.5,
        "odds_home": 1.6, "odds_draw": 3.8, "odds_away": 5.5, "odds_source": "B365-fixtures",
    }])
    assert predict_mod.append_log(preds, "M1 test", pd.Timestamp.now(tz="UTC")) == (1, 0)
    assert predict_mod.append_log(preds, "M1 test", pd.Timestamp.now(tz="UTC")) == (0, 1)
    assert ("ITA-Serie A", "2627", "Napoli", "Lecce") in predict_mod.already_logged("M1 test")
    dopo = registro.FILE_REGISTRO.stat().st_mtime_ns if registro.FILE_REGISTRO.exists() else None
    assert prima == dopo, "in modalita' postgres il CSV non si scrive"


# --- l'API ----------------------------------------------------------------------

def test_l_api_legge_dal_database_gli_stessi_dati_dei_file(db, monkeypatch) -> None:
    """
    Il passaggio non deve cambiare nessuna risposta: previsioni, giornate
    chiuse e riepilogo, letti da Postgres, identici a quelli letti dai CSV.
    """
    if not registro.FILE_REGISTRO.exists():
        pytest.skip("nessun registro su file in questo clone")
    from backend.api import store

    # Importare l'API chiude le scritture del processo (la sua guardia, che
    # vale anche per il registro: e' giusto). Qui l'import lo fa il test.
    monkeypatch.setattr(storage, "_vietato", None)
    src = store.default_sources()
    monkeypatch.setenv(storage.VARIABILE, "parquet")
    da_file = (store._read_predictions(src), store._read_closed(src), store._read_summary(src))

    monkeypatch.setenv(storage.VARIABILE, "postgres")
    registro.importa()
    assert store._track_in_db(src)
    da_db = (store._read_predictions(src), store._read_closed(src), store._read_summary(src))

    chiavi = (["model_version", "season", "home_team", "away_team"],
              ["source_file", "home_team", "away_team"], ["season", "matchday"])
    for nome, a, b, k in zip(("previsioni", "giornate", "riepilogo"), da_file, da_db, chiavi):
        assert len(a) == len(b), nome
        a = a.sort_values(k).reset_index(drop=True)
        b = b.sort_values(k).reset_index(drop=True)
        comuni = [c for c in a.columns if c in b.columns]
        pd.testing.assert_frame_equal(a[comuni], b[comuni], check_dtype=False, obj=nome)


def test_un_processo_chiuso_in_scrittura_non_scrive_il_registro(db, monkeypatch) -> None:
    """La guardia dell'API e degli esperimenti vale anche per il track record."""
    monkeypatch.setattr(storage, "_vietato", None)
    storage.vieta_scritture("prova")
    with pytest.raises(storage.ScritturaVietata):
        registro.aggiungi(*_riga())
    with pytest.raises(storage.ScritturaVietata):
        registro.importa()
