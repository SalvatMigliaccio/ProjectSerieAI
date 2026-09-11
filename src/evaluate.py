"""
Ciclo di valutazione: metriche, calibrazione, walk-forward.

PERCHE' QUESTO MODULO VIENE PRIMA DELLE FEATURE
Una feature aggiunta senza un metro non e' un miglioramento, e' un'ipotesi.
Con l'harness in piedi ogni feature successiva si misura sullo stesso test set
con lo stesso protocollo, e o guadagna RPS o esce.

IL PROTOCOLLO
- Test set fisso: stagioni 2324, 2425, 2526 (`config.TEST_SEASONS`). Non si
  guarda altro finche' non si e' finito di scegliere.
- La 1415 non entra mai in addestramento: e' il burn-in delle finestre mobili
  (`config.BURN_IN_SEASONS`).
- Walk-forward per giornata: per ogni giornata del test si addestra da zero su
  tutto cio' che e' stato *giocato prima* del suo primo calcio d'inizio, si
  predice la giornata, si avanza. Riaddestramento da zero, come da decisione
  bloccata.

IL TAGLIO E' LA DATA, NON LA GIORNATA
Il confine dell'addestramento e' `date < prima data della giornata`, non
`giornata < r`. La differenza conta: una partita rinviata appartiene alla sua
giornata ufficiale ma si gioca mesi dopo, e con il taglio per giornata
finirebbe nel training di se stessa. Con il taglio per data non puo' accadere,
e c'e' un assert che lo verifica a ogni blocco.
Il prezzo e' che la partita rinviata viene predetta con informazione vecchia
di mesi. Sono una o due partite per stagione, e sbagliare in questa direzione
e' l'unico modo onesto di sbagliare.

RPS E' LA METRICA PRIMARIA
Le altre si riportano per capire *come* un modello sbaglia: la log loss punisce
la sicurezza mal riposta, il Brier e' meno sensibile alle code, l'accuratezza
non serve a scegliere e sta in tabella solo per comunicare (regola 3).

COME SI DECIDE SE UNA DIFFERENZA E' REALE
Confronto APPAIATO, mai fra due medie separate. Due modelli valutati sulle
stesse partite condividono la difficolta' di quelle partite; la differenza riga
per riga la elimina. L'incertezza su quella differenza si stima con un
bootstrap a cluster sulla giornata, non sulla partita: le dieci partite di una
giornata sono predette dallo stesso addestramento e non sono indipendenti.
Ignorare il cluster restringe l'intervallo del 38%.

Non usare la varianza fra stagioni come soglia di rumore: misura quanto le
stagioni differiscono in difficolta', che e' proprio cio' che l'appaiamento
toglie di mezzo.

Uso:
    python -m src.evaluate                 # tabella + confronto appaiato
    python -m src.evaluate --calibration   # curve di calibrazione ed ECE
    python -m src.evaluate --bias          # favourite-longshot, stagione per stagione
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from . import config
from .models.baseline import Model, default_models
from .normalize import apply_name_map, load_name_map, load_raw, normalize_season

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("evaluate")

KEYS = ["league", "season", "home_team", "away_team"]
OUTCOMES = ("H", "D", "A")
PROB_COLS = ["p_home", "p_draw", "p_away"]


# ---------------------------------------------------------------------------
# Dati
# ---------------------------------------------------------------------------

def add_matchday(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggancia la giornata ufficiale da fbref_schedule.

    Non si prova a ricostruirla dalle date: e' stato tentato, con
    assegnamento goloso in ordine cronologico, e coincide con quella ufficiale
    solo nell'85% dei casi. Un singolo rinvio sfasa tutte le giornate
    successive e genera blocchi che si accavallano nel tempo. La giornata e'
    un dato, non una deduzione.
    """
    mapping = load_name_map()
    sched = normalize_season(apply_name_map(load_raw("fbref_schedule"), mapping))
    sched = sched[sched["league"].isin(config.LEAGUES)]
    sched = sched[KEYS + ["week"]].drop_duplicates(subset=KEYS)

    out = df.merge(sched, on=KEYS, how="left", validate="one_to_one")
    missing = out["week"].isna().sum()
    if missing:
        raise ValueError(
            f"{missing} partite senza giornata: manca una voce in "
            f"{config.TEAM_NAME_MAP.name}? Lancia python -m src.normalize --report"
        )
    out = out.rename(columns={"week": "matchday"})
    out["matchday"] = out["matchday"].astype(int)
    return out


def load_dataset(with_form: bool = True, with_context: bool = True) -> pd.DataFrame:
    """
    Risultati, giornata e feature (mercato, forma, contesto) in un frame solo.

    `with_context` non serve a misurare il blocco A — quello si fa escludendo
    le colonne dal modello, non dal dataset, cosi' i due modelli girano sulle
    STESSE righe e il confronto resta appaiato. Serve solo se il parquet del
    contesto non e' ancora stato costruito.
    """
    matches = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    base = matches[KEYS + ["date", "FTHG", "FTAG", "FTR"]].copy()

    market = pd.read_parquet(config.PROCESSED / "features_market.parquet")
    market = market.drop(columns=[c for c in market.columns if c == "date"])
    df = base.merge(market, on=KEYS, how="left", validate="one_to_one")

    if with_form:
        form = pd.read_parquet(config.PROCESSED / "features_form.parquet")
        form = form.drop(columns=[c for c in form.columns if c == "date"])
        df = df.merge(form, on=KEYS, how="left", validate="one_to_one")

    if with_context:
        for nome, comando in (("features_context", "src.features.context"),
                              ("features_players", "src.features.players")):
            path = config.PROCESSED / f"{nome}.parquet"
            if not path.exists():
                log.warning("%s assente: quel blocco non e' disponibile. "
                            "Lancia 'python -m %s'.", path.name, comando)
                continue
            extra = pd.read_parquet(path)
            extra = extra.drop(columns=[c for c in extra.columns if c == "date"])
            df = df.merge(extra, on=KEYS, how="left", validate="one_to_one")

    df = add_matchday(df)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "home_team"]).reset_index(drop=True)
    log.info("dataset: %d righe, stagioni %s-%s", len(df), df["season"].min(), df["season"].max())
    return df


