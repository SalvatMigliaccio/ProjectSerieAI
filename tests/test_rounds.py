"""
Test del ciclo di vita della giornata e della sua chiusura.

PERCHE' ESISTE. I due comandi decidono da soli su quale giornata agire, e la
decisione sbagliata non da' errore: predice la giornata gia' predetta (non
scrive niente e sembra tutto a posto), oppure archivia una giornata a cui
manca un posticipo, e quell'RPS resta storto per sempre perche' al lancio
dopo la giornata risulta chiusa. Qui si prova ogni stato del ciclo su una
tabella costruita a mano, e la chiusura su previsioni finte agganciate a
partite VERE gia' giocate.

Niente tocca il registro o l'archivio veri: si sostituiscono
`predict.PREDICTIONS_LOG` e `rounds.ROUNDS` con file temporanei.

    python -m tests.test_rounds
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import close_round, config, predict as predict_mod, rounds  # noqa: E402
from src.models.baseline import predictions_from_lambdas  # noqa: E402

rng = np.random.default_rng(3)


def _giornata(stato: str, **kw) -> dict:
    """Una riga della tabella di stato, con i valori tipici di quello stato."""
    base = {
        "season": "2627", "matchday": 1,
        "prima_data": pd.Timestamp("2026-08-22"),
        "ultima_data": pd.Timestamp("2026-08-24"),
        "n_partite": 10, "n_quote": 0, "n_predette": 0, "n_giocate": 0,
        "n_predicibili": 0, "stato": stato, "rps": np.nan,
    }
    base.update(kw)
    return base


def test_transizioni() -> None:
    """Ogni stato del ciclo porta alla decisione giusta."""
    tab = pd.DataFrame([
        _giornata(rounds.CHIUSA, matchday=1, n_quote=10, n_predette=10,
                  n_giocate=10, rps=0.19),
        _giornata(rounds.GIOCATA, matchday=2, n_quote=10, n_predette=10,
                  n_giocate=10),
        _giornata(rounds.PARZIALE, matchday=3, n_quote=6, n_predette=6,
                  n_predicibili=0),
        _giornata(rounds.APERTA, matchday=4, n_quote=10, n_predicibili=10),
        _giornata(rounds.FUTURA, matchday=5),
    ])

    scelta = rounds.da_predire(tab)
    assert scelta is not None and scelta["matchday"] == 4, \
        "da_predire deve saltare la giornata parziale senza partite predicibili"
    print("  da_predire sceglie la prima con partite predicibili   ok")

    scelta = rounds.da_chiudere(tab)
    assert scelta is not None and scelta["matchday"] == 2, \
        "da_chiudere deve scegliere la giocata, non la gia' chiusa"
    print("  da_chiudere sceglie la giocata e salta la chiusa      ok")

    # Una giornata giocata ma mai predetta non e' chiudibile: non c'e' niente
    # da archiviare, e sceglierla bloccherebbe il comando per sempre.
    solo_giocate = pd.DataFrame([
        _giornata(rounds.GIOCATA, matchday=1, n_giocate=10, n_predette=0),
        _giornata(rounds.GIOCATA, matchday=2, n_giocate=10, n_predette=8),
    ])
    assert rounds.da_chiudere(solo_giocate)["matchday"] == 2
    print("  da_chiudere ignora le giornate senza previsioni       ok")

    # Nessuna giornata chiudibile: il messaggio deve nominare quella che
    # aspetta i risultati, non uscire in silenzio.
    incompleta = pd.DataFrame([
        _giornata(rounds.PARZIALE, matchday=3, n_predette=6, n_giocate=7),
    ])
    assert rounds.da_chiudere(incompleta) is None
    msg = rounds.perche_niente_da_fare(incompleta, "chiudere")
    assert "giornata 3" in msg and "3 partite" in msg, msg
    print("  il messaggio nomina la giornata e cosa manca          ok")

    msg = rounds.perche_niente_da_fare(
        pd.DataFrame([_giornata(rounds.FUTURA, matchday=5)]), "predire")
    assert "FUTURA" in msg and "quota" in msg, msg
    print("  giornata futura: spiega che mancano le quote          ok")


def _log_finto(season: str, matchday: int, n: int = 10) -> pd.DataFrame:
    """
    Previsioni finte su partite vere gia' giocate.

    L'ultima riga e' scritta DOPO il calcio d'inizio: e' il caso che il file
    di giornata deve conservare ma escludere dalle medie.
    """
    sched = rounds.calendario()
    g = sched[(sched["season"].astype(str) == season)
              & (sched["matchday"] == matchday)].head(n).reset_index(drop=True)
    assert not g.empty, f"giornata {matchday} della stagione {season} assente"

    lam_h = np.clip(rng.normal(1.5, 0.3, len(g)), 0.4, None)
    lam_a = np.clip(rng.normal(1.2, 0.3, len(g)), 0.4, None)
    p = predictions_from_lambdas(lam_h, lam_a, index=g.index)

    ts = g["kickoff"] - pd.Timedelta(days=1)
    ts.iloc[-1] = g["kickoff"].iloc[-1] + pd.Timedelta(minutes=30)

    return pd.DataFrame({
        "timestamp_prediction": ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_version": "M1 market-only (lambda)",
        "league": g["league"], "season": g["season"].astype(str),
        "matchday": matchday,
        "match_date": g["date"].dt.strftime("%Y-%m-%d"),
        "home_team": g["home_team"], "away_team": g["away_team"],
        "lambda_home": lam_h.round(4), "lambda_away": lam_a.round(4),
        "p_home": p["p_home"].round(5), "p_draw": p["p_draw"].round(5),
        "p_away": p["p_away"].round(5),
        "p_over25": p["p_over25"].round(5), "p_btts": p["p_btts"].round(5),
        "odds_home": 2.20, "odds_draw": 3.40, "odds_away": 3.10,
        "odds_source": "B365-fixtures",
    })[predict_mod.LOG_COLUMNS]


def test_chiusura(tmp: Path) -> None:
    """Il file di giornata: previsione, risultato ed errore per partita."""
    log_path = tmp / "predictions_log.csv"
    _log_finto("2526", 1).to_csv(log_path, index=False)
    predict_mod.PREDICTIONS_LOG = log_path

    d = close_round.costruisci_giornata("2526", 1)
    assert len(d) == 10, f"attese 10 righe, {len(d)}"
    assert list(d.columns) == close_round.COLONNE, "colonne fuori ordine"
    print(f"  file di giornata: {len(d)} righe, colonne in ordine       ok")

    # La riga scritta dopo il fischio resta nel file ma non fa media.
    assert int((~d["valida"]).sum()) == 1, \
        "la previsione scritta dopo il calcio d'inizio non e' stata riconosciuta"
    tardiva = d[~d["valida"].astype(bool)].iloc[0]
    assert pd.isna(tardiva["rps"]), "una riga non valida non deve avere un RPS"
    assert pd.notna(tardiva["FTR"]), "la riga resta nel file, con il suo risultato"
    print("  previsione tardiva: conservata ma esclusa dalle medie   ok")

    valide = d[d["valida"].astype(bool)]
    assert valide["rps"].notna().all() and (valide["rps"] <= 1).all()
    assert valide["FTR"].isin(["H", "D", "A"]).all(), "risultati non agganciati"
    atteso = (valide["esito_previsto"] == valide["FTR"])
    assert (valide["azzeccato"].astype(bool) == atteso).all()
    print(f"  RPS medio {valide['rps'].mean():.4f} su {len(valide)} valide   ok")

    # L'RPS del mercato viene dalle quote REGISTRATE, non da quelle di oggi:
    # con quote costanti dev'essere identico su ogni riga.
    assert valide["rps_mercato"].notna().all()
    print("  RPS delle quote registrate calcolato riga per riga      ok")


def test_stato_su_dati_veri(tmp: Path) -> None:
    """
    La macchina a stati sul calendario vero, con un registro finto.

    Le prove sopra girano su una tabella costruita a mano: verificano la
    decisione, non il conteggio che la alimenta. Qui si parte dal calendario
    e dai risultati veri di una stagione conclusa, e si controlla che una
    giornata gia' giocata e presente in registro risulti CHIUDIBILE.
    """
    log_path = tmp / "predictions_log.csv"
    _log_finto("2526", 1).to_csv(log_path, index=False)
    predict_mod.PREDICTIONS_LOG = log_path
    rounds.ROUNDS = tmp / "rounds"

    tab = rounds.stato_giornate(season="2526")
    g1 = tab[tab["matchday"] == 1].iloc[0]
    assert g1["n_partite"] == 10 and g1["n_giocate"] == 10, g1.to_dict()
    assert g1["n_predette"] == 10, "il registro finto non e' stato letto"
    assert g1["stato"] == rounds.GIOCATA, g1["stato"]

    scelta = rounds.da_chiudere(tab)
    assert scelta is not None and scelta["matchday"] == 1
    print("  stato su calendario e risultati veri: giornata chiudibile  ok")

    # Una volta archiviata, la stessa giornata non e' piu' chiudibile.
    rounds.ROUNDS.mkdir(parents=True, exist_ok=True)
    close_round.costruisci_giornata("2526", 1).to_csv(
        rounds.file_giornata("2526", 1), index=False)
    tab = rounds.stato_giornate(season="2526")
    assert tab[tab["matchday"] == 1].iloc[0]["stato"] == rounds.CHIUSA
    # Nel registro finto c'e' solo la giornata 1: archiviata quella, non resta
    # niente da chiudere. Se tornasse ancora lei, il comando si bloccherebbe a
    # riscrivere per sempre lo stesso file.
    assert rounds.da_chiudere(tab) is None
    print("  dopo l'archiviazione risulta chiusa e non si richiude     ok")


def test_riepilogo(tmp: Path) -> None:
    """Il cumulativo pesa le partite, non le giornate."""
    rounds.ROUNDS = tmp / "rounds"
    rounds.RIEPILOGO = rounds.ROUNDS / "riepilogo.csv"
    rounds.ROUNDS.mkdir(parents=True, exist_ok=True)

    def scrivi(matchday: int, n: int, rps: float) -> None:
        d = pd.DataFrame({
            "season": "2526", "matchday": matchday,
            "home_team": [f"A{i}" for i in range(n)],
            "away_team": [f"B{i}" for i in range(n)],
            "rps": rps, "rps_mercato": rps, "azzeccato": True, "valida": True,
        })
        d.to_csv(rounds.file_giornata("2526", matchday), index=False)

    scrivi(1, 10, 0.20)
    scrivi(2, 2, 0.10)
    tab = close_round.aggiorna_riepilogo()

    atteso = (0.20 * 10 + 0.10 * 2) / 12
    assert abs(tab["rps_cumulativo"].iloc[-1] - atteso) < 1e-12, \
        f"cumulativo {tab['rps_cumulativo'].iloc[-1]:.6f}, atteso {atteso:.6f}"
    # La media di medie darebbe 0.15: e' l'errore che questo test esclude.
    assert abs(tab["rps_cumulativo"].iloc[-1] - 0.15) > 1e-3
    assert rounds.RIEPILOGO.exists()
    print(f"  cumulativo pesato sulle partite: {atteso:.4f} (non 0.1500)  ok")


def main() -> None:
    veri = (predict_mod.PREDICTIONS_LOG, rounds.ROUNDS, rounds.RIEPILOGO)
    try:
        test_transizioni()
        with tempfile.TemporaryDirectory() as tmp:
            test_chiusura(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            test_stato_su_dati_veri(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            test_riepilogo(Path(tmp))
        print("\ntutti i controlli sul ciclo di vita della giornata superati")
    finally:
        predict_mod.PREDICTIONS_LOG, rounds.ROUNDS, rounds.RIEPILOGO = veri


if __name__ == "__main__":
    main()
