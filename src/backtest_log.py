"""
Track record: rilegge predictions_log.csv e lo confronta con i risultati veri.

NON E' UN BACKTEST
Un backtest riaddestra e ripredice il passato, e per quanto onesto sia il
protocollo chi lo scrive conosce gia' come e' andata: la scelta del modello,
delle feature e degli iperparametri e' stata fatta guardando quegli stessi
anni. Qui no. Ogni riga del registro e' stata scritta prima del calcio
d'inizio, da un modello congelato, senza sapere il risultato. E' l'unica
misura davvero fuori campione che il progetto possa produrre, e cresce di
dieci partite a settimana.

Serve a rispondere a una domanda che il test set non puo' toccare: il modello
si comporta in produzione come si e' comportato in valutazione? Se l'RPS
registrato divergesse da 0.188 in modo persistente, il problema non sarebbe il
modello ma la pipeline — quote lette in un momento diverso, feature calcolate
con dati che in produzione arrivano tardi, nomi che non si agganciano.

QUALE RIGA SI USA QUANDO CE NE SONO PIU' D'UNA
La PRIMA per timestamp. Il registro e' append-only e rilanciare la previsione
della stessa giornata aggiunge righe: la piu' recente e' anche la piu'
informata, quindi usarla gonfierebbe il risultato. La prima e' quella scritta
con il maggiore anticipo, ed e' l'unica onesta.

Uso:
    python -m src.backtest_log
    python -m src.backtest_log --by-season
    python -m src.backtest_log --pending
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from . import config
from .evaluate import (
    _onehot,
    accuracy,
    brier,
    calibration_table,
    ece_pooled,
    log_loss,
    outcome_index,
    rps,
)
from .predict import LOG_COLUMNS, PREDICTIONS_LOG

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("backtest_log")

KEYS = ["league", "season", "home_team", "away_team"]
PROB_COLS = ["p_home", "p_draw", "p_away"]


def load_log(path=PREDICTIONS_LOG, first_only: bool = True) -> pd.DataFrame:
    """Il registro, con una riga per (partita, modello) se `first_only`."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} non esiste. Nessuna previsione registrata: lancia prima "
            "python -m src.predict --next"
        )
    df = pd.read_csv(path)
    mancanti = [c for c in LOG_COLUMNS if c not in df.columns]
    if mancanti:
        raise ValueError(f"registro malformato, colonne mancanti: {mancanti}")

    df["timestamp_prediction"] = pd.to_datetime(df["timestamp_prediction"], utc=True)
    df["season"] = df["season"].astype(str)
    n = len(df)
    if first_only:
        df = (
            df.sort_values("timestamp_prediction")
            .drop_duplicates(subset=KEYS + ["model_version"], keep="first")
        )
    log.info("registro: %d righe, %d previsioni distinte", n, len(df))
    return df.reset_index(drop=True)


def attach_results(preds: pd.DataFrame) -> pd.DataFrame:
    """Aggancia i risultati veri. Chiave la quadrupla, mai la data."""
    truth = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    truth = truth[KEYS + ["date", "FTHG", "FTAG", "FTR"]].drop_duplicates(subset=KEYS)
    truth["season"] = truth["season"].astype(str)

    out = preds.merge(truth, on=KEYS, how="left", validate="many_to_one")

    # Controllo di coerenza: la data registrata al momento della previsione
    # deve coincidere con quella del risultato, salvo rinvii. Uno scarto
    # grande non invalida la previsione ma segnala che il calendario e'
    # cambiato dopo che l'avevamo scritta.
    risolte = out["FTR"].notna()
    if risolte.any():
        scarto = (
            pd.to_datetime(out.loc[risolte, "date"])
            - pd.to_datetime(out.loc[risolte, "match_date"])
        ).dt.days.abs()
        rinviate = int((scarto > 3).sum())
        if rinviate:
            log.warning("%d partite giocate a piu' di 3 giorni dalla data prevista "
                        "(rinvii): la previsione resta valida", rinviate)
    return out


def metrics_block(g: pd.DataFrame, etichetta: str) -> dict:
    p = g[PROB_COLS].to_numpy(dtype=float)
    y = outcome_index(g["FTR"])
    return {
        "gruppo": etichetta,
        "n": len(g),
        "RPS": float(rps(p, y).mean()),
        "log_loss": float(log_loss(p, y).mean()),
        "Brier": float(brier(p, y).mean()),
        "accuratezza": float(accuracy(p, y).mean()),
        "ECE": ece_pooled(p, y),
    }


