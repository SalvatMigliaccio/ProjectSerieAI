"""
Test del blocco A: riposo, congestione e derby.

PERCHE' ESISTE. Riposo e congestione sono conteggi su finestre temporali, e
l'errore tipico — contare la partita corrente dentro la propria finestra — non
da' nessun errore: produce numeri plausibili e leakage. Qui le finestre si
verificano su un calendario costruito a mano, dove il valore giusto si conta
sulle dita.

    python -m tests.test_context
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.features import context  # noqa: E402


def calendario() -> pd.DataFrame:
    """
    Un calendario con distanze note, scelte per essere contabili a mente.

    Napoli gioca il 1, 4, 8, 15 e 30 agosto: gli intervalli sono 3, 4, 7 e 15
    giorni. Nella finestra prima del 15 agosto cadono esattamente le partite
    del 4 e dell'8: quella del 1 e' a 14 giorni ESATTI e resta fuori, ed e'
    proprio il confine che si vuole verificare. Il 30 agosto la finestra e'
    vuota, perche' il 15 dista quindici giorni.
    """
    giorni = [1, 4, 8, 15, 30]
    avversari = ["Roma", "Lazio", "Inter", "Milan", "Juventus"]
    righe = []
    for g, avv in zip(giorni, avversari):
        righe.append({
            "league": "ITA-Serie A", "season": "2526",
            "home_team": "Napoli", "away_team": avv,
            "date": datetime(2025, 8, g),
        })
    # Una partita in piu' fra due squadre terze, per avere righe che non
    # riguardano il Napoli e verificare che non ne sporchino i conteggi.
    righe.append({
        "league": "ITA-Serie A", "season": "2526",
        "home_team": "Roma", "away_team": "Lazio",
        "date": datetime(2025, 8, 6),
    })
    return pd.DataFrame(righe)


def test_riposo() -> None:
    """I giorni dall'ultima partita, contati a mano."""
    out = context.build(calendario(), save=False)
    nap = out[out["home_team"] == "Napoli"].sort_values("date")

    atteso = [np.nan, 3.0, 4.0, 7.0, 15.0]
    ottenuto = nap["home_rest_days"].tolist()
    assert pd.isna(ottenuto[0]), f"la prima partita non puo' avere riposo: {ottenuto[0]}"
    for i, (a, o) in enumerate(zip(atteso[1:], ottenuto[1:]), start=1):
        assert abs(a - o) < 1e-9, f"partita {i}: atteso {a} giorni, ottenuto {o}"
    print(f"  riposo: {ottenuto[1:]} giorni, come contato a mano   ok")


def test_congestione_esclude_la_partita_stessa() -> None:
    """
    La finestra e' (d-14, d): aperta da entrambi i lati.

    E' il controllo che vale il test. Con la finestra chiusa a destra ogni
    partita conterebbe se' stessa e tutte le righe avrebbero un +1: nessun
    errore, nessun sintomo, e una feature che sa qualcosa della partita che
    deve predire.
    """
    out = context.build(calendario(), save=False)
    nap = out[out["home_team"] == "Napoli"].sort_values("date")
    ottenuto = nap["home_matches_14d"].tolist()

    # 1 ago: nessuna prima.   4 ago: solo l'1.   8 ago: 1 e 4.
    # 15 ago: 4 e 8 — l'1 e' a 14 giorni ESATTI e resta fuori.
    # 30 ago: nessuna, perche' il 15 dista 15 giorni.
    atteso = [0, 1, 2, 2, 0]
    assert ottenuto == atteso, f"atteso {atteso}, ottenuto {ottenuto}"
    print(f"  congestione: {ottenuto} partite/14g, confine escluso   ok")

    # Il confine a 14 giorni esatti e' fuori: si verifica esplicitamente,
    # perche' e' l'unico punto in cui 'left' e 'right' danno risposte diverse.
    quindici = nap[nap["date"] == datetime(2025, 8, 15)].iloc[0]
    assert quindici["home_matches_14d"] == 2, \
        "la partita a 14 giorni esatti dev'essere ESCLUSA dalla finestra"
    print("  la partita a 14 giorni esatti resta fuori            ok")


