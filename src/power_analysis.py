"""
Calcolo di potenza: i Big 5 servono davvero, o bastano i dati che abbiamo?

LA DOMANDA
Il layer giocatori costa settimane. Prima di costruirlo conviene sapere se il
test set attuale sia in grado di *vedere* l'effetto che potrebbe produrre. Se
l'effetto minimo rilevabile in Serie A e' gia' sotto l'effetto atteso, i Big 5
non servono e si passa direttamente al layer giocatori. Altrimenti allargare
il perimetro non e' un lusso: e' la condizione perche' la misura significhi
qualcosa.

LA DEFINIZIONE DI CLUSTER, CHE CAMBIA IL RISULTATO
Il bootstrap del progetto raggruppa per giornata, perche' le partite di una
giornata sono predette dallo stesso addestramento. Allargando ai Big 5 la
definizione si biforca, e le due strade danno numeri diversi:

  giornata di campionato   ogni lega ha le sue 38 giornate -> 5 volte i
                           cluster della sola Serie A. Corretta se ogni lega
                           ha un modello addestrato per conto suo.
  settimana di calendario  le giornate dei cinque campionati che cadono nella
                           stessa settimana sono UN cluster -> tanti cluster
                           quanti ne ha la Serie A da sola. Corretta se il
                           modello e' unico e addestrato su tutte le leghe
                           insieme, perche' allora le partite della stessa
                           settimana condividono lo stesso stato.

Fra le due c'e' un fattore sqrt(5) ~ 2.24 sull'errore standard. Il progetto
addestra UN modello su tutte le leghe (config.LEAGUES e' una lista sola), e la
scelta prudente e' quindi la settimana di calendario. Si riportano entrambe
perche' la differenza e' esattamente il punto della domanda.

Uso:
    python -m src.power_analysis
    python -m src.power_analysis --minuti-assenti 0.15 --shift-lambda 0.10
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy import stats

from . import config
from .evaluate import PROB_COLS, outcome_index, rps
from .models.baseline import predictions_from_lambdas

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("power")

# Partite per stagione: Serie A 380, i Big 5 insieme ~1826
# (380+380+380+306+380 fra Italia, Inghilterra, Spagna, Germania, Francia).
PARTITE_STAGIONE = {"Serie A": 380, "Big 5": 1826}
GIORNATE_STAGIONE = {"Serie A": 38, "Big 5": 38 * 5}
SETTIMANE_STAGIONE = {"Serie A": 38, "Big 5": 38}


def sd_differenza_appaiata() -> tuple[float, float, int]:
    """
    Deviazione standard della differenza appaiata di RPS, per partita e per
    cluster, misurata sulle previsioni gia' prodotte dal walk-forward.

    Si usa la differenza fra i due modelli piu' vicini fra loro (M5 ancorato
    contro il mercato): e' la scala di rumore che un effetto piccolo dovra'
    superare, ed e' quella giusta perche' il layer giocatori si misurera'
    esattamente cosi', ancorando al mercato.
    """
    path = config.PROCESSED / "walk_forward_predictions.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path.name} assente: lancia prima python -m src.evaluate"
        )
    preds = pd.read_parquet(path)
    keys = ["season", "matchday", "home_team", "away_team"]
    ok = preds.dropna(subset=PROB_COLS).copy()
    ok["rps"] = rps(ok[PROB_COLS].to_numpy(float), outcome_index(ok["FTR"]))
    wide = ok.pivot_table(index=keys, columns="model", values="rps")

    a, b = "M5 GBM ancorato al mercato", "M1b market-only (diretto)"
    if a not in wide.columns or b not in wide.columns:
        a, b = wide.columns[0], wide.columns[1]
        log.warning("modelli attesi assenti, uso %s contro %s", a, b)
    d = (wide[a] - wide[b]).dropna()

    cluster = (d.index.get_level_values("season").astype(str) + "-"
               + d.index.get_level_values("matchday").astype(str))
    per_cluster = d.groupby(cluster).mean()
    log.info("differenza appaiata '%s' - '%s': %d partite, %d cluster",
             a, b, len(d), len(per_cluster))
    return float(d.std(ddof=1)), float(per_cluster.std(ddof=1)), len(per_cluster)


def effetto_minimo(sd_cluster: float, n_cluster: float,
                   potenza: float = 0.80, alfa: float = 0.05) -> float:
    """
    Effetto minimo rilevabile con un test a due code su medie di cluster.

    MDE = (z_{alfa/2} + z_{potenza}) * sd_cluster / sqrt(n_cluster)

    Si ragiona sui cluster e non sulle partite perche' e' l'unita' davvero
    indipendente: dentro una giornata le previsioni condividono lo stesso
    addestramento. Ignorarlo restringe l'intervallo del 38%, misurato.
    """
    z = stats.norm.ppf(1 - alfa / 2) + stats.norm.ppf(potenza)
    return z * sd_cluster / np.sqrt(n_cluster)


def effetto_atteso_da_shift(shift: float = 0.10, n: int = 40_000,
                            seed: int = 0) -> float:
    """
    Quanto vale, in RPS, sapere che una squadra vale ~`shift` gol in meno.

    Simulazione: si prendono lambda plausibili, si genera il risultato da un
    modello in cui la squadra di casa e' indebolita di `shift` gol, e si
    confronta l'RPS di chi lo sa con quello di chi non lo sa. E' l'effetto che
    il layer giocatori potrebbe produrre se il 20% dei minuti mancanti valesse
    un decimo di gol.

    Non e' una stima dell'effetto vero — quello non lo sappiamo — ma
    dell'ordine di grandezza con cui confrontare il minimo rilevabile.
    """
    rng = np.random.default_rng(seed)
    lam_h = rng.uniform(0.8, 2.2, n)
    lam_a = rng.uniform(0.7, 1.8, n)

    vero_h = np.maximum(lam_h - shift, 0.05)          # chi sa dell'assenza
    x = rng.poisson(vero_h)
    y = rng.poisson(lam_a)
    esito = np.where(x > y, 0, np.where(x == y, 1, 2))

    idx = pd.RangeIndex(n)
    p_ignaro = predictions_from_lambdas(lam_h, lam_a, index=idx)[PROB_COLS].to_numpy()
    p_informato = predictions_from_lambdas(vero_h, lam_a, index=idx)[PROB_COLS].to_numpy()
    return float(rps(p_ignaro, esito).mean() - rps(p_informato, esito).mean())


def analisi(minuti_assenti: float = 0.15, shift: float = 0.10,
            stagioni: int = 3) -> pd.DataFrame:
    """
    I quattro numeri: minimo rilevabile in Serie A e nei Big 5, nelle due
    definizioni di cluster, contro l'effetto atteso.

    Il calcolo si fa sul SOTTOINSIEME con assenze rilevanti, non su tutte le
    partite: il layer giocatori non puo' cambiare la previsione di una partita
    in cui giocano tutti, e diluire l'effetto su quelle e' il modo piu' comune
    di concludere che non serve.
    """
    sd_partita, sd_cluster, n_cluster_osservati = sd_differenza_appaiata()
    atteso = effetto_atteso_da_shift(shift)

    log.info("sd della differenza per partita  %.5f", sd_partita)
    log.info("sd della differenza per cluster  %.5f (su %d cluster osservati)",
             sd_cluster, n_cluster_osservati)
    log.info("effetto atteso da uno shift di %.2f gol: %.5f RPS", shift, atteso)
    log.info("quota di partite con oltre il %.0f%% di minuti indisponibili: %.0f%%",
             minuti_assenti * 100, minuti_assenti * 100)

    righe = []
    for perimetro in ("Serie A", "Big 5"):
        for nome_cluster, per_stagione in (
            ("giornata di campionato", GIORNATE_STAGIONE[perimetro]),
            ("settimana di calendario", SETTIMANE_STAGIONE[perimetro]),
        ):
            # Solo la frazione di partite con assenze rilevanti: i cluster si
            # riducono nella stessa proporzione, perche' dentro ogni giornata
            # solo quella quota di partite entra nel test.
            n_cluster = per_stagione * stagioni
            sd_eff = sd_cluster / np.sqrt(max(minuti_assenti, 1e-9))
            mde = effetto_minimo(sd_eff, n_cluster)
            righe.append({
                "perimetro": perimetro,
                "cluster": nome_cluster,
                "n_cluster": int(n_cluster),
                "partite_totali": int(PARTITE_STAGIONE[perimetro] * stagioni),
                "partite_utili": int(PARTITE_STAGIONE[perimetro] * stagioni * minuti_assenti),
                "MDE": mde,
                "effetto_atteso": atteso,
                "rilevabile": mde < atteso,
            })
    return pd.DataFrame(righe)


def main() -> None:
    ap = argparse.ArgumentParser(description="Calcolo di potenza per il layer giocatori")
    ap.add_argument("--minuti-assenti", type=float, default=0.15,
                    help="quota di partite con oltre il 15%% di minuti indisponibili")
    ap.add_argument("--shift-lambda", type=float, default=0.10,
                    help="spostamento di lambda che le assenze produrrebbero")
    ap.add_argument("--stagioni", type=int, default=3, help="stagioni nel test set")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    tab = analisi(args.minuti_assenti, args.shift_lambda, args.stagioni)

    print("\n=== EFFETTO MINIMO RILEVABILE (potenza 80%, alfa 5%) ===")
    print("Sul sottoinsieme con assenze rilevanti, non su tutte le partite.\n")
    vista = tab.copy()
    vista["MDE"] = vista["MDE"].map(lambda v: f"{v:.5f}")
    vista["effetto_atteso"] = vista["effetto_atteso"].map(lambda v: f"{v:.5f}")
    vista["rilevabile"] = vista["rilevabile"].map({True: "SI", False: "no"})
    print(vista.to_string(index=False))

    print("\n=== SENSIBILITA': quanto deve essere grande l'effetto ===")
    print("Lo shift di lambda dipende da quanto pesano le assenze. A 0.10 gol")
    print("corrisponde grosso modo il 20% dei minuti di una rosa media.\n")
    sd_cluster = sd_differenza_appaiata()[1]
    sd_eff = sd_cluster / np.sqrt(max(args.minuti_assenti, 1e-9))
    print(f"  {'shift':>7} {'effetto RPS':>12} {'cluster necessari':>18} "
          f"{'= stagioni Serie A':>20} {'= stagioni Big 5':>18}")
    for sh in (0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
        eff = effetto_atteso_da_shift(sh)
        z = stats.norm.ppf(0.975) + stats.norm.ppf(0.80)
        n_nec = (z * sd_eff / eff) ** 2
        print(f"  {sh:>7.2f} {eff:>12.5f} {n_nec:>18.0f} "
              f"{n_nec / 38:>20.1f} {n_nec / (38 * 5):>18.1f}")
    print("\n  'stagioni Big 5' assume il cluster per GIORNATA DI CAMPIONATO,")
    print("  cioe' l'ipotesi ottimistica: un modello per lega. Con un modello")
    print("  unico e cluster per settimana, la colonna da leggere e' quella")
    print("  della Serie A anche per i Big 5.")

    print("\n=== VERDETTO ===")
    sa = tab[(tab["perimetro"] == "Serie A")
             & (tab["cluster"] == "settimana di calendario")].iloc[0]
    b5 = tab[(tab["perimetro"] == "Big 5")
             & (tab["cluster"] == "settimana di calendario")].iloc[0]
    atteso = float(tab["effetto_atteso"].iloc[0])

    print(f"Con la definizione prudente di cluster (settimana di calendario):")
    print(f"  Serie A: MDE {sa['MDE']:.5f}  contro effetto atteso {atteso:.5f}")
    print(f"  Big 5:   MDE {b5['MDE']:.5f}  contro effetto atteso {atteso:.5f}")
    if sa["rilevabile"]:
        print("\n-> La sola Serie A basta. I Big 5 non servono: si passa")
        print("   direttamente al layer giocatori.")
    elif b5["rilevabile"]:
        print("\n-> La Serie A da sola NON basta, i Big 5 si'. Allargare il")
        print("   perimetro e' la condizione perche' la misura significhi qualcosa.")
    else:
        print("\n-> Nessuno dei due perimetri basta per un effetto di questa")
        print("   dimensione. Serve un effetto piu' grande, o piu' stagioni,")
        print("   oppure si accetta di non poterlo dimostrare.")


if __name__ == "__main__":
    main()
