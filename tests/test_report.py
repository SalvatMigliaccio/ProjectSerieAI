"""
Test del report settimanale.

PERCHE' ESISTE. Le sezioni piu' utili del report — il grafico dell'RPS, la
curva di calibrazione, il confronto con la previsione precedente — sono
proprio quelle che con il registro di oggi (poche righe, nessuna risolta) non
vengono mai eseguite. Restano non provate finche' non succede in produzione,
cioe' fra mesi, quando accorgersi di un errore costa una giornata di track
record. Qui si finge un registro pieno e si guarda che escano.

Il registro vero non viene toccato: si sostituisce `predict.PREDICTIONS_LOG`
con un file temporaneo, che e' il motivo per cui `report.py` legge quel
percorso al momento della chiamata invece che dal default di `load_log`.

    python -m tests.test_report
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src import predict as predict_mod  # noqa: E402
from src import report  # noqa: E402
from src.models.baseline import predictions_from_lambdas  # noqa: E402

rng = np.random.default_rng(11)


def registro_finto(n_giornate: int = 12) -> pd.DataFrame:
    """
    Un registro plausibile costruito su partite VERE gia' giocate.

    Le partite sono vere perche' il report aggancia i risultati sulla
    quadrupla contro `matches_master`: con squadre inventate il join non
    troverebbe niente e il test verificherebbe solo il ramo vuoto, cioe'
    quello che gia' si osserva in produzione.
    """
    m = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    m = m[m["season"] == "2526"].dropna(subset=["FTR"]).copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date").head(n_giornate * 10)

    # Lambda finti ma sensati, cosi' le probabilita' non sono degeneri e la
    # curva di calibrazione ha davvero dei bin da riempire.
    lam_h = np.clip(rng.normal(1.5, 0.35, len(m)), 0.4, None)
    lam_a = np.clip(rng.normal(1.2, 0.30, len(m)), 0.4, None)
    p = predictions_from_lambdas(lam_h, lam_a, index=m.index)

    return pd.DataFrame({
        "timestamp_prediction": (m["date"] - pd.Timedelta(days=1)).dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "model_version": "M1 market-only (lambda)",
        "league": m["league"], "season": m["season"],
        "matchday": np.repeat(np.arange(1, n_giornate + 1), 10)[:len(m)],
        "match_date": m["date"].dt.strftime("%Y-%m-%d"),
        "home_team": m["home_team"], "away_team": m["away_team"],
        "lambda_home": lam_h.round(4), "lambda_away": lam_a.round(4),
        "p_home": p["p_home"].round(5), "p_draw": p["p_draw"].round(5),
        "p_away": p["p_away"].round(5),
        "p_over25": p["p_over25"].round(5), "p_btts": p["p_btts"].round(5),
        "odds_home": 2.10, "odds_draw": 3.40, "odds_away": 3.30,
        "odds_source": "B365-fixtures",
    })[predict_mod.LOG_COLUMNS]


def esito_finto(storico: pd.DataFrame) -> predict_mod.Esito:
    """
    Una giornata da prevedere che riusa DUE squadre gia' presenti nel
    registro: e' l'unico modo di far scattare la sezione 'cosa e' cambiato'.
    """
    ultima = storico.tail(4)
    squadre = list(ultima["home_team"])[:2] + list(ultima["away_team"])[:2]
    ko = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=2)

    righe = []
    for i in range(0, 4, 2):
        righe.append({
            "league": "ITA-Serie A", "season": "2526",
            "home_team": squadre[i], "away_team": squadre[i + 1],
            "matchday": 30, "date": ko.tz_convert(None).normalize(),
            "kickoff": ko, "lambda_home": 1.10 + 0.4 * i,
            "lambda_away": 1.05, "odds_home": 2.2, "odds_draw": 3.3,
            "odds_away": 3.2, "odds_source": "B365-fixtures",
        })
    preds = pd.DataFrame(righe)
    p = predictions_from_lambdas(preds["lambda_home"].to_numpy(float),
                                preds["lambda_away"].to_numpy(float),
                                index=preds.index)
    preds = pd.concat([preds.drop(columns=["lambda_home", "lambda_away"]), p], axis=1)
    return predict_mod.Esito(
        matchday=30, preds=preds, skipped=pd.DataFrame(),
        model_version="M1 market-only (lambda)", now=pd.Timestamp.now(tz="UTC"),
    )


def test_ramo_vuoto() -> None:
    """Senza registro il report esce lo stesso: e' il caso del primo lancio."""
    with tempfile.TemporaryDirectory() as tmp:
        _con_registro(Path(tmp) / "assente.csv", lambda: None)
        esito = predict_mod.Esito(matchday=None, model_version="M1 market-only (lambda)")
        html = _genera(esito, Path(tmp))
        assert "Registro non ancora creato" in html or "Track record" in html
        assert "Stato del sistema" in html
    print("  registro assente: il report esce comunque   ok")


def _con_registro(path: Path, scrivi) -> None:
    predict_mod.PREDICTIONS_LOG = path
    scrivi()


def _genera(esito: predict_mod.Esito, tmp: Path) -> str:
    """Genera il report dirottando entrambe le destinazioni nel temporaneo."""
    report.REPORT = tmp / "report.html"
    report.ARCHIVIO = tmp / "archivio"
    path = report.build(esito, registro=report.RIGENERATO, diverge=False)
    return path.read_text(encoding="utf-8")


def main() -> None:
    vero_log, vero_report, vero_arch = (
        predict_mod.PREDICTIONS_LOG, report.REPORT, report.ARCHIVIO)
    try:
        test_ramo_vuoto()

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            storico = registro_finto()
            log_path = tmp / "predictions_log.csv"
            storico.to_csv(log_path, index=False)
            predict_mod.PREDICTIONS_LOG = log_path

            esito = esito_finto(storico)
            html = _genera(esito, tmp)

            assert (tmp / "archivio" / "giornata_2526_30.html").exists(), \
                "la copia d'archivio della giornata non e' stata scritta"
            print(f"  report generato: {len(html)} byte, archivio scritto   ok")

            # Sezione 2: le due squadre riprese dal registro devono comparire
            # con un confronto, non con il messaggio di sezione vuota.
            assert "previsione precedente" in html, \
                "sezione 'cosa e' cambiato' vuota: il confronto non ha agganciato"
            print("  sezione 2: confronto con la previsione precedente   ok")

            # Sezione 4: con 120 partite risolte devono uscire ENTRAMBI i
            # grafici, che sono il pezzo che in produzione non gira mai.
            assert "<polyline class=\"serie\"" in html, "manca il grafico dell'RPS"
            assert "class='diagonale'" in html or 'class="diagonale"' in html, \
                "manca la curva di calibrazione"
            print("  sezione 4: grafico RPS e curva di calibrazione   ok")

            # Il fabbisogno passa dal bootstrap, non piu' dal ripiego.
            assert "bootstrap a cluster sulle previsioni registrate" in html, \
                "con 12 giornate il fabbisogno doveva venire dal bootstrap"
            print("  sezione 4: fabbisogno stimato col bootstrap a cluster   ok")

            # Nessun placeholder non sostituito, nessun NaN a video.
            for brutto in ("nan", "None", "{}"):
                assert f">{brutto}<" not in html, f"'{brutto}' finito nel report"
            print("  nessun NaN o placeholder impaginato   ok")

        print("\ntutti i controlli sul report superati")
    finally:
        predict_mod.PREDICTIONS_LOG = vero_log
        report.REPORT, report.ARCHIVIO = vero_report, vero_arch


if __name__ == "__main__":
    main()