# ---------------------------------------------------------------------------
# Metriche
# ---------------------------------------------------------------------------

def outcome_index(ftr: pd.Series | np.ndarray) -> np.ndarray:
    """Da 'H'/'D'/'A' a 0/1/2. L'ordine e' quello, ed e' ordinale: conta."""
    return pd.Series(ftr).map({c: i for i, c in enumerate(OUTCOMES)}).to_numpy()


def _onehot(y: np.ndarray, k: int = 3) -> np.ndarray:
    obs = np.zeros((len(y), k))
    obs[np.arange(len(y)), y] = 1.0
    return obs


def rps(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Ranked Probability Score, per riga.

    E' l'unica delle quattro metriche che sa che gli esiti sono ORDINATI:
    prevedere '2' quando esce '1' e' peggio che prevedere 'X'. Si confrontano
    le probabilita' cumulate, non quelle puntuali, e per questo l'ordine
    H, D, A non e' arbitrario.
    """
    obs = _onehot(y)
    cp = np.cumsum(p, axis=1)[:, :-1]
    co = np.cumsum(obs, axis=1)[:, :-1]
    return ((cp - co) ** 2).sum(axis=1) / (p.shape[1] - 1)


def log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Log loss, per riga. NON si clippa.

    Clippare a 1e-15 farebbe sembrare finita la perdita di un modello che
    assegna probabilita' zero a un esito che accade, e il numero che ne esce
    dipenderebbe dall'epsilon scelto, non dal modello. Se esce `inf` e' una
    risposta, non un errore: quel modello e' inutilizzabile per scommettere.
    """
    px = p[np.arange(len(y)), y]
    with np.errstate(divide="ignore"):
        return -np.log(px)


def brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Brier multiclasse: somma degli scarti quadratici sui tre esiti, 0-2."""
    return ((p - _onehot(y)) ** 2).sum(axis=1)


def accuracy(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (p.argmax(axis=1) == y).astype(float)


def metrics(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    return {
        "RPS": float(rps(p, y).mean()),
        "log_loss": float(log_loss(p, y).mean()),
        "Brier": float(brier(p, y).mean()),
        "accuratezza": float(accuracy(p, y).mean()),
        "n": int(len(y)),
    }


# ---------------------------------------------------------------------------
# Calibrazione
# ---------------------------------------------------------------------------

def wilson(k: np.ndarray, n: np.ndarray, z: float = 1.96) -> tuple[np.ndarray, np.ndarray]:
    """
    Intervallo di Wilson per una proporzione.

    Non l'intervallo normale: con bin da un centinaio di osservazioni e
    proporzioni vicine a 0 o 1 quello sballa, e qui le code sono esattamente
    dove si va a cercare la distorsione.
    """
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        phat = k / n
        denom = 1.0 + z**2 / n
        centre = (phat + z**2 / (2 * n)) / denom
        half = z * np.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half


def calibration_table(
    p: np.ndarray,
    y: np.ndarray,
    n_bins: int = config.CALIBRATION_BINS,
) -> pd.DataFrame:
    """
    Curva di calibrazione a bin di uguale numerosita'.

    Bin per quantile e non per ampiezza fissa: le probabilita' non sono
    distribuite uniformemente, e con bin fissi gli estremi conterrebbero
    quattro osservazioni e un intervallo di confidenza inutile.

    ECCEZIONE PER LE PREVISIONI DEGENERI. Se un modello emette pochi valori
    distinti si raggruppa per valore esatto invece che per quantile. Non e' un
    dettaglio: `qcut` su un modello che dice sempre (1, 0, 0) collassa tutto in
    un bin solo, dove atteso e osservato coincidono per forza e l'ECE risulta
    0.000 — cioe' calibrazione perfetta per il modello peggiore possibile.
    Raggruppando per valore lo stesso modello segna 0.399, che e' la verita'.
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]

    distinct = np.unique(p.round(9))
    if len(distinct) <= n_bins:
        bins = pd.Categorical(p.round(9))
    else:
        bins = pd.qcut(p, n_bins, duplicates="drop")
    g = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True)
    tab = g.agg(n=("y", "size"), atteso=("p", "mean"), osservato=("y", "mean")).reset_index()
    lo, hi = wilson(tab["osservato"] * tab["n"], tab["n"])
    tab["ic_basso"], tab["ic_alto"] = lo, hi
    tab["scarto"] = tab["osservato"] - tab["atteso"]
    # Fuori dall'intervallo: lo scarto non e' spiegabile dal solo campionamento.
    tab["significativo"] = (tab["atteso"] < tab["ic_basso"]) | (tab["atteso"] > tab["ic_alto"])
    return tab


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = config.CALIBRATION_BINS) -> float:
    """Expected Calibration Error: scarto medio |atteso - osservato|, pesato."""
    tab = calibration_table(p, y, n_bins)
    return float((tab["n"] * (tab["osservato"] - tab["atteso"]).abs()).sum() / tab["n"].sum())


