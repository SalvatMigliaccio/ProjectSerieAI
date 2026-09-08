"""
Calcolo di potenza: quale effetto questo test set e' in grado di VEDERE.

LA DOMANDA
Ogni blocco di informazione nuova (contesto, giocatori, valore delle rose)
costa giorni o settimane. Prima di costruirlo conviene sapere se il test set
sia capace di distinguere il suo effetto dal rumore. Se non lo e', il blocco
si potra' costruire ma non misurare, e un risultato non interpretabile e'
peggio di nessun risultato: sembra una risposta.

DUE COSE DIVERSE CHE E' FACILE CONFONDERE
  soglia         il criterio che definisce il sottoinsieme, per esempio
                 "oltre il 15% dei minuti stagionali indisponibili";
  quota          la FRAZIONE DI PARTITE che quel criterio seleziona.
La prima si sceglie, la seconda si misura — e finche' il layer giocatori non
esiste non si conosce. Qui la quota e' un parametro che si fa variare, non un
numero che si finge di sapere. Una versione precedente di questo modulo usava
lo stesso valore per entrambe, che e' un errore silenzioso: produce numeri
plausibili e sbagliati.

LA DEFINIZIONE DI CLUSTER, CHE CAMBIA IL RISULTATO
Il bootstrap del progetto raggruppa per giornata, perche' le partite di una
giornata sono predette dallo stesso addestramento. Allargando ai Big 5 la
definizione si biforca:

  giornata di campionato   ogni lega ha le sue 38 giornate -> 5 volte i
                           cluster della sola Serie A. Corretta se ogni lega
                           ha un modello addestrato per conto suo.
  settimana di calendario  le giornate dei cinque campionati che cadono nella
                           stessa settimana sono UN cluster, ma con cinque
                           volte le partite dentro. Corretta se il modello e'
                           unico, come oggi (`config.LEAGUES` e' una lista
                           sola).

QUANTO VALGA LA SECONDA NON E' UN'OPINIONE, E' UNA MISURA
Piu' partite dentro lo stesso cluster abbassano la varianza della media di
cluster, ma solo fino al pavimento imposto dalla correlazione interna:

    Var(media di k partite) = sigma^2 * (1 + (k-1) * rho) / k

Con rho = 0 le partite in piu' contano tutte; con rho = 1 non contano niente e
i Big 5 non aggiungono potenza. Una versione precedente di questo documento
assumeva il secondo caso senza verificarlo. Qui `rho` si stima dalle
differenze appaiate gia' osservate nel walk-forward, e il verdetto segue dal
numero misurato.

Uso:
    python -m src.power_analysis
    python -m src.power_analysis --quota-partite 0.25 --shift-lambda 0.15
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

# I tre scenari, come (perimetro, definizione di cluster, cluster per stagione,
# partite dentro un cluster). L'ultimo numero e' quello che la biforcazione
# sopra rende decisivo.
SCENARI = [
    ("Serie A", "giornata di campionato", 38, 10),
    ("Big 5", "giornata di campionato", 38 * 5, 10),
    ("Big 5", "settimana di calendario", 38, 48),
]

# I sottoinsiemi su cui i blocchi del piano verranno misurati, con la quota di
# partite che ci si aspetta selezionino. Sono ordini di grandezza dichiarati
# PRIMA di guardare i risultati: servono a scegliere se un blocco vale la pena,
# e vanno sostituiti con la quota vera appena il blocco esiste.
SOTTOINSIEMI = {
    "tutte le partite": 1.00,
    # Misurate su matches_master dal blocco A (features/context.py), non piu'
    # assunte: 514 partite infrasettimanali e 503 derby su 4580.
    "infrasettimanali": 0.112,
    "derby (derbies.csv)": 0.110,
    # Ancora assunta: la soglia del 15% di minuti pesati indisponibili esiste,
    # ma quante partite la superino si sapra' solo col blocco B.
    "assenze oltre il 15% dei minuti": 0.20,
}


# ---------------------------------------------------------------------------
# La scala del rumore, misurata
# ---------------------------------------------------------------------------

def _rps_per_modello(path) -> pd.DataFrame:
    preds = pd.read_parquet(path)
    keys = ["season", "matchday", "home_team", "away_team"]
    ok = preds.dropna(subset=PROB_COLS).copy()
    ok["rps"] = rps(ok[PROB_COLS].to_numpy(float), outcome_index(ok["FTR"]))
    return ok.pivot_table(index=keys, columns="model", values="rps")


def differenze_appaiate(annidata: bool = True) -> tuple[np.ndarray, np.ndarray, str]:
    """
    La differenza appaiata di RPS, partita per partita, con la sua giornata.

    QUALE COPPIA, E PERCHE' NON E' UN DETTAGLIO
    Una versione precedente di questo modulo usava M5 ancorato contro il
    mercato. E' la coppia sbagliata: quella misura il rumore sul LIVELLO del
    modello, mentre un blocco di feature si decide sulla differenza fra due
    modelli ANNIDATI — lo stesso M5 con e senza le colonne nuove. I due
    modelli condividono quasi tutto, quindi la loro differenza e' molto meno
    rumorosa.

    Misurato sul blocco A: sd per partita 0.00280 contro 0.01102, cioe' il
    confronto annidato e' **4.3 volte** meno rumoroso. Usare la coppia
    sbagliata gonfiava il minimo rilevabile dello stesso fattore, e faceva
    dichiarare non misurabile un test che invece lo era.

    Si ripiega sulla coppia contro il mercato solo se non esiste ancora
    nessun walk-forward di blocco, dicendolo.
    """
    keys = ["season", "matchday", "home_team", "away_team"]

    if annidata:
        for path in sorted(config.PROCESSED.glob("walk_forward_blocco_*.parquet")):
            wide = _rps_per_modello(path)
            con = [c for c in wide.columns if "(con " in c]
            senza = [c for c in wide.columns if "(senza " in c]
            if con and senza:
                d = (wide[con[0]] - wide[senza[0]]).dropna()
                cluster = (d.index.get_level_values("season").astype(str) + "-"
                           + d.index.get_level_values("matchday").astype(str)).to_numpy()
                return d.to_numpy(float), cluster, f"{con[0]}  -  {senza[0]}"
        log.warning("nessun walk-forward di blocco: ripiego sulla coppia contro "
                    "il mercato, che SOVRASTIMA il rumore di circa 4 volte")

    path = config.PROCESSED / "walk_forward_predictions.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path.name} assente: lancia prima python -m src.evaluate"
        )
    wide = _rps_per_modello(path)
    a, b = "M5 GBM ancorato al mercato", "M1b market-only (diretto)"
    if a not in wide.columns or b not in wide.columns:
        a, b = wide.columns[0], wide.columns[1]
        log.warning("modelli attesi assenti, uso %s contro %s", a, b)
    d = (wide[a] - wide[b]).dropna()

    cluster = (d.index.get_level_values("season").astype(str) + "-"
               + d.index.get_level_values("matchday").astype(str)).to_numpy()
    return d.to_numpy(float), cluster, f"{a}  -  {b}"


def correlazione_interna(d: np.ndarray, cluster: np.ndarray) -> dict:
    """
    Varianza fra cluster, dentro i cluster, e la loro correlazione interna.

    E' l'ANOVA a una via sulle differenze appaiate. `rho` (l'intraclass
    correlation) e' la quota di varianza che sta FRA le giornate invece che
    dentro: dice quanto una partita in piu' nella stessa giornata aggiunga
    davvero informazione.

    Stimatore dei momenti, con `k0` la dimensione media corretta dei cluster:
    con cluster di dimensione diversa la media semplice sarebbe distorta.
    """
    codici, _ = pd.factorize(cluster)
    n_cl = codici.max() + 1
    n = len(d)
    conteggi = np.bincount(codici).astype(float)
    medie = np.bincount(codici, weights=d) / conteggi
    media = d.mean()

    # Somme dei quadrati: fra i cluster e dentro i cluster.
    ss_fra = float((conteggi * (medie - media) ** 2).sum())
    ss_dentro = float(((d - medie[codici]) ** 2).sum())
    ms_fra = ss_fra / (n_cl - 1)
    ms_dentro = ss_dentro / (n - n_cl)

    k0 = (n - (conteggi ** 2).sum() / n) / (n_cl - 1)
    var_fra = max((ms_fra - ms_dentro) / k0, 0.0)
    rho = var_fra / (var_fra + ms_dentro) if (var_fra + ms_dentro) > 0 else 0.0

    return {
        "n": n, "n_cluster": n_cl, "k_medio": float(conteggi.mean()),
        "sd_partita": float(d.std(ddof=1)),
        "var_fra": var_fra, "var_dentro": ms_dentro, "rho": float(rho),
        "sd_cluster_osservata": float(medie.std(ddof=1)),
    }


def sd_media_cluster(sd_partita: float, rho: float, k: float) -> float:
    """
    Deviazione standard della media di un cluster da `k` partite.

    sd = sigma * sqrt((1 + (k-1) * rho) / k)

    Con k < 1 il cluster non contiene neppure una partita del sottoinsieme:
    non si restringe il cluster, si perdono cluster interi. Quel caso lo
    gestisce `scenari`, qui k si tiene a 1.
    """
    k = max(float(k), 1.0)
    return sd_partita * np.sqrt((1.0 + (k - 1.0) * rho) / k)


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
    return z * sd_cluster / np.sqrt(max(n_cluster, 1e-9))


def effetto_atteso_da_shift(shift: float = 0.10, n: int = 40_000,
                            seed: int = 0) -> float:
    """
    Quanto vale, in RPS, sapere che una squadra vale ~`shift` gol in meno.

    Simulazione: si prendono lambda plausibili, si genera il risultato da un
    modello in cui la squadra di casa e' indebolita di `shift` gol, e si
    confronta l'RPS di chi lo sa con quello di chi non lo sa.

    Non e' una stima dell'effetto vero — quello non lo sappiamo — ma
    dell'ordine di grandezza con cui confrontare il minimo rilevabile. Vale
    per una partita DENTRO il sottoinsieme, dove l'assenza c'e' davvero.
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