def summary(resolved: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    rows = []
    for model, g in resolved.groupby("model_version", sort=False):
        if by is None:
            rows.append({"model_version": model, **metrics_block(g, "tutte")})
        else:
            for chiave, sub in g.groupby(by, sort=True):
                if len(sub) >= 10:
                    rows.append({"model_version": model, **metrics_block(sub, str(chiave))})
    return pd.DataFrame(rows)


def market_reference(resolved: pd.DataFrame) -> dict | None:
    """
    RPS delle quote registrate, de-viggate in modo proporzionale.

    E' il riferimento contro cui leggere il track record, ed e' ricavato dalle
    quote SALVATE NEL REGISTRO, non da quelle di oggi. E' la ragione per cui
    le quote vanno registrate: senza, a distanza di mesi non si potrebbe piu'
    distinguere un errore del modello da un prezzo che nel frattempo e'
    cambiato.
    """
    cols = ["odds_home", "odds_draw", "odds_away"]
    ok = resolved.dropna(subset=cols + ["FTR"])
    if ok.empty:
        return None
    inv = 1.0 / ok[cols].to_numpy(dtype=float)
    p = inv / inv.sum(axis=1, keepdims=True)
    y = outcome_index(ok["FTR"])
    return {
        "gruppo": "quote registrate (de-vig proporzionale)",
        "n": len(ok),
        "RPS": float(rps(p, y).mean()),
        "log_loss": float(log_loss(p, y).mean()),
        "Brier": float(brier(p, y).mean()),
        "accuratezza": float(accuracy(p, y).mean()),
        "ECE": ece_pooled(p, y),
    }


def report(by_season: bool = False, show_pending: bool = False, path=PREDICTIONS_LOG) -> None:
    preds = load_log(path)
    joined = attach_results(preds)

    resolved = joined[joined["FTR"].notna()].copy()
    pending = joined[joined["FTR"].isna()].copy()

    print(f"\n=== TRACK RECORD — {path.name} ===")
    print(f"previsioni registrate: {len(joined)}   risolte: {len(resolved)}   "
          f"in attesa: {len(pending)}")
    if not joined.empty:
        print(f"periodo: dal {joined['match_date'].min()} al {joined['match_date'].max()}")

    if show_pending:
        if pending.empty:
            print("\nnessuna previsione in attesa di risultato")
        else:
            print("\n=== IN ATTESA DI RISULTATO ===")
            print(pending[["match_date", "home_team", "away_team", "p_home", "p_draw",
                           "p_away", "model_version"]].to_string(index=False))
        return

    if resolved.empty:
        print("\nnessuna previsione ancora risolta: torna dopo che le partite si sono giocate.")
        return

    tab = summary(resolved, by="season" if by_season else None)
    ref = market_reference(resolved)
    if ref is not None:
        tab = pd.concat([tab, pd.DataFrame([{"model_version": "-", **ref}])], ignore_index=True)

    print("\n=== METRICHE SULLE SOLE PREVISIONI FUORI CAMPIONE ===")
    print(tab.round(4).to_string(index=False))
    print(f"\nriferimento in valutazione (test set 2324-2526): RPS 0.1881")
    print("Uno scarto persistente da quel valore non e' un modello che sbaglia:")
    print("e' la pipeline di produzione che si comporta diversamente dal backtest.")

    if len(resolved) >= 50:
        p = resolved[PROB_COLS].to_numpy(dtype=float)
        y = outcome_index(resolved["FTR"])
        print("\n=== CALIBRAZIONE (tre esiti impilati) ===")
        print(calibration_table(p.reshape(-1), _onehot(y).reshape(-1)).round(4).to_string(index=False))
    else:
        print(f"\nservono almeno 50 previsioni risolte per una curva di calibrazione "
              f"leggibile (ora {len(resolved)}).")

    print("\n=== ULTIME PREVISIONI RISOLTE ===")
    ultime = resolved.sort_values("match_date").tail(10).copy()
    ultime["risultato"] = (ultime["FTHG"].astype("Int64").astype(str) + "-"
                           + ultime["FTAG"].astype("Int64").astype(str))
    ultime["rps"] = rps(ultime[PROB_COLS].to_numpy(dtype=float), outcome_index(ultime["FTR"]))
    print(ultime[["match_date", "home_team", "away_team", "p_home", "p_draw", "p_away",
                  "risultato", "FTR", "rps"]].round(4).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Track record delle previsioni registrate")
    ap.add_argument("--by-season", action="store_true", help="metriche stagione per stagione")
    ap.add_argument("--pending", action="store_true", help="elenca le previsioni non ancora risolte")
    ap.add_argument("--backfill", action="store_true",
                    help="legge predictions_backfill.csv invece del track record. "
                         "Serve a provare la pipeline, NON e' un track record: "
                         "quelle previsioni sono ricostruite a posteriori")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    from .predict import BACKFILL_LOG
    path = BACKFILL_LOG if args.backfill else PREDICTIONS_LOG
    try:
        report(by_season=args.by_season, show_pending=args.pending, path=path)
    except FileNotFoundError as exc:
        # Registro assente e' uno stato normale finche' non si e' predetto
        # nulla, non un errore da traceback.
        log.warning("%s", exc)


if __name__ == "__main__":
    main()