def test_troncamento_riposo() -> None:
    """Oltre un mese non e' riposo: e' pausa fra stagioni."""
    df = calendario()
    df.loc[len(df)] = {
        "league": "ITA-Serie A", "season": "2627",
        "home_team": "Napoli", "away_team": "Roma",
        "date": datetime(2026, 8, 20),          # un anno dopo
    }
    out = context.build(df, save=False)
    valore = out[out["date"] == datetime(2026, 8, 20)]["home_rest_days"].iloc[0]
    assert valore == context.RIPOSO_MASSIMO, \
        f"riposo non troncato: {valore} invece di {context.RIPOSO_MASSIMO}"
    print(f"  riposo troncato a {context.RIPOSO_MASSIMO:.0f} giorni "
          f"(grezzo: 365)              ok")


def test_derby_non_ordinato() -> None:
    """Inter-Milan e Milan-Inter sono lo stesso derby."""
    derby = context.carica_derby()
    if derby is None:
        print("  derbies.csv assente: controllo saltato")
        return

    df = pd.DataFrame([
        {"league": "ITA-Serie A", "season": "2526", "home_team": "Inter",
         "away_team": "Milan", "date": datetime(2025, 9, 1)},
        {"league": "ITA-Serie A", "season": "2526", "home_team": "Milan",
         "away_team": "Inter", "date": datetime(2026, 2, 1)},
        {"league": "ITA-Serie A", "season": "2526", "home_team": "Napoli",
         "away_team": "Udinese", "date": datetime(2025, 9, 1)},
    ])
    out = context.build(df, save=False)
    assert out["is_derby"].tolist() == [1, 1, 0], out["is_derby"].tolist()
    assert out["derby_intensity"].tolist() == [3, 3, 0], \
        "il derby di Milano dev'essere 'city' in entrambi i versi"
    print("  derby riconosciuto nei due versi, non derby a zero   ok")


def test_derby_assente_non_inventa() -> None:
    """
    Senza il file, le colonne del derby non esistono.

    Metterle tutte a zero direbbe "nessuna partita e' un derby", che e' un
    dato falso. Assente e falso non sono la stessa cosa, e il modello deve
    poter distinguere.
    """
    vero = config.DERBIES
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config.DERBIES = Path(tmp) / "non_esiste.csv"
            out = context.build(calendario(), save=False)
            assert "is_derby" not in out.columns, \
                "senza derbies.csv la colonna non deve comparire"
            assert "home_rest_days" in out.columns, \
                "il resto del blocco deve funzionare lo stesso"
    finally:
        config.DERBIES = vero
    print("  senza derbies.csv: colonne assenti, non zero        ok")


def test_partite_future() -> None:
    """
    Una partita da giocare riceve riposo e congestione come le altre.

    E' il caso di `predict.py`, che accoda la riga futura allo storico. Il
    contesto guarda indietro, quindi non ha bisogno di sapere che quella riga
    e' speciale — ma se lo sbagliasse, la previsione userebbe una feature
    diversa da quelle su cui e' stato addestrato il modello.
    """
    df = calendario()
    df.loc[len(df)] = {
        "league": "ITA-Serie A", "season": "2526",
        "home_team": "Napoli", "away_team": "Torino",
        "date": datetime(2025, 9, 3),           # non giocata: nessun risultato
    }
    out = context.build(df, save=False)
    futura = out[out["date"] == datetime(2025, 9, 3)].iloc[0]
    assert futura["home_rest_days"] == 4.0, futura["home_rest_days"]
    assert futura["home_matches_14d"] == 1, futura["home_matches_14d"]
    print("  riga futura: riposo 4 giorni, 1 partita in 14        ok")


def main() -> None:
    test_riposo()
    test_congestione_esclude_la_partita_stessa()
    test_troncamento_riposo()
    test_derby_non_ordinato()
    test_derby_assente_non_inventa()
    test_partite_future()
    print("\ntutti i controlli sul blocco contesto superati")


if __name__ == "__main__":
    main()