def verifica_formula(stat: dict, d: np.ndarray, cluster: np.ndarray) -> float:
    """
    La formula del design effect riproduce la sd di cluster osservata?

    Serve perche' tutto il resto ne dipende: se con k pari alla dimensione
    vera dei cluster la formula non ritrova la deviazione standard misurata,
    la stima di rho e' sbagliata e i verdetti con lei.
    """
    atteso = sd_media_cluster(stat["sd_partita"], stat["rho"], stat["k_medio"])
    return abs(atteso - stat["sd_cluster_osservata"]) / stat["sd_cluster_osservata"]


# ---------------------------------------------------------------------------
# Gli scenari
# ---------------------------------------------------------------------------

def scenari(stat: dict, quota: float, atteso: float,
            stagioni: int = 3) -> pd.DataFrame:
    """
    Minimo rilevabile per perimetro e definizione di cluster, sul
    sottoinsieme.

    IL SOTTOINSIEME SI DEFINISCE PRIMA, NON DOPO. Misurare l'effetto sulle
    sole partite in cui il blocco puo' cambiare qualcosa non e' cherry
    picking se il criterio e' scritto prima di guardare i risultati: e' la
    differenza fra un test potente e uno diluito. La colonna `MDE_diluito`
    mostra cosa succede a non farlo — l'effetto medio si riduce in
    proporzione alla quota, il rumore no.
    """
    righe = []
    for perimetro, nome_cluster, cluster_stagione, k_pieno in SCENARI:
        n_cluster = cluster_stagione * stagioni
        k_sub = k_pieno * quota

        # Con meno di una partita per cluster non si accorcia il cluster: si
        # perdono cluster interi, e sono loro a fare la potenza.
        if k_sub < 1.0:
            n_eff, k_eff = n_cluster * k_sub, 1.0
        else:
            n_eff, k_eff = n_cluster, k_sub

        sd_sub = sd_media_cluster(stat["sd_partita"], stat["rho"], k_eff)
        mde = effetto_minimo(sd_sub, n_eff)

        # Test diluito: stesse partite di sempre, ma l'effetto c'e' solo su
        # una quota di esse, quindi la media si abbassa in proporzione.
        sd_pieno = sd_media_cluster(stat["sd_partita"], stat["rho"], k_pieno)
        mde_diluito = effetto_minimo(sd_pieno, n_cluster) / max(quota, 1e-9)

        righe.append({
            "perimetro": perimetro,
            "cluster": nome_cluster,
            "n_cluster": round(n_eff, 1),
            "partite_cluster": round(k_eff, 1),
            "partite_utili": int(PARTITE_STAGIONE[perimetro] * stagioni * quota),
            "MDE": mde,
            "MDE_diluito": mde_diluito,
            "effetto_atteso": atteso,
            "rilevabile": mde < atteso,
        })
    return pd.DataFrame(righe)