def ece_multiclass(p: np.ndarray, y: np.ndarray, n_bins: int = config.CALIBRATION_BINS) -> float:
    """
    ECE multiclasse nella forma classica: si guarda solo la probabilita' della
    classe piu' probabile e la si confronta con la frequenza con cui quella
    classe e' davvero uscita.
    """
    conf = p.max(axis=1)
    correct = (p.argmax(axis=1) == y).astype(float)
    return ece(conf, correct, n_bins)


def ece_pooled(p: np.ndarray, y: np.ndarray, n_bins: int = config.CALIBRATION_BINS) -> float:
    """
    ECE su tutte e tre le probabilita' impilate.

    E' la vista giusta per la distorsione favorito-sfavorito, che riguarda il
    livello della probabilita' e non l'esito: una probabilita' del 15% deve
    verificarsi il 15% delle volte, che sia un '1', una 'X' o un '2'.
    """
    stacked_p = p.reshape(-1)
    stacked_y = _onehot(y).reshape(-1)
    return ece(stacked_p, stacked_y, n_bins)


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward(
    df: pd.DataFrame,
    models: list[Model],
    test_seasons: list[str] = tuple(config.TEST_SEASONS),
    exclude_seasons: list[str] = tuple(config.BURN_IN_SEASONS),
    stride: int = 1,
) -> pd.DataFrame:
    """
    Per ogni giornata del test: addestra sul passato, predici, avanza.

    Restituisce un frame lungo, una riga per (partita, modello), che si salva
    su disco: cosi' un modello nuovo si confronta con i vecchi senza doverli
    riaddestrare tutti.

    `stride` raggruppa piu' giornate in un blocco solo, cioe' riaddestra ogni
    `stride` giornate invece che ogni giornata. Resta senza leakage — il taglio
    e' sempre la prima data del blocco — ma e' una previsione piu' difficile,
    perche' l'ultima giornata del blocco viene predetta con un modello vecchio
    di tre settimane. Serve SOLO ad abbassare il costo della ricerca degli
    iperparametri: il risultato riportato usa sempre stride=1.
    """
    played = df[df["FTR"].notna()]
    test = df[df["season"].isin(test_seasons)].copy()
    test["_block"] = (test["matchday"] - 1) // stride
    blocks = test.groupby(["season", "_block"], observed=True, sort=True)
    log.info(
        "walk-forward: %d blocchi (stride %d), %d partite, modelli %s",
        blocks.ngroups, stride, len(test), [m.name for m in models],
    )

    rows: list[pd.DataFrame] = []
    for (season, matchday), block in blocks:
        cutoff = block["date"].min()
        train = played[
            (played["date"] < cutoff) & (~played["season"].isin(exclude_seasons))
        ]
        # La garanzia anti-leakage, verificata a ogni singolo blocco invece che
        # una volta sola all'inizio: costa niente e non lascia scampo.
        assert train["date"].max() < cutoff, f"leakage in {season} giornata {matchday}"
        assert not train.index.intersection(block.index).size, "partita di test nel training"

        for model in models:
            pred = model.fit(train).predict(block)
            rec = block[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]].copy()
            rec["model"] = model.name
            rec["n_train"] = len(train)
            rows.append(pd.concat([rec, pred], axis=1))

    out = pd.concat(rows, ignore_index=True)
    log.info("previsioni prodotte: %d righe", len(out))
    return out


def common_rows(preds: pd.DataFrame) -> pd.DataFrame:
    """
    Restringe al sottoinsieme di partite che TUTTI i modelli hanno predetto.

    Senza questo, un modello che rinuncia sulle partite difficili sembrerebbe
    migliore. Il confronto ha senso solo a parita' di righe.
    """
    ok = preds.dropna(subset=PROB_COLS)
    counts = ok.groupby(KEYS + ["date"], observed=True)["model"].nunique()
    full = counts[counts == preds["model"].nunique()].index
    keep = ok.set_index(KEYS + ["date"]).loc[full].reset_index()
    dropped = preds["model"].nunique() and (len(preds) - len(keep)) // preds["model"].nunique()
    if dropped:
        log.warning("escluse %d partite non predette da tutti i modelli", dropped)
    return keep


def rps_by_match(preds: pd.DataFrame) -> pd.DataFrame:
    """
    RPS di ogni singola partita, una colonna per modello.

    E' la base del confronto appaiato: due modelli valutati sulle STESSE
    partite condividono la difficolta' di quelle partite, e la differenza
    riga per riga la elimina. Confrontare due medie separate no.
    """
    keep = common_rows(preds).copy()
    keep["rps"] = rps(keep[PROB_COLS].to_numpy(dtype=float), outcome_index(keep["FTR"]))
    return keep.pivot(
        index=["season", "matchday", "home_team", "away_team"],
        columns="model",
        values="rps",
    )


def cluster_bootstrap(
    diff: np.ndarray,
    cluster: np.ndarray,
    n_boot: int = config.BOOTSTRAP_SAMPLES,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """
    Intervallo di confidenza sulla differenza media, con bootstrap a cluster.

    PERCHE' A CLUSTER E NON SULLE SINGOLE PARTITE
    Le dieci partite di una giornata condividono lo stato del modello: sono
    predette dallo stesso addestramento, con gli stessi coefficienti. Trattarle
    come dieci osservazioni indipendenti restringerebbe l'intervallo di circa
    la radice di dieci, cioe' farebbe sembrare significativa qualsiasi cosa.
    Si ricampionano le 114 giornate, non le 1140 partite.

    PERCHE' IL BOOTSTRAP E NON UN t-TEST APPAIATO
    La differenza di RPS fra due modelli non e' normale: e' asimmetrica e con
    code pesanti, perche' dominata dalle poche partite in cui un modello
    sbaglia clamorosamente. Il bootstrap non chiede nulla alla forma.
    """
    codes, uniq = pd.factorize(cluster)
    n_cl = len(uniq)
    sums = np.bincount(codes, weights=diff, minlength=n_cl)
    counts = np.bincount(codes, minlength=n_cl).astype(float)

    rng = np.random.default_rng(seed)
    pick = rng.integers(0, n_cl, size=(n_boot, n_cl))
    boot = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)

    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "differenza": float(diff.mean()),
        "ic_basso": float(lo),
        "ic_alto": float(hi),
        "quota_peggiore": float((boot > 0).mean()),
        "n_cluster": int(n_cl),
    }


