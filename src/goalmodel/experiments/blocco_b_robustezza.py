"""
Blocco B: verifiche di robustezza. NON sono test nuovi, e questo conta.

LA REGOLA DI LETTURA, SCRITTA PRIMA DEI RISULTATI
Queste verifiche possono solo DECLASSARE il blocco, mai promuoverlo. Il
verdetto statistico resta quello pre-registrato (p = 0.0225, che non
sopravvive a Bonferroni con m = 3). Se una variante qui desse un intervallo
piu' netto, non diventerebbe un risultato: sarebbe una specifica scelta dopo
aver visto i dati, cioe' esattamente un confronto in piu'. Si usano in una
direzione sola.

COSA SI MISURA
  simmetria   BASE+GIOCATORI senza `home_quota_minuti_assenti` e
              `away_quota_minuti_assenti`: resta solo la differenza. Un
              effetto calcistico reale fra casa e trasferta starebbe intorno a
              1.5:1, non 20:1. Se il guadagno di -0.00027 evapora togliendo le
              due colonne separate, il blocco poggiava su una colonna e su un
              caso, e il set GIOCATORI va marcato come scartato.
  semi        la stessa misura con cinque semi di LightGBM. Se i valori
              attraversano lo zero, il risultato non e' stabile.
  media       la previsione mediata sui cinque semi, per ciascuna variante.
              Non aggiunge informazione: toglie la varianza del campionamento
              di righe e colonne.

COME GIRA
Ogni coppia (variante, seme) e' un walk-forward completo — ~20 minuti — e gira
in un processo suo. `--lancia` li distribuisce su piu' processi: LightGBM qui
usa un solo thread (`n_jobs=1`), quindi otto processi su dieci core non si
pestano i piedi. Un processo gia' concluso non si rifa': il suo file c'e'.

Scrive solo in `experiments/output/`. La guardia di `experiments` lo impone.

Uso:
    python -m goalmodel.experiments.blocco_b_robustezza --lancia --paralleli 8
    python -m goalmodel.experiments.blocco_b_robustezza --analizza
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from .. import config, experiments
from ..evaluation.evaluate import KEYS, PROB_COLS, load_dataset, paired_pairs, walk_forward
from ..models.baseline import PRED_COLS
from .modelli import M5Set, media_log_lambda

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("bloccoB")

SIMMETRICHE_TOLTE = ("home_quota_minuti_assenti", "away_quota_minuti_assenti")

VARIANTI = {
    "senza": dict(sets=("BASE",), togli=()),
    "con": dict(sets=("BASE", "GIOCATORI"), togli=()),
    "simmetrico": dict(sets=("BASE", "GIOCATORI"), togli=SIMMETRICHE_TOLTE),
}
SEMI = (0, 1, 2, 3, 4)
COLONNE = KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR", "model"] + PRED_COLS

STORICO = config.PROCESSED / "walk_forward_blocco_giocatori.parquet"
MERCATO = "M1b market-only (diretto)"


def file_uscita(variante: str, seme: int):
    return experiments.percorso(f"wf_bloccoB_{variante}_s{seme}.parquet")


def esegui(variante: str, seme: int) -> None:
    experiments.proteggi_produzione()
    df = load_dataset()
    modello = M5Set(etichetta=variante, seed=seme, **VARIANTI[variante])
    log.info("walk-forward: %s", modello.name)
    preds = walk_forward(df, [modello])
    preds[COLONNE].to_parquet(file_uscita(variante, seme), index=False)
    log.info("scritto %s", file_uscita(variante, seme).name)


def lancia(paralleli: int) -> None:
    coda = [(v, s) for v in VARIANTI for s in SEMI if not file_uscita(v, s).exists()]
    log.info("da eseguire: %d su %d, fino a %d in parallelo",
             len(coda), len(VARIANTI) * len(SEMI), paralleli)
    attivi = []
    t0 = time.time()
    while coda or attivi:
        while coda and len(attivi) < paralleli:
            v, s = coda.pop(0)
            fh = open(experiments.percorso(f"log_bloccoB_{v}_s{s}.txt"), "w",
                      encoding="utf-8")
            p = subprocess.Popen(
                [sys.executable, "-m", "src.experiments.blocco_b_robustezza",
                 "--variante", v, "--seed", str(s)],
                stdout=fh, stderr=subprocess.STDOUT, cwd=config.ROOT,
            )
            attivi.append((p, v, s, fh))
        time.sleep(15)
        for voce in list(attivi):
            p, v, s, fh = voce
            if p.poll() is None:
                continue
            fh.close()
            attivi.remove(voce)
            esito = "ok" if p.returncode == 0 and file_uscita(v, s).exists() else "FALLITO"
            print(f"[{(time.time()-t0)/60:5.1f} min] {v} s{s}: {esito}", flush=True)
    print("LANCIO CONCLUSO", flush=True)


# ---------------------------------------------------------------------------
# Analisi
# ---------------------------------------------------------------------------

def _carica(variante: str, seme: int) -> pd.DataFrame:
    return pd.read_parquet(file_uscita(variante, seme))


def _media(variante: str) -> pd.DataFrame:
    """La media dei cinque semi, ricostruita esatta dalle previsioni salvate."""
    pezzi = [_carica(variante, s).sort_values(KEYS).reset_index(drop=True)
             for s in SEMI]
    base = pezzi[0]
    for p in pezzi[1:]:
        assert p[KEYS].equals(base[KEYS]), "semi con righe diverse: non si mediano"
    media = media_log_lambda([p[PRED_COLS] for p in pezzi], base.index)
    out = base[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]].copy()
    out["model"] = f"media5 {variante}"
    return pd.concat([out, media], axis=1)


def _confronto(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    riga = paired_pairs(pd.concat([a, b], ignore_index=True),
                        [(a["model"].iloc[0], b["model"].iloc[0])]).iloc[0]
    return {"differenza": riga["differenza"], "ic_basso": riga["ic_basso"],
            "ic_alto": riga["ic_alto"], "quota_peggiore": riga["quota_peggiore"]}


def analizza() -> None:
    mancanti = [(v, s) for v in VARIANTI for s in SEMI if not file_uscita(v, s).exists()]
    if mancanti:
        raise SystemExit(f"mancano {len(mancanti)} walk-forward: {mancanti}")

    righe = []

    # 0. Determinismo: il seme 0 rifatto qui deve coincidere con la misura
    #    originale del blocco B. Se non coincide, niente di cio' che segue e'
    #    confrontabile con il -0.00027 di partenza.
    if STORICO.exists():
        vecchio = pd.read_parquet(STORICO)
        for variante, nome in (("con", "M5 GBM ancorato al mercato (con giocatori)"),
                               ("senza", "M5 GBM ancorato al mercato (senza giocatori)")):
            v = vecchio[vecchio["model"] == nome].set_index(KEYS)[PROB_COLS]
            n = _carica(variante, 0).set_index(KEYS)[PROB_COLS]
            comuni = v.index.intersection(n.index)
            scarto = float(np.nanmax(np.abs(v.loc[comuni].to_numpy()
                                            - n.loc[comuni].to_numpy())))
            print(f"determinismo {variante} s0 contro la misura originale: "
                  f"scarto massimo {scarto:.2e} su {len(comuni)} partite")
            righe.append({"verifica": f"determinismo {variante} s0",
                          "scarto_massimo": scarto})

    # 1. Semi: con contro senza, stesso seme.
    print("\n=== SEMI: M5+GIOCATORI - M5, seme per seme ===")
    for s in SEMI:
        c = _confronto(_carica("con", s), _carica("senza", s))
        print(f"  seme {s}: {c['differenza']:+.5f}  IC [{c['ic_basso']:+.5f}, "
              f"{c['ic_alto']:+.5f}]  p={c['quota_peggiore']:.4f}")
        righe.append({"verifica": f"semi con-senza s{s}", **c})

    # 2. Simmetria, seme per seme.
    print("\n=== SIMMETRIA: BASE+GIOCATORI senza le due colonne separate - M5 ===")
    for s in SEMI:
        c = _confronto(_carica("simmetrico", s), _carica("senza", s))
        print(f"  seme {s}: {c['differenza']:+.5f}  IC [{c['ic_basso']:+.5f}, "
              f"{c['ic_alto']:+.5f}]  p={c['quota_peggiore']:.4f}")
        righe.append({"verifica": f"simmetria simm-senza s{s}", **c})

    # 3. Medie dei semi.
    medie = {v: _media(v) for v in VARIANTI}
    print("\n=== MEDIA DEI 5 SEMI ===")
    for a, b in (("con", "senza"), ("simmetrico", "senza"), ("simmetrico", "con")):
        c = _confronto(medie[a], medie[b])
        print(f"  media {a} - media {b}: {c['differenza']:+.5f}  "
              f"IC [{c['ic_basso']:+.5f}, {c['ic_alto']:+.5f}]  "
              f"p={c['quota_peggiore']:.4f}")
        righe.append({"verifica": f"media5 {a}-{b}", **c})

    # 4. Contro il mercato: il criterio di promozione, non un test del blocco.
    if STORICO.exists():
        vecchio = pd.read_parquet(STORICO)
        mercato = vecchio[vecchio["model"] == MERCATO][COLONNE]
        if not mercato.empty:
            print("\n=== CONTRO IL MERCATO (criterio di promozione) ===")
            for v in ("con", "simmetrico"):
                c = _confronto(medie[v], mercato)
                print(f"  media {v} - mercato: {c['differenza']:+.5f}  "
                      f"IC [{c['ic_basso']:+.5f}, {c['ic_alto']:+.5f}]")
                righe.append({"verifica": f"media5 {v}-mercato", **c})

    tab = pd.DataFrame(righe)
    dst = experiments.percorso("riassunto_bloccoB_robustezza.csv")
    tab.to_csv(dst, index=False)
    print(f"\nscritto {dst}")


def importanza() -> None:
    """
    L'asimmetria 20:1 era un seme solo? Importanza mediata su cinque semi.

    La diagnosi di partenza — `home_quota_minuti_assenti` prima feature su 61,
    la gemella in trasferta cinquantanovesima — veniva da UN seme. Con
    `colsample_bytree` a 0.6 ogni albero vede un sottoinsieme casuale delle
    colonne, e fra due colonne che portano quasi la stessa informazione quale
    vinca dipende dal seme. Qui si ripete la diagnosi su tutti i semi e si
    guarda quanto oscilla il rapporto: se cambia verso fra un seme e l'altro,
    il 20:1 era rumore di campionamento delle colonne.
    """
    experiments.proteggi_produzione()
    df = load_dataset()
    played = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    casa, fuori = "home_quota_minuti_assenti", "away_quota_minuti_assenti"

    righe = []
    for seme in SEMI:
        for stagione in config.TEST_SEASONS:
            cutoff = df.loc[df["season"] == stagione, "date"].min()
            m = M5Set(("BASE", "GIOCATORI"), seed=seme).fit(played[played["date"] < cutoff])
            for lato, mod in (("casa", m.model_home_), ("fuori", m.model_away_)):
                gain = pd.Series(mod.booster_.feature_importance(importance_type="gain"),
                                 index=m.features_)
                gain = gain / gain.sum()
                rango = gain.rank(ascending=False)
                righe.append({"seme": seme, "stagione": stagione, "lato_modello": lato,
                              "quota_casa": gain[casa], "quota_fuori": gain[fuori],
                              "rango_casa": rango[casa], "rango_fuori": rango[fuori]})
    tab = pd.DataFrame(righe)
    per_seme = tab.groupby("seme")[["quota_casa", "quota_fuori",
                                    "rango_casa", "rango_fuori"]].mean()
    per_seme["rapporto"] = per_seme["quota_casa"] / per_seme["quota_fuori"]
    print("\n=== IMPORTANZA, SEME PER SEME (media su 3 stagioni x 2 lati) ===")
    print(per_seme.round(4).to_string())
    media = tab[["quota_casa", "quota_fuori"]].mean()
    print(f"\nmedia sui 5 semi: casa {media['quota_casa']:.4f}  fuori "
          f"{media['quota_fuori']:.4f}  rapporto {media['quota_casa']/media['quota_fuori']:.1f}:1")
    print(f"rapporto per seme: da {per_seme['rapporto'].min():.1f} a "
          f"{per_seme['rapporto'].max():.1f}")
    tab.to_csv(experiments.percorso("riassunto_bloccoB_importanza.csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Blocco B: verifiche di robustezza")
    ap.add_argument("--lancia", action="store_true")
    ap.add_argument("--paralleli", type=int, default=8)
    ap.add_argument("--analizza", action="store_true")
    ap.add_argument("--importanza", action="store_true",
                    help="l'asimmetria 20:1 regge su cinque semi?")
    ap.add_argument("--variante", choices=sorted(VARIANTI))
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()

    experiments.proteggi_produzione()
    if args.variante is not None:
        esegui(args.variante, args.seed)
    elif args.lancia:
        lancia(args.paralleli)
    elif args.analizza:
        analizza()
    elif args.importanza:
        importanza()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