def quota_necessaria(stat: dict, atteso: float, stagioni: int = 3) -> pd.DataFrame:
    """
    Per ogni scenario, la quota di partite oltre la quale l'effetto si vede.

    E' la lettura piu' utile del calcolo: non "si puo' o non si puo'", ma
    "quanto deve essere grande il sottoinsieme perche' si possa".
    """
    righe = []
    for perimetro, nome_cluster, cluster_stagione, k_pieno in SCENARI:
        n_cluster = cluster_stagione * stagioni
        trovata = np.nan
        for q in np.arange(0.01, 1.001, 0.01):
            k_sub = k_pieno * q
            if k_sub < 1.0:
                n_eff, k_eff = n_cluster * k_sub, 1.0
            else:
                n_eff, k_eff = n_cluster, k_sub
            if effetto_minimo(sd_media_cluster(stat["sd_partita"], stat["rho"], k_eff),
                              n_eff) < atteso:
                trovata = q
                break
        righe.append({
            "perimetro": perimetro, "cluster": nome_cluster,
            "quota_minima": trovata,
            "partite_richieste": (int(PARTITE_STAGIONE[perimetro] * stagioni * trovata)
                                  if np.isfinite(trovata) else np.nan),
        })
    return pd.DataFrame(righe)


def curva_effetto(shift_max: float = 0.60, passo: float = 0.02) -> pd.DataFrame:
    """L'effetto RPS in funzione dello shift, calcolato una volta sola."""
    shift = np.arange(passo, shift_max + 1e-9, passo)
    return pd.DataFrame({
        "shift": shift,
        "effetto": [effetto_atteso_da_shift(s) for s in shift],
    })


