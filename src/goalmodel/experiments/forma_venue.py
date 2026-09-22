"""
Forma condizionata alla sede: come rende una squadra GIOCANDO IN CASA, e come
rende GIOCANDO IN TRASFERTA. Misurata solo in validazione.

LA DOMANDA. BASE ha la forma generale della squadra di casa e di quella in
trasferta, calcolata su tutte le loro partite. Nessuna colonna dice come una
squadra rende specificamente nella sede in cui gioca oggi: il vantaggio
casalingo, nel modello, e' uniforme — lo stesso per l'Atalanta a Bergamo e per
una neopromossa che in casa non vince mai. `config.FORM_HALFLIFE_VENUE = 10`
era stato dichiarato per questo e mai usato: la half-life e' piu' lunga di
quella di BASE (6) perche' restringendo alla sede i campioni si dimezzano.

LE COLONNE, FISSATE PRIMA DI MISURARE
  home_<stat>_<verso>_ewm_sede   la squadra di casa, sulle sue sole partite in casa
  away_<stat>_<verso>_ewm_sede   la squadra in trasferta, sulle sole in trasferta
  diff_<stat>_<verso>_ewm_sede   la differenza
Stesse 8 statistiche e stessi due versi di BASE: 48 colonne. Half-life
`FORM_HALFLIFE_VENUE` (10), non tarata. Medie di lega per la regressione al
confine di stagione calcolate PER SEDE, perche' in casa si segna di piu' e
tirare la forma casalinga verso la media di tutte le partite la sporcherebbe.
Stessa regressione del 30%. Nessun contatore di partite per sede: il set
aggiunge forma, non numerosita'.

NON TOCCA `features/form.py`. Ne riusa le funzioni pure (`to_long`,
`league_means`, `ewma_by_team`) senza modificarle e le applica a meta' del
formato lungo. Le colonne vivono solo dentro il processo dell'esperimento: non
esiste un parquet di queste feature, e nessuna scrittura in `data/processed/`.

PERCHE' IN VALIDAZIONE E NON SUL TEST. Il test confermativo e' una risorsa
finita: ogni confronto alza la soglia per tutti i risultati futuri (m = 3 oggi).
Qui si esplora sulle stagioni di validazione, come per collinearita' e baseline.

REGOLA DI LETTURA, SCRITTA PRIMA DEI RISULTATI
  stima       media dei 5 semi (log-lambda), M5 BASE+FORMA_VENUE contro M5 BASE
  nulla       intervallo che contiene lo zero, o differenza >= 0: il set e'
              SCARTATO e la questione e' chiusa a costo zero
  qualcosa    intervallo tutto sotto zero: il set diventa un'IPOTESI
              PRE-REGISTRATA per il test confermativo futuro (test set cresciuto
              o Big 5), con specifica congelata com'e' qui. NON un blocco tenuto
              e nessuna promozione: la validazione non e' il test.
I singoli semi si riportano per informazione, non decidono.

Uso:
    python -m goalmodel.experiments.forma_venue --descrivi
    python -m goalmodel.experiments.forma_venue --lancia
    python -m goalmodel.experiments.forma_venue --analizza
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import KEYS, load_dataset, paired_pairs, walk_forward
from ..features import form
from ..features import sets as sets_mod
from ..models.baseline import PRED_COLS
from .modelli import M5Set, media_log_lambda

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("forma_venue")

SEMI = (0, 1, 2, 3, 4)
VARIANTI = {"base": ("BASE",), "sede": ("BASE", "FORMA_VENUE")}
COLONNE = KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR", "model"] + PRED_COLS
SUFFISSO = "_ewm_sede"


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------

def costruisci(master: pd.DataFrame, halflife: float = config.FORM_HALFLIFE_VENUE) -> pd.DataFrame:
    """KEYS + le colonne di forma per sede, una riga per partita."""
    long = form.to_long(master)
    value_cols = [c for c in long.columns if c.endswith(("_for", "_against"))]

    lati = []
    for sede in ("home", "away"):
        # Mezzo formato lungo: la squadra solo nelle partite giocate in questa
        # sede. `ewma_by_team` scrive lo stato PRIMA di aggiornarlo, quindi la
        # riga riceve la forma delle partite precedenti nella stessa sede.
        sub = long[long["venue"] == sede].reset_index(drop=True)
        medie = form.league_means(sub, value_cols)
        stato = form.ewma_by_team(sub, value_cols, medie, halflife=halflife,
                                  regression=config.SEASON_REGRESSION)
        parte = pd.concat([sub[KEYS], stato[value_cols]], axis=1)
        lati.append(parte.rename(columns={c: f"{sede}_{c}{SUFFISSO}" for c in value_cols})
                    .set_index(KEYS))

    out = lati[0].join(lati[1], how="outer").reset_index()
    for c in value_cols:
        out[f"diff_{c}{SUFFISSO}"] = out[f"home_{c}{SUFFISSO}"] - out[f"away_{c}{SUFFISSO}"]
    return out


def dataset() -> pd.DataFrame:
    """Il dataset di valutazione con le colonne per sede agganciate in memoria."""
    df = load_dataset()
    master = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    master["season"] = master["season"].astype(str)
    sede = costruisci(master)
    attese = list(sets_mod.SETS["FORMA_VENUE"].colonne)
    prodotte = [c for c in sede.columns if c not in KEYS]
    if sorted(prodotte) != sorted(attese):
        raise AssertionError("le colonne prodotte non coincidono con il set registrato: "
                             f"{sorted(set(prodotte) ^ set(attese))[:6]}")
    df["season"] = df["season"].astype(str)
    return df.merge(sede, on=KEYS, how="left", validate="one_to_one")


def descrivi() -> None:
    df = dataset()
    cols = list(sets_mod.SETS["FORMA_VENUE"].colonne)
    for nome, stagioni in (("stima (prima della validazione)", None),
                           ("validazione", config.VALIDATION_SEASONS)):
        sub = df[df["season"].isin(stagioni)] if stagioni else \
            df[df["season"] < min(config.VALIDATION_SEASONS)]
        print(f"{nome}: {len(sub)} partite, righe con forma per sede completa "
              f"{sub[cols].notna().all(axis=1).mean():.1%}")
    # Quanto e' nuova l'informazione: correlazione con la colonna gemella di BASE.
    val = df[df["season"].isin(config.VALIDATION_SEASONS)]
    print("\ncorrelazione con la gemella di BASE, in validazione:")
    for stat in ("goals_for", "np_xg_for", "xpts_for", "points_for"):
        for lato in ("home", "away"):
            r = val[f"{lato}_{stat}_ewm"].corr(val[f"{lato}_{stat}{SUFFISSO}"])
            print(f"  {lato}_{stat:<12} r = {r:.3f}")


# ---------------------------------------------------------------------------
# Misura
# ---------------------------------------------------------------------------

def file_uscita(variante: str, seme: int):
    return experiments.percorso(f"wf_sede_validazione_{variante}_s{seme}.parquet")


def esegui(seme: int) -> None:
    experiments.proteggi_produzione()
    df = dataset()
    modelli = [M5Set(sets, seed=seme, etichetta=v) for v, sets in VARIANTI.items()]
    preds = walk_forward(df, modelli, test_seasons=config.VALIDATION_SEASONS)
    for v, m in zip(VARIANTI, modelli):
        preds[preds["model"] == m.name][COLONNE].to_parquet(file_uscita(v, seme), index=False)
    log.info("seme %d scritto", seme)


def lancia() -> None:
    """Un processo per seme, tutti insieme: LightGBM qui usa un thread solo."""
    coda = [s for s in SEMI if not all(file_uscita(v, s).exists() for v in VARIANTI)]
    processi = []
    for s in coda:
        fh = open(experiments.percorso(f"log_sede_s{s}.txt"), "w", encoding="utf-8")
        processi.append((s, fh, subprocess.Popen(
            [sys.executable, "-m", "goalmodel.experiments.forma_venue", "--seed", str(s)],
            stdout=fh, stderr=subprocess.STDOUT, cwd=config.ROOT)))
    for s, fh, p in processi:
        p.wait()
        fh.close()
        print(f"seme {s}: {'ok' if p.returncode == 0 else 'FALLITO'}", flush=True)


def _media(variante: str) -> pd.DataFrame:
    pezzi = [pd.read_parquet(file_uscita(variante, s)).sort_values(KEYS).reset_index(drop=True)
             for s in SEMI]
    base = pezzi[0]
    for p in pezzi[1:]:
        if not p[KEYS].equals(base[KEYS]):
            raise ValueError("semi con righe diverse: non si mediano")
    out = base[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]].copy()
    out["model"] = f"media5 {variante}"
    return pd.concat([out, media_log_lambda([p[PRED_COLS] for p in pezzi], base.index)], axis=1)


def _confronto(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    r = paired_pairs(pd.concat([a, b], ignore_index=True),
                     [(a["model"].iloc[0], b["model"].iloc[0])]).iloc[0]
    return {"differenza": r["differenza"], "ic_basso": r["ic_basso"],
            "ic_alto": r["ic_alto"], "quota_peggiore": r["quota_peggiore"]}


def importanza(df: pd.DataFrame) -> pd.DataFrame:
    """Quota di guadagno del set, addestrando fino alla prima stagione di validazione."""
    played = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    cutoff = df.loc[df["season"] == min(config.VALIDATION_SEASONS), "date"].min()
    nuove = set(sets_mod.SETS["FORMA_VENUE"].colonne)
    righe = []
    for seme in SEMI:
        m = M5Set(VARIANTI["sede"], seed=seme).fit(played[played["date"] < cutoff])
        for lato, mod in (("casa", m.model_home_), ("fuori", m.model_away_)):
            gain = pd.Series(mod.booster_.feature_importance(importance_type="gain"),
                             index=m.features_)
            gain = gain / gain.sum()
            righe.append({"seme": seme, "lato_modello": lato,
                          "quota_set": gain[gain.index.isin(nuove)].sum(),
                          "quota_colonne": len(nuove) / len(gain)})
    return pd.DataFrame(righe)


def analizza() -> None:
    mancanti = [(v, s) for v in VARIANTI for s in SEMI if not file_uscita(v, s).exists()]
    if mancanti:
        raise SystemExit(f"mancano {len(mancanti)} walk-forward: {mancanti}")
    righe = []
    print(f"validazione {config.VALIDATION_SEASONS} — test non toccato, m resta 3")
    print("\n=== SEMI: M5 BASE+FORMA_VENUE - M5 BASE (informativi) ===")
    for s in SEMI:
        c = _confronto(pd.read_parquet(file_uscita("sede", s)),
                       pd.read_parquet(file_uscita("base", s)))
        print(f"  seme {s}: {c['differenza']:+.5f}  IC [{c['ic_basso']:+.5f}, "
              f"{c['ic_alto']:+.5f}]  p={c['quota_peggiore']:.4f}")
        righe.append({"verifica": f"seme {s}", **c})

    c = _confronto(_media("sede"), _media("base"))
    print("\n=== MEDIA DEI 5 SEMI — la stima che decide ===")
    print(f"  {c['differenza']:+.5f}  IC [{c['ic_basso']:+.5f}, {c['ic_alto']:+.5f}]  "
          f"p={c['quota_peggiore']:.4f}")
    esito = ("QUALCOSA: ipotesi pre-registrata per il test futuro"
             if c["ic_alto"] < 0 else "NULLA: set scartato, chiuso a costo zero")
    print(f"  esito secondo la regola dichiarata: {esito}")
    righe.append({"verifica": "media5", **c, "esito": esito})

    imp = importanza(dataset())
    print("\n=== IMPORTANZA DEL SET (training fino alla validazione) ===")
    print(f"  quota di guadagno {imp['quota_set'].mean():.1%} con il "
          f"{imp['quota_colonne'].iloc[0]:.1%} delle colonne "
          f"(per seme da {imp.groupby('seme')['quota_set'].mean().min():.1%} "
          f"a {imp.groupby('seme')['quota_set'].mean().max():.1%})")
    righe.append({"verifica": "importanza", "quota_set": imp["quota_set"].mean(),
                  "quota_colonne": imp["quota_colonne"].iloc[0]})
    pd.DataFrame(righe).to_csv(experiments.percorso("riassunto_forma_venue_validazione.csv"),
                               index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Forma condizionata alla sede, in validazione")
    ap.add_argument("--descrivi", action="store_true")
    ap.add_argument("--lancia", action="store_true", help="i 5 semi in parallelo")
    ap.add_argument("--analizza", action="store_true")
    ap.add_argument("--seed", type=int, help="un seme solo (lo usa --lancia)")
    args = ap.parse_args()
    experiments.proteggi_produzione()
    if args.seed is not None:
        esegui(args.seed)
    elif args.lancia:
        lancia()
    elif args.analizza:
        analizza()
    elif args.descrivi:
        descrivi()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