def paired_comparison(
    preds: pd.DataFrame,
    reference: str,
    n_boot: int = config.BOOTSTRAP_SAMPLES,
) -> pd.DataFrame:
    """Ogni modello contro il riferimento, appaiato sulle stesse partite."""
    wide = rps_by_match(preds)
    cluster = (
        wide.index.get_level_values("season").astype(str)
        + "-"
        + wide.index.get_level_values("matchday").astype(str)
    ).to_numpy()

    rows = []
    for model in wide.columns:
        if model == reference:
            continue
        d = (wide[model] - wide[reference]).to_numpy(dtype=float)
        res = cluster_bootstrap(d, cluster, n_boot=n_boot)
        res["modello"] = model
        # Il segno: positivo = peggiore del riferimento, perche' l'RPS si
        # minimizza. Si esplicita, perche' e' la lettura che si sbaglia.
        res["meglio_del_mercato"] = res["ic_alto"] < 0
        res["peggio_del_mercato"] = res["ic_basso"] > 0
        rows.append(res)
    return pd.DataFrame(rows).set_index("modello")


def paired_pairs(preds: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """
    Confronti appaiati fra coppie scelte, non tutti contro un riferimento.

    Serve per le domande che non passano dal mercato: "la forma batte la forza
    stimata dai risultati?" si risponde confrontando M4 senza mercato con M3,
    e nessuno dei due col mercato.
    """
    wide = rps_by_match(preds)
    cluster = (
        wide.index.get_level_values("season").astype(str)
        + "-"
        + wide.index.get_level_values("matchday").astype(str)
    ).to_numpy()

    rows = []
    for a, b in pairs:
        if a not in wide.columns or b not in wide.columns:
            log.warning("coppia saltata, modello assente: %s vs %s", a, b)
            continue
        res = cluster_bootstrap((wide[a] - wide[b]).to_numpy(dtype=float), cluster)
        res["confronto"] = f"{a}  -  {b}"
        res["conclusione"] = (
            "il primo e' migliore" if res["ic_alto"] < 0
            else "il secondo e' migliore" if res["ic_basso"] > 0
            else "indistinguibili"
        )
        rows.append(res)
    return pd.DataFrame(rows).set_index("confronto")


def compare(
    preds: pd.DataFrame,
    reference: str = "M1b market-only (diretto)",
    floor: str = "M0b frequenze di base",
) -> pd.DataFrame:
    """Tabella di confronto, stesse righe per tutti, ordinata per RPS."""
    keep = common_rows(preds)
    rows = []
    for name, g in keep.groupby("model", observed=True, sort=False):
        p = g[PROB_COLS].to_numpy(dtype=float)
        y = outcome_index(g["FTR"])
        m = metrics(p, y)
        m["modello"] = name
        m["ECE"] = ece_pooled(p, y)
        rows.append(m)
    tab = pd.DataFrame(rows).set_index("modello")

    # Quota di distanza coperta fra il pavimento e il mercato. Dice quanto di
    # cio' che si puo' imparare e' stato imparato, cosa che il valore assoluto
    # di RPS non comunica: 0.1969 non significa niente da solo, "78% della
    # strada verso il mercato" si'. Sopra 1 vuol dire aver superato il mercato.
    if reference in tab.index and floor in tab.index:
        span = tab.loc[floor, "RPS"] - tab.loc[reference, "RPS"]
        tab["skill_closed"] = (tab.loc[floor, "RPS"] - tab["RPS"]) / span

    cols = ["RPS", "log_loss", "Brier", "accuratezza", "ECE"]
    if "skill_closed" in tab.columns:
        cols.append("skill_closed")
    return tab[cols + ["n"]].sort_values("RPS")


# ---------------------------------------------------------------------------
# Distorsione favorito-sfavorito
# ---------------------------------------------------------------------------

def calibration_slope(p: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """
    Pendenza di calibrazione: si regredisce l'esito sul logit della
    probabilita' prevista. Pendenza 1 e intercetta 0 = calibrazione perfetta.

    Pendenza < 1 significa previsioni troppo estreme: le probabilita' alte
    vanno abbassate e le basse alzate. E' la firma quantitativa della
    distorsione favorito-sfavorito, in un numero solo, con il suo errore
    standard. Molto piu' leggibile di dieci bin da guardare a occhio.
    """
    import statsmodels.api as sm

    ok = np.isfinite(p) & np.isfinite(y) & (p > 0) & (p < 1)
    logit = np.log(p[ok] / (1 - p[ok]))
    x = sm.add_constant(logit)
    fit = sm.Logit(y[ok], x).fit(disp=0)
    slope = float(fit.params[1])
    se = float(fit.bse[1])
    # z contro l'ipotesi che la pendenza valga 1, non 0: qui zero non
    # interessa a nessuno, interessa se e' diversa da una calibrazione buona.
    return slope, se, (slope - 1.0) / se


def bias_report(df: pd.DataFrame, prob_col: str = "mkt_", n_bins: int = 10) -> None:
    """
    La distorsione favorito-sfavorito trovata sul totale sopravvive fuori
    campione, o e' un artefatto della media su dodici stagioni?

    Si guarda impilando i tre esiti: la domanda e' se una probabilita' bassa
    si realizzi meno spesso di quanto dichiari, indipendentemente da quale
    esito sia.
    """
    d = df.dropna(subset=[f"{prob_col}p_home", "FTR"]).copy()
    p_all = d[[f"{prob_col}p_home", f"{prob_col}p_draw", f"{prob_col}p_away"]].to_numpy(dtype=float)
    y_all = _onehot(outcome_index(d["FTR"]))

    print("\n=== CALIBRAZIONE DEL MERCATO, TRE ESITI IMPILATI ===")
    for etichetta, mask in (
        ("tutte le stagioni", np.ones(len(d), dtype=bool)),
        ("solo training (1516-2223)", (~d["season"].isin(config.TEST_SEASONS)
                                       & ~d["season"].isin(config.BURN_IN_SEASONS)).to_numpy()),
        ("solo TEST (2324-2526)", d["season"].isin(config.TEST_SEASONS).to_numpy()),
    ):
        p = p_all[mask].reshape(-1)
        y = y_all[mask].reshape(-1)
        tab = calibration_table(p, y, n_bins)
        slope, se, z = calibration_slope(p, y)
        print(f"\n-- {etichetta}: {mask.sum()} partite, {len(p)} coppie (p, esito)")
        print(tab.round(4).to_string(index=False))
        print(f"   ECE {ece(p, y, n_bins):.4f}   pendenza {slope:.3f} +/- {se:.3f}  "
              f"z contro 1: {z:+.2f}")
        print(f"   bin con scarto oltre l'intervallo di confidenza: "
              f"{int(tab['significativo'].sum())}/{len(tab)}")

    print("\n=== STAGIONE PER STAGIONE, REGIONE A BASSA PROBABILITA' (p < 0.25) ===")
    print("Se la distorsione esiste, qui il mercato promette piu' di quanto mantiene.")
    rows = []
    for season, g in d.groupby("season", observed=True, sort=True):
        idx = d.index.get_indexer(g.index)
        atteso, oss, var, n = _low_region_stats(p_all[idx], y_all[idx])
        slope, se, z_slope = calibration_slope(p_all[idx].reshape(-1), y_all[idx].reshape(-1))
        rows.append({
            "season": season,
            "partite": n,
            "atteso": round(atteso, 1),
            "osservato": int(oss),
            "z": round((oss - atteso) / np.sqrt(var), 2) if var > 0 else np.nan,
            "pendenza": round(slope, 3),
            "z_pendenza": round(z_slope, 2),
            "test": season in config.TEST_SEASONS,
        })
    tab = pd.DataFrame(rows)
    print(tab.to_string(index=False))

    print("\n=== VERDETTO ===")
    for etichetta, mask in (
        ("training (1516-2223)", (~d["season"].isin(config.TEST_SEASONS)
                                  & ~d["season"].isin(config.BURN_IN_SEASONS)).to_numpy()),
        ("TEST (2324-2526)", d["season"].isin(config.TEST_SEASONS).to_numpy()),
    ):
        atteso, oss, var, n = _low_region_stats(p_all[mask], y_all[mask])
        z = (oss - atteso) / np.sqrt(var)
        print(f"{etichetta}: attesi {atteso:.1f} eventi, osservati {int(oss)} "
              f"su {n} partite -> z = {z:+.2f} ({'significativo' if abs(z) > 1.96 else 'NON significativo'})")

    neg = int((tab["z"] < 0).sum())
    print(f"\nstagioni con segno negativo: {neg}/{len(tab)}")
    print(f"stagioni con |z| > 1.96 prese singolarmente: {int((tab['z'].abs() > 1.96).sum())}/{len(tab)}")

    # La segnalazione originale riguardava il solo '1' nei decili bassi di
    # p_home, cioe' le partite con la trasferta favorita. E' una vista diversa
    # da quella impilata — mescola il livello di probabilita' con il fattore
    # campo — e va verificata per quello che era.
    print("\n=== VISTA ORIGINALE: SOLO IL '1' ===")
    print("La segnalazione iniziale riguardava i decili bassi di p_home. Ma se")
    print("l'effetto ci fosse anche a p_home alta non sarebbe una distorsione")
    print("legata al livello di probabilita': sarebbe il fattore campo sopravvalutato.")
    p_home = d[f"{prob_col}p_home"].to_numpy(dtype=float)
    y_home = (d["FTR"] == "H").to_numpy(dtype=float)
    mediana = float(np.median(p_home))
    splits = (
        ("training (1516-2223)", (~d["season"].isin(config.TEST_SEASONS)
                                  & ~d["season"].isin(config.BURN_IN_SEASONS)).to_numpy()),
        ("TEST (2324-2526)", d["season"].isin(config.TEST_SEASONS).to_numpy()),
    )
    for etichetta, mask in splits:
        print(f"\n{etichetta}")
        for regione, sel in (
            (f"p_home < {mediana:.3f}", p_home < mediana),
            (f"p_home >= {mediana:.3f}", p_home >= mediana),
            ("tutte", np.ones(len(p_home), dtype=bool)),
        ):
            m = mask & sel
            p, y = p_home[m], y_home[m]
            atteso, oss = p.sum(), y.sum()
            var = (p * (1 - p)).sum()
            z = (oss - atteso) / np.sqrt(var)
            print(f"  {regione:22} attesi {atteso:6.1f}  osservati {int(oss):4d}  "
                  f"su {int(m.sum()):4d} partite  z = {z:+.2f}  "
                  f"{'*' if abs(z) > 1.96 else ''}")


def _low_region_stats(p: np.ndarray, y: np.ndarray, soglia: float = 0.25) -> tuple[float, float, float, int]:
    """
    Attesi, osservati e varianza nella regione a bassa probabilita'.

    L'aggregazione e' per PARTITA, non per coppia (p, esito), e la differenza
    non e' cosmetica. I tre esiti di una partita sono mutuamente esclusivi:
    trattarli come tre osservazioni indipendenti gonfia la varianza e rende il
    test troppo prudente. Correttamente, per ogni partita si somma la
    probabilita' degli esiti sotto soglia — ne esce una singola Bernoulli con
    probabilita' q — e la varianza e' q(1-q) sommata sulle partite, che sono
    davvero indipendenti fra loro.
    """
    low = p < soglia
    q = (p * low).sum(axis=1)
    hit = (y * low).sum(axis=1)
    played = q > 0
    return float(q[played].sum()), float(hit[played].sum()), float((q * (1 - q))[played].sum()), int(played.sum())


# ---------------------------------------------------------------------------

def report(preds: pd.DataFrame, reference: str = "M1b market-only (diretto)") -> pd.DataFrame:
    """
    La tabella unica: metriche, quota coperta e confronto appaiato col mercato.

    Sta tutto insieme di proposito. Un RPS senza il suo intervallo appaiato
    invita a leggere come differenza quello che e' rumore, e un intervallo
    senza `skill_closed` non dice se il modello sia arrivato a meta' strada o
    a un decimo.
    """
    tab = compare(preds, reference=reference)
    paired = paired_comparison(preds, reference=reference)

    tab["delta_mercato"] = paired["differenza"]
    tab["ic_95"] = [
        "riferimento" if m == reference
        else f"[{paired.loc[m, 'ic_basso']:+.5f}, {paired.loc[m, 'ic_alto']:+.5f}]"
        for m in tab.index
    ]
    tab["verdetto"] = [
        "riferimento" if m == reference
        else "batte il mercato" if paired.loc[m, "ic_alto"] < 0
        else "peggio del mercato" if paired.loc[m, "ic_basso"] > 0
        else "indistinguibile dal mercato"
        for m in tab.index
    ]
    cols = ["RPS", "skill_closed", "delta_mercato", "ic_95", "verdetto",
            "log_loss", "Brier", "accuratezza", "ECE"]
    out = tab[[c for c in cols if c in tab.columns]].copy()
    for c in ("RPS", "skill_closed", "log_loss", "Brier", "accuratezza", "ECE"):
        if c in out.columns:
            out[c] = out[c].round(4)
    out["delta_mercato"] = out["delta_mercato"].round(5)
    return out


# ---------------------------------------------------------------------------
# Misura di un blocco di feature
# ---------------------------------------------------------------------------

def colonne_blocco(nome: str, df: pd.DataFrame) -> list[str]:
    """Le colonne che un blocco aggiunge, quelle presenti nel dataset."""
    from .features.context import FEATURES_CONTEXT, FEATURES_DERBY
    from .features.players import FEATURES_PLAYERS

    blocchi = {
        "contesto": FEATURES_CONTEXT + FEATURES_DERBY,
        "giocatori": FEATURES_PLAYERS,
    }
    if nome not in blocchi:
        raise ValueError(f"blocco ignoto '{nome}': disponibili {sorted(blocchi)}")
    return [c for c in blocchi[nome] if c in df.columns]


def misura_blocco(nome: str, floor: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Il protocollo di misura di un blocco, in un pezzo solo.

    TRE MODELLI SULLE STESSE RIGHE
      M1b     il mercato, riferimento;
      M5      ancorato al mercato, SENZA le colonne del blocco;
      M5+     lo stesso, con le colonne.

    Perche' M5 e non M4: l'ancoraggio elimina il lavoro di ricostruire il
    mercato dagli ingressi, quindi tutta la capacita' del modello punta sul
    residuo — che e' l'unica cosa che il blocco puo' spiegare. E se non c'e'
    segnale M5 degenera esattamente nel mercato (verificato: azzerando le
    feature l'arresto anticipato si ferma a 1 albero e lo scarto logaritmico e'
    0.00000), quindi un risultato nullo e' un risultato, non un difetto.

    Le colonne si tolgono dal MODELLO, non dal dataset: cosi' le due varianti
    girano sulle stesse partite e il confronto e' appaiato riga per riga.

    IL CONFRONTO CHE DECIDE E' M5+ CONTRO M5, non contro il mercato. Contro il
    mercato si misura il livello del modello; contro M5 si misura il blocco, ed
    e' l'unica differenza in cui il blocco e' l'unica cosa cambiata.
    """
    from .models.baseline import BaseRate, MarketDirect
    from .models.gbm import MarketAnchoredGBM

    df = load_dataset()
    colonne = colonne_blocco(nome, df)
    if not colonne:
        raise ValueError(
            f"nessuna colonna del blocco '{nome}' nel dataset: "
            f"il parquet delle feature e' stato costruito?"
        )
    log.info("blocco '%s': %d colonne -> %s", nome, len(colonne), colonne)

    # La base e' il modello di produzione: tutte le feature tranne i blocchi
    # gia' misurati e scartati. La variante aggiunge SOLO il blocco in esame —
    # non anche gli altri blocchi bocciati, che tornerebbero dentro di
    # straforo e renderebbero la differenza non attribuibile.
    from .models.gbm import FUORI_DAL_MODELLO

    senza = MarketAnchoredGBM(escludi=tuple(FUORI_DAL_MODELLO | set(colonne)),
                              **config.GBM_PARAMS_ANCHORED)
    senza.suffisso = f" (senza {nome})"
    con = MarketAnchoredGBM(escludi=tuple(FUORI_DAL_MODELLO - set(colonne)),
                            **config.GBM_PARAMS_ANCHORED)
    con.suffisso = f" (con {nome})"

    modelli = [MarketDirect(), senza, con]
    if floor:
        # Serve solo a dare un senso a skill_closed: senza il pavimento, la
        # quota di distanza coperta non e' calcolabile.
        modelli.insert(0, BaseRate())

    preds = walk_forward(df, modelli)
    coppie = [(con.name, senza.name),
              (con.name, "M1b market-only (diretto)"),
              (senza.name, "M1b market-only (diretto)")]
    return preds, paired_pairs(preds, coppie)


def diagnostica_nan(nome: str) -> pd.DataFrame:
    """
    Il blocco spiega il CONTENUTO, o solo la disponibilita' del dato?

    IL PROBLEMA. Un blocco a copertura parziale ha NaN su tutte le stagioni
    che lo scraping non raggiunge. LightGBM tratta il NaN come una direzione
    di split: puo' separare "so" da "non so" e guadagnare, perche' quella
    separazione coincide con "prima o dopo il 2021/22" — cioe' con il tempo,
    non con gli infortuni. L'importanza risulterebbe alta e il blocco
    sembrerebbe informativo mentre sta solo leggendo un calendario.

    LA VERIFICA. Si addestra due volte: su tutto il training, e sulle sole
    stagioni dove il blocco e' coperto — dove di NaN non ce ne sono. Se
    l'importanza delle colonne nuove regge, il modello usa il contenuto. Se
    crolla, stava splittando sulla disponibilita'.

    Non e' un test sul RPS e non decide se tenere il blocco: decide come
    leggere la sua importanza.
    """
    from .models.gbm import FUORI_DAL_MODELLO, MarketAnchoredGBM

    df = load_dataset()
    colonne = colonne_blocco(nome, df)
    presente = df[colonne[0]].notna()
    stagioni_coperte = sorted(df.loc[presente, "season"].unique())
    log.info("blocco '%s' coperto nelle stagioni %s", nome, stagioni_coperte)

    played = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    escludi = tuple(FUORI_DAL_MODELLO - set(colonne))

    righe = []
    for etichetta, dati in (
        ("tutto il training (con NaN)", played),
        ("solo stagioni coperte (senza NaN)",
         played[played["season"].isin(stagioni_coperte)]),
    ):
        quote = []
        for stagione in config.TEST_SEASONS:
            cutoff = df.loc[df["season"] == stagione, "date"].min()
            train = dati[dati["date"] < cutoff]
            if len(train) < 500:
                log.warning("%s, stagione %s: solo %d righe, saltata",
                            etichetta, stagione, len(train))
                continue
            m = MarketAnchoredGBM(escludi=escludi,
                                  **config.GBM_PARAMS_ANCHORED).fit(train)
            if m.model_home_ is None:
                continue
            for mod in (m.model_home_, m.model_away_):
                gain = mod.booster_.feature_importance(importance_type="gain")
                s = pd.Series(gain / max(gain.sum(), 1e-12), index=m.features_)
                quote.append(s.reindex(colonne).fillna(0.0).sum())
        righe.append({"training": etichetta, "n_fit": len(quote),
                      "quota_blocco": float(np.mean(quote)) if quote else np.nan})
    return pd.DataFrame(righe)


def all_models() -> list[Model]:
    """
    Il roster completo. Sta qui e non in baseline.py perche' dixon_coles e gbm
    importano da baseline: metterlo la' chiuderebbe un ciclo di import.

    Gli iperparametri sono quelli scelti sulla VALIDAZIONE, non sul test:
      - half-life di M3, da `python -m src.models.dixon_coles --tune`
      - iperparametri di M4, da `python -m src.models.gbm --tune`
    Se si ritarano, vanno aggiornati in config.py e va rifatta la tabella.
    """
    from .models.dixon_coles import DixonColes
    from .models.gbm import LogBlend, MarketAnchoredGBM, PoissonGBM

    return default_models() + [
        DixonColes(halflife_days=config.DC_HALFLIFE),
        PoissonGBM(use_market=False, **config.GBM_PARAMS_NO_MARKET),
        PoissonGBM(use_market=True, **config.GBM_PARAMS_MARKET),
        MarketAnchoredGBM(**config.GBM_PARAMS_ANCHORED),
        LogBlend(weight=config.BLEND_WEIGHT, **config.GBM_PARAMS_NO_MARKET),
    ]


def run(save: bool = True, models: list[Model] | None = None) -> pd.DataFrame:
    df = load_dataset()
    preds = walk_forward(df, models or all_models())
    if save:
        dst = config.PROCESSED / "walk_forward_predictions.parquet"
        preds.to_parquet(dst, index=False)
        log.info("scritto %s", dst.name)
    return preds


def main() -> None:
    ap = argparse.ArgumentParser(description="Valutazione: metriche, calibrazione, walk-forward")
    ap.add_argument("--calibration", action="store_true", help="curve di calibrazione ed ECE per modello")
    ap.add_argument("--bias", action="store_true", help="distorsione favorito-sfavorito per stagione")
    ap.add_argument("--no-save", action="store_true", help="non scrivere le previsioni su disco")
    ap.add_argument("--blocco", help="misura un blocco di feature con M5 ancorato "
                                     "(oggi: 'contesto', 'giocatori')")
    ap.add_argument("--blocco-nan", dest="blocco_nan",
                    help="il blocco spiega il contenuto o solo la "
                         "disponibilita' del dato? Riaddestra sulle sole "
                         "stagioni coperte e confronta l'importanza")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)

    if args.bias:
        bias_report(load_dataset())
        return

    if args.blocco_nan:
        tab = diagnostica_nan(args.blocco_nan)
        print(f"\n=== BLOCCO '{args.blocco_nan.upper()}': CONTENUTO O "
              f"DISPONIBILITA'? ===")
        print("Quota del guadagno presa dalle colonne del blocco, media sui "
              "fit di test.\n")
        print(tab.to_string(index=False))
        if tab["quota_blocco"].notna().all() and len(tab) == 2:
            con, senza = tab["quota_blocco"]
            if con > 0 and senza / con < 0.5:
                print("\n-> L'importanza CROLLA senza i NaN: il modello stava "
                      "splittando\n   sulla disponibilita' del dato, non sul "
                      "contenuto.")
            else:
                print("\n-> L'importanza regge anche senza NaN: il modello usa "
                      "il contenuto.")
        return

    if args.blocco:
        preds, coppie = misura_blocco(args.blocco)
        if not args.no_save:
            dst = config.PROCESSED / f"walk_forward_blocco_{args.blocco}.parquet"
            preds.to_parquet(dst, index=False)
            log.info("scritto %s", dst.name)

        print(f"\n=== BLOCCO '{args.blocco.upper()}' — walk-forward sul test set ===")
        print(report(preds).to_string())

        print("\n=== CONFRONTI APPAIATI (bootstrap a cluster sulla giornata) ===")
        print("Il primo e' quello che decide: e' l'unica differenza in cui il")
        print("blocco e' l'unica cosa cambiata.\n")
        print(coppie.round(5).to_string())

        decisivo = coppie.iloc[0]
        print(f"\n=== VERDETTO SUL BLOCCO '{args.blocco}' ===")
        print(f"differenza {decisivo['differenza']:+.5f}  "
              f"IC 95% [{decisivo['ic_basso']:+.5f}, {decisivo['ic_alto']:+.5f}]  "
              f"su {int(decisivo['n_cluster'])} cluster")
        if decisivo["ic_alto"] < 0:
            print("-> L'INTERVALLO STA SOTTO ZERO: il blocco aggiunge. Si tiene.")
        elif decisivo["ic_basso"] > 0:
            print("-> L'intervallo sta sopra zero: il blocco PEGGIORA. Va rimosso.")
        else:
            print("-> L'intervallo contiene lo zero: il blocco non e' distinguibile")
            print("   dal rumore. Va RIMOSSO, non tenuto 'male che vada non fa")
            print("   danno': con 3400 righe di training diluisce e basta.")
        return

    preds = run(save=not args.no_save)
    reference = "M1b market-only (diretto)"
    tab = report(preds, reference=reference)

    print("\n=== TEST SET " + ", ".join(config.TEST_SEASONS) + " — 1140 partite ===")
    print(tab.to_string())
    print("""
RPS            metrica primaria, piu' basso e' meglio
skill_closed   quota della distanza fra il pavimento (M0b) e il mercato che il
               modello ha coperto. 1.0 = pari al mercato, >1 = lo batte
delta_mercato  differenza di RPS APPAIATA partita per partita contro il mercato.
               Positiva = peggiore del mercato
ic_95          intervallo bootstrap sulla differenza, con cluster sulla giornata.
               Se contiene lo zero, la differenza non e' distinguibile dal rumore
log_loss inf   il modello assegna probabilita' zero a un esito accaduto""")

    print("\n=== CONFRONTI DIRETTI FRA MODELLI ===")
    gbm_no = "M4 GBM senza mercato"
    gbm_si = "M4 GBM con mercato"
    anc = "M5 GBM ancorato al mercato"
    blend = f"M6 miscela log w={config.BLEND_WEIGHT:.2f}"
    dc = f"M3 Dixon-Coles hl={config.DC_HALFLIFE:g}g"
    pairs = paired_pairs(preds, [
        (gbm_no, dc),            # forma e xG contro forza stimata dai risultati
        (gbm_no, "M2 GLM Poisson"),
        (dc, "M2 GLM Poisson"),  # quanto valgono decadimento e rho
        (gbm_si, gbm_no),        # quanto aggiunge il mercato al GBM
        (anc, reference),        # IL TEST DECISIVO
        (blend, reference),      # e il suo controllo povero
        (anc, gbm_si),           # ancorare batte il dare le quote come feature?
    ])
    if not pairs.empty:
        print(pairs[["differenza", "ic_basso", "ic_alto", "conclusione"]].round(5).to_string())

    if args.calibration:
        keep = common_rows(preds)
        print("\n=== CALIBRAZIONE PER MODELLO (tre esiti impilati) ===")
        for name, g in keep.groupby("model", observed=True, sort=False):
            p = g[PROB_COLS].to_numpy(dtype=float)
            y = outcome_index(g["FTR"])
            print(f"\n-- {name}   ECE impilato {ece_pooled(p, y):.4f}   "
                  f"ECE su classe piu' probabile {ece_multiclass(p, y):.4f}")
            print(calibration_table(p.reshape(-1), _onehot(y).reshape(-1)).round(4).to_string(index=False))


if __name__ == "__main__":
    main()