def soglia_di_rottura(stat: dict, curva: pd.DataFrame,
                      stagioni: int = 3) -> pd.DataFrame:
    """
    Per ogni quota, quanto grande deve essere l'effetto perche' si veda.

    E' LA TABELLA CHE DECIDE, e va letta al posto delle precedenti.
    Restringere il sottoinsieme NON abbassa il minimo rilevabile: lo alza,
    perche' le partite diminuiscono. Restringere conviene solo se l'effetto
    per partita cresce piu' in fretta del rumore — ed e' plausibile che sia
    cosi', perche' il 5% di partite con le assenze piu' pesanti ha uno shift
    molto maggiore del 50% con qualche assenza.

    Qui non si finge di sapere quanto cresca: si dice, per ogni dimensione
    del sottoinsieme, quale shift di lambda servirebbe. Il confronto con la
    realta' lo fa chi legge, ed e' una domanda a cui si puo' rispondere —
    "un portiere titolare piu' due difensori valgono 0.20 gol?" e' decidibile,
    "l'effetto e' 0.0006 RPS" no.
    """
    righe = []
    for perimetro, nome_cluster, cluster_stagione, k_pieno in SCENARI:
        n_cluster = cluster_stagione * stagioni
        for quota in (1.00, 0.50, 0.25, 0.15, 0.10, 0.05, 0.02):
            k_sub = k_pieno * quota
            if k_sub < 1.0:
                n_eff, k_eff = n_cluster * k_sub, 1.0
            else:
                n_eff, k_eff = n_cluster, k_sub
            mde = effetto_minimo(
                sd_media_cluster(stat["sd_partita"], stat["rho"], k_eff), n_eff)
            # Il primo shift della griglia il cui effetto supera l'MDE.
            sopra = curva[curva["effetto"] >= mde]
            righe.append({
                "perimetro": perimetro,
                "cluster": nome_cluster,
                "quota": quota,
                "partite": int(PARTITE_STAGIONE[perimetro] * stagioni * quota),
                "MDE": mde,
                "shift_richiesto": (float(sopra["shift"].iloc[0])
                                    if not sopra.empty else np.nan),
            })
    return pd.DataFrame(righe)


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Calcolo di potenza: quale effetto il test set puo' vedere"
    )
    ap.add_argument("--quota-partite", type=float, default=0.20,
                    help="frazione di partite che il sottoinsieme seleziona "
                         "(NON la soglia che lo definisce)")
    ap.add_argument("--shift-lambda", type=float, default=0.10,
                    help="spostamento di lambda che il blocco produrrebbe, "
                         "sulle partite del sottoinsieme")
    ap.add_argument("--stagioni", type=int, default=3, help="stagioni nel test set")
    args = ap.parse_args()

    pd.set_option("display.width", 220)

    d, cluster, coppia = differenze_appaiate()
    stat = correlazione_interna(d, cluster)
    atteso = effetto_atteso_da_shift(args.shift_lambda)
    scarto = verifica_formula(stat, d, cluster)

    print("\n=== LA SCALA DEL RUMORE, MISURATA ===")
    print(f"coppia di riferimento: {coppia}")
    print(f"  partite                        {stat['n']}")
    print(f"  cluster (giornate)             {stat['n_cluster']}")
    print(f"  partite per cluster            {stat['k_medio']:.1f}")
    print(f"  sd della differenza, partita   {stat['sd_partita']:.5f}")
    print(f"  sd della media di cluster      {stat['sd_cluster_osservata']:.5f}")
    print(f"  correlazione interna rho       {stat['rho']:.4f}")
    print(f"  la formula del design effect la riproduce a meno del {scarto:.1%}")
    if stat["rho"] < 0.02:
        print("\n  rho ~ 0: dentro una giornata le differenze sono quasi")
        print("  indipendenti. Aggiungere partite allo stesso cluster AUMENTA")
        print("  la potenza — quindi i Big 5 servono anche con un modello unico.")
    else:
        print(f"\n  rho = {stat['rho']:.3f}: le partite della stessa giornata si")
        print("  somigliano. Aggiungerne altre nello stesso cluster aiuta, ma")
        print("  meno di quanto il loro numero suggerisca.")

    print("\n=== EFFETTO MINIMO RILEVABILE (potenza 80%, alfa 5%) ===")
    print(f"Sottoinsieme: {args.quota_partite:.0%} delle partite. "
          f"Effetto atteso su una partita del sottoinsieme: {atteso:.5f} RPS")
    print(f"(shift di {args.shift_lambda:.2f} gol).\n")
    tab = scenari(stat, args.quota_partite, atteso, args.stagioni)
    vista = tab.copy()
    for c in ("MDE", "MDE_diluito", "effetto_atteso"):
        vista[c] = vista[c].map(lambda v: f"{v:.5f}")
    vista["rilevabile"] = vista["rilevabile"].map({True: "SI", False: "no"})
    print(vista.to_string(index=False))
    print("\nMDE          misurando SOLO sul sottoinsieme, definito prima.")
    print("MDE_diluito  misurando su tutte le partite: l'effetto medio si")
    print("             riduce con la quota, il rumore no. E' la colonna che")
    print("             spiega perche' il sottoinsieme va dichiarato prima.")

    print("\n=== QUOTA MINIMA PERCHE' L'EFFETTO SI VEDA ===")
    qt = quota_necessaria(stat, atteso, args.stagioni)
    vista = qt.copy()
    vista["quota_minima"] = vista["quota_minima"].map(
        lambda v: f"{v:.0%}" if np.isfinite(v) else "mai")
    print(vista.to_string(index=False))

    print("\n=== I SOTTOINSIEMI DEL PIANO ===")
    print("Quote dichiarate a priori, da sostituire con quelle vere quando il")
    print("blocco esiste. Serie A, cluster per giornata:\n")
    perimetro, nome_cluster, cluster_stagione, k_pieno = SCENARI[0]
    print(f"  {'sottoinsieme':<38} {'quota':>6} {'partite':>8} {'MDE':>9} {'vede?':>6}")
    for nome, q in SOTTOINSIEMI.items():
        riga = scenari(stat, q, atteso, args.stagioni).iloc[0]
        print(f"  {nome:<38} {q:>6.0%} {riga['partite_utili']:>8} "
              f"{riga['MDE']:>9.5f} {'SI' if riga['rilevabile'] else 'no':>6}")

    print("\n=== SOGLIA DI ROTTURA: quale shift serve, per ogni sottoinsieme ===")
    print("Restringere il sottoinsieme ALZA il minimo rilevabile, non lo abbassa:")
    print("le partite diminuiscono. Conviene solo se l'effetto per partita cresce")
    print("piu' in fretta del rumore. Questa tabella dice quanto deve crescere.\n")
    curva = curva_effetto()
    rot = soglia_di_rottura(stat, curva, args.stagioni)
    for (perimetro, nome_cluster), g in rot.groupby(["perimetro", "cluster"], sort=False):
        print(f"  {perimetro} - cluster per {nome_cluster}")
        print(f"    {'quota':>6} {'partite':>8} {'MDE':>9} {'shift richiesto':>16}")
        for _, r in g.iterrows():
            sh = (f"{r['shift_richiesto']:.2f} gol"
                  if np.isfinite(r["shift_richiesto"]) else "oltre 0.60")
            print(f"    {r['quota']:>6.0%} {r['partite']:>8} {r['MDE']:>9.5f} {sh:>16}")
        print()

    print("=== VERDETTO ===")
    for _, r in tab.iterrows():
        rapporto = r["MDE"] / atteso
        print(f"  {r['perimetro']:<8} {r['cluster']:<24} MDE {r['MDE']:.5f}  "
              f"= {rapporto:.1f}x l'effetto atteso   "
              f"{'RILEVABILE' if r['rilevabile'] else 'non rilevabile'}")
    print(f"\n  (a quota {args.quota_partite:.0%} e shift {args.shift_lambda:.2f} gol)")
    if tab["rilevabile"].any():
        ok = tab[tab["rilevabile"]].iloc[0]
        print(f"\n-> Basta {ok['perimetro']} con cluster per "
              f"{ok['cluster']}: si puo' misurare.")
    else:
        print("\n-> A queste condizioni nessuno scenario vede l'effetto. Le vie")
        print("   che restano, in ordine di onesta':")
        print("   1. cercare un blocco il cui shift superi la soglia di rottura")
        print("      qui sopra: e' una domanda decidibile a mente ('un portiere")
        print("      titolare piu' due difensori valgono 0.20 gol?'), non un")
        print("      numero da sperare;")
        print("   2. allargare ai Big 5 — con rho misurato ~0 aiutano davvero,")
        print("      qualunque delle due definizioni di cluster si scelga;")
        print("   3. costruire il blocco senza pretendere di dimostrarlo,")
        print("      dichiarandolo;")
        print("   4. non costruirlo.")


if __name__ == "__main__":
    main()
