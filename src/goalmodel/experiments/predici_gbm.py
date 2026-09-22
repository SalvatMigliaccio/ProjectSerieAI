"""
La giornata in arrivo predetta con M5, il GBM ancorato al mercato — FUORI dal
percorso di produzione e SENZA scrivere nel registro.

COSA FA E COSA NON FA. Riusa `src/predict.py` cosi' com'e', passandogli un
modello diverso e `dry_run=True`: stesso calendario, stesse quote, stesse
feature, stesse protezioni sul calcio d'inizio. Nessuna riga entra in
`predictions_log.csv`, nessun file di produzione viene toccato, e la guardia di
`experiments` lo impone invece di limitarsi a promettere.

PERCHE' NON E' IL MODELLO DI PRODUZIONE, E PERCHE' NON DEVE DIVENTARLO OGGI.
Sul test set M5 e' **indistinguibile dal mercato**: +0.00011 di RPS con IC
[-0.00056, +0.00077]. Non e' peggiore, non e' migliore. Il criterio di
promozione scritto in CLAUDE.md chiede che un modello batta il mercato con
intervallo che non tocca lo zero, dopo correzione per confronti multipli:
M5 non lo fa, quindi `predict_round` resta su M1. Mettere in produzione un
modello misurato indistinguibile significherebbe buttare via la misura.

A COSA SERVE ALLORA. A guardare **dove** M5 si discosta dal mercato su partite
vere. E' la stessa idea della sezione 3 del report — diagnostica, non un
segnale di scommessa — ma con il modello giusto: il report usa M4 senza
mercato, che parte da zero e diverge ovunque; M5 parte dal mercato e si muove
solo dove le feature di forma dicono qualcosa. Uno scarto grande qui e' una
partita da capire, non un'occasione da giocare. Il modello non produce valore
atteso positivo: vedi la sezione dedicata in CLAUDE.md.

QUALE M5. `M5MediaSemi(["BASE"])`: le 52 colonne congelate di BASE, media
geometrica dei lambda di cinque semi. I set sperimentali non entrano —
GIOCATORI e' provvisorio e per una partita futura le assenze non sono nemmeno
nel dataset (servirebbe `ingest --stage missing` sul turno in arrivo), quindi
un M5 con quelle colonne girerebbe con NaN dove in addestramento aveva dati.
La media dei semi e' la stima dichiarata: un seme solo sposta i lambda di qualche
millesimo, e non ha senso che la previsione mostrata dipenda da quale.

Uso:
    python -m goalmodel.experiments.predici_gbm            # prima giornata utile
    python -m goalmodel.experiments.predici_gbm --matchday 5
    python -m goalmodel.experiments.predici_gbm --semi 0   # un seme solo, piu' veloce
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import load_dataset
from ..models.baseline import fair_odds, predictions_from_lambdas
from ..prediction import predict as predict_mod
from .modelli import M5MediaSemi

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("predici_gbm")


def addestra(semi: tuple[int, ...], cutoff: pd.Timestamp | None = None) -> M5MediaSemi:
    """
    M5 addestrato su tutto lo storico giocato, come farebbe il walk-forward.

    Stesso perimetro della valutazione: partite con risultato, burn-in escluso.
    Non c'e' niente da tarare — gli iperparametri sono quelli di
    `config.GBM_PARAMS_ANCHORED`, fissati sulla validazione — e il numero di
    alberi lo decide l'arresto anticipato sulla coda del training.

    `cutoff` serve con `--as-of`: rifare una giornata gia' giocata addestrando
    su tutto lo storico significherebbe addestrare anche su quella giornata, e
    la "previsione" conterrebbe il risultato che dice di prevedere. Il taglio
    e' sulla data, come nel walk-forward, non sulla giornata.
    """
    df = load_dataset()
    train = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    if cutoff is not None:
        limite = cutoff.tz_convert(None) if cutoff.tz is not None else cutoff
        train = train[pd.to_datetime(train["date"]) < limite]
        log.info("taglio del training a %s (--as-of)", limite.date())
    log.info("addestro su %d partite giocate, %d semi", len(train), len(semi))
    modello = M5MediaSemi(["BASE"], semi=semi).fit(train)
    alberi = [m.best_iters_ for m in modello.modelli if m.model_home_ is not None]
    log.info("alberi per seme (casa, fuori): %s", alberi)
    if not alberi:
        raise SystemExit("modello non addestrato: training troppo corto")
    return modello


def confronta(preds: pd.DataFrame) -> pd.DataFrame:
    """
    M5 accanto a M1 sulle stesse righe, con lo scarto.

    M1 non si ricalcola con un secondo giro di `predict.run`: i lambda del
    mercato sono gia' nelle colonne `mkt_lambda_*` del frame, e M1 *e'* la
    matrice dei risultati costruita su quei due numeri. Rifare la previsione
    da capo aggiungerebbe solo un'occasione di divergere.
    """
    m1 = predictions_from_lambdas(
        preds["mkt_lambda_home"].to_numpy(float),
        preds["mkt_lambda_away"].to_numpy(float),
        index=preds.index, rho=config.DC_RHO,
    )
    out = pd.DataFrame({
        # Chiave, orario e (piu' sotto) istante della previsione. Senza, il file
        # salvato si legge ma non si aggancia ai risultati, e "abbiamo
        # indovinato?" resta un lavoro a mano. La quadrupla e' la stessa del
        # resto del progetto; la data resta un controllo, non la chiave.
        "season": preds["season"].astype(str),
        "matchday": preds["matchday"].astype(int),
        "home_team": preds["home_team"],
        "away_team": preds["away_team"],
        "kickoff_utc": preds.get("kickoff", pd.NaT),
        "data": pd.to_datetime(preds["date"]).dt.strftime("%a %d/%m"),
        "partita": preds["home_team"] + " - " + preds["away_team"],
        "1 mkt": m1["p_home"], "1 M5": preds["p_home"],
        "X mkt": m1["p_draw"], "X M5": preds["p_draw"],
        "2 mkt": m1["p_away"], "2 M5": preds["p_away"],
        "lam casa mkt": m1["lambda_home"], "lam casa M5": preds["lambda_home"],
        "lam fuori mkt": m1["lambda_away"], "lam fuori M5": preds["lambda_away"],
    })
    out["scarto max 1X2"] = (
        pd.concat([(out["1 M5"] - out["1 mkt"]).abs(),
                   (out["X M5"] - out["X mkt"]).abs(),
                   (out["2 M5"] - out["2 mkt"]).abs()], axis=1).max(axis=1)
    )
    # La quota equa di M5 accanto a quella reale del book: serve a LEGGERE la
    # quota, non a prometterle un vantaggio. Con M1 la reale sta sempre sotto
    # l'equa, per aritmetica del margine.
    out["equa 1 M5"] = fair_odds(preds["p_home"])
    out["quota 1"] = preds["odds_home"]
    return out.sort_values("scarto max 1X2", ascending=False)


def verifica(season: str | None = None, matchday: int | None = None) -> None:
    """
    A risultati usciti: le previsioni salvate contro quello che e' successo.

    PERCHE' NON NEL REGISTRO. Il track record e' di M1 ed e' l'unico dato che
    non si rigenera: mettere una seconda riga per partita significherebbe che
    `close_round` archivia due previsioni per ogni partita e che la giornata
    chiusa non e' piu' una sola cosa. Qui le previsioni di M5 stanno nei propri
    file, con dentro l'istante in cui sono state scritte — la sola cosa che a
    risultati usciti distingue una stima da un commento.

    IL CONFRONTO E' APPAIATO, sempre sulle STESSE partite: M5 e M1 valutati su
    righe diverse non si possono confrontare, e la differenza riga per riga
    toglie di mezzo la difficolta' della giornata. E' lo stesso metro di
    `evaluate.paired_comparison`, senza il bootstrap: con dieci partite un
    intervallo di confidenza sarebbe un ornamento.
    """
    from ..evaluate import load_dataset, outcome_index, rps

    files = sorted(config.ROOT.glob("experiments/output/previsioni_m5_*.csv"))
    if not files:
        print("nessuna previsione salvata: lancia prima "
              "'python -m goalmodel.experiments.predici_gbm'")
        return

    richieste = {"season", "matchday", "home_team", "away_team"}
    pezzi, saltati = [], []
    for f in files:
        d = pd.read_csv(f)
        (pezzi if richieste <= set(d.columns) else saltati).append(
            d if richieste <= set(d.columns) else f.name)
    if saltati:
        # Fonderli lo stesso darebbe righe con la chiave a NaN, che il merge
        # non aggancia: comparirebbero per sempre come "non ancora giocate".
        print(f"saltati {len(saltati)} file salvati prima che il formato avesse "
              f"la chiave: {', '.join(saltati)}")
        print("rilancia quella giornata con --as-of per riscriverli nel formato nuovo.\n")
    if not pezzi:
        print("nessuna previsione salvata nel formato con la chiave.")
        return

    salvate = pd.concat(pezzi, ignore_index=True)
    salvate["season"] = salvate["season"].astype(str)
    if season:
        salvate = salvate[salvate["season"] == str(season)]
    if matchday:
        salvate = salvate[salvate["matchday"] == int(matchday)]
    if salvate.empty:
        print("nessuna previsione salvata per il perimetro richiesto")
        return

    veri = load_dataset()[["season", "home_team", "away_team", "FTR", "FTHG", "FTAG"]]
    veri["season"] = veri["season"].astype(str)
    d = salvate.merge(veri, on=["season", "home_team", "away_team"], how="left")

    giocate = d[d["FTR"].notna()].copy()
    if giocate.empty:
        print(f"{len(d)} previsioni salvate, nessuna ancora giocata.")
        return

    y = outcome_index(giocate["FTR"])
    for nome, colonne in (("M1", ["1 mkt", "X mkt", "2 mkt"]),
                          ("M5", ["1 M5", "X M5", "2 M5"])):
        p = giocate[colonne].to_numpy(float)
        giocate[f"rps {nome}"] = rps(p, y)
        # L'esito previsto e' il piu' probabile dei tre, nell'ordine H, D, A.
        giocate[f"scelta {nome}"] = [["H", "D", "A"][i] for i in p.argmax(axis=1)]
        giocate[f"ok {nome}"] = giocate[f"scelta {nome}"] == giocate["FTR"]

    print(f"\n=== M5 contro M1 su {len(giocate)} partite gia' giocate ===\n")
    vista = giocate.assign(
        risultato=giocate["FTHG"].astype(int).astype(str) + "-"
                  + giocate["FTAG"].astype(int).astype(str),
        esito=giocate["FTR"],
    )[["matchday", "partita", "risultato", "esito",
       "scelta M1", "ok M1", "rps M1", "scelta M5", "ok M5", "rps M5"]]
    print(vista.round(4).to_string(index=False))

    diff = giocate["rps M5"] - giocate["rps M1"]
    print(f"\nRPS medio     M1 {giocate['rps M1'].mean():.4f}   "
          f"M5 {giocate['rps M5'].mean():.4f}")
    print(f"esiti 1X2     M1 {int(giocate['ok M1'].sum())} su {len(giocate)}   "
          f"M5 {int(giocate['ok M5'].sum())} su {len(giocate)}")
    print(f"differenza appaiata M5 - M1: {diff.mean():+.5f} "
          f"({int((diff < 0).sum())} partite su {len(diff)} a favore di M5)")

    ancora = int(d["FTR"].isna().sum())
    if ancora:
        print(f"\n{ancora} previsioni salvate non sono ancora giocate.")
    print("\nDIECI PARTITE NON DECIDONO NIENTE. Sul test set (1140 partite) la "
          "differenza\nfra i due e' +0.00011 con IC [-0.00056, +0.00077]: "
          "indistinguibili. Questo conto\nserve a vedere cosa e' successo, non "
          "a promuovere un modello.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="La giornata in arrivo secondo M5 (GBM ancorato). Non scrive nel registro.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--matchday", type=int, help="giornata da predire")
    g.add_argument("--next", action="store_true", help="prima giornata utile (default)")
    ap.add_argument("--semi", type=int, nargs="+", default=[0, 1, 2, 3, 4],
                    help="semi da mediare (default: cinque)")
    ap.add_argument("--team", help="filtra su una squadra")
    ap.add_argument("--as-of", help="finge che 'adesso' sia questa data UTC (YYYY-MM-DD), "
                                    "per rifare una giornata gia' giocata con le quote di "
                                    "allora. Resta un dry-run: non scrive nemmeno nel "
                                    "file di backfill")
    ap.add_argument("--verifica", action="store_true",
                    help="non predice: confronta le previsioni gia' salvate con i "
                         "risultati usciti")
    args = ap.parse_args()

    experiments.proteggi_produzione()

    if args.verifica:
        verifica(matchday=args.matchday)
        return

    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)

    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else None
    modello = addestra(tuple(args.semi), cutoff=as_of)
    esito = predict_mod.run(
        matchday=args.matchday,
        team=args.team,
        use_next=args.matchday is None,
        as_of=as_of,
        model=modello,
        dry_run=True,      # non negoziabile: qui non si scrive nel registro
        quiet=True,
    )

    if esito.preds.empty:
        print("\nnessuna partita predicibile: "
              f"{len(esito.skipped)} senza quote di apertura complete.")
        if not esito.skipped.empty:
            for _, r in esito.skipped.iterrows():
                print(f"  {r['home_team']} - {r['away_team']}: manca {r.get('manca', 'quote')}")
        return

    tab = confronta(esito.preds)
    print(f"\n=== GIORNATA {esito.matchday} — M5 GBM ancorato contro il mercato (M1) ===")
    print(f"    {esito.model_version}\n")
    print(tab.drop(columns=["season", "matchday", "home_team", "away_team",
                            "kickoff_utc"]).round(3).to_string(index=False))
    print(f"\nscarto massimo sull'1X2: {tab['scarto max 1X2'].max():.4f} "
          f"(mediano {tab['scarto max 1X2'].median():.4f})")
    print("\nDIAGNOSTICA, NON UN SEGNALE DI SCOMMESSA. Sul test set M5 e'"
          " indistinguibile dal mercato\n(+0.00011, IC [-0.00056, +0.00077]):"
          " uno scarto grande e' una partita da capire,\nnon un'occasione."
          " Il registro resta su M1 e questa passata non lo ha toccato.")

    dst = experiments.percorso(
        f"previsioni_m5_{esito.preds['season'].iloc[0]}_{esito.matchday:02d}.csv")
    # L'istante della previsione vale quanto la previsione: a risultati usciti
    # e' l'unica cosa che distingue una stima da un commento.
    tab.insert(0, "timestamp_prediction",
               (esito.now or pd.Timestamp.now(tz="UTC")).isoformat())

    # IL FILE SI UNISCE, NON SI SOVRASCRIVE, e non e' un dettaglio. Questo
    # comando gira piu' volte nel fine settimana e `predict.run` prevede solo
    # le partite ANCORA DA GIOCARE: la passata del sabato mattina, riscrivendo,
    # cancellerebbe la partita del venerdi' sera che aveva gia' previsto.
    # Stessa regola del registro: a parita' di partita vince la riga piu'
    # VECCHIA, perche' e' quella scritta con meno informazione.
    chiave = ["season", "matchday", "home_team", "away_team"]
    if dst.exists():
        prima = pd.read_csv(dst)
        prima["season"] = prima["season"].astype(str)
        if set(chiave) <= set(prima.columns):
            unione = pd.concat([prima, tab], ignore_index=True)
            unione = unione.sort_values("timestamp_prediction")
            tab = unione.drop_duplicates(subset=chiave, keep="first")
            nuove = len(tab) - len(prima)
            if nuove > 0:
                print(f"\n{nuove} partite aggiunte a quelle gia' salvate")
            elif len(tab) == len(prima):
                print("\nnessuna partita nuova: il file resta com'era")

    tab.to_csv(dst, index=False)
    print(f"\nscritto {dst}")
    print("a risultati usciti:  python -m goalmodel.experiments.predici_gbm --verifica")


if __name__ == "__main__":
    main()
