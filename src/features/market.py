"""
Feature di mercato: de-vigging delle quote e grandezze implicite.

PERCHE' IL DE-VIGGING
La quota di un book non e' una probabilita'. Il reciproco 1/quota e' una
probabilita' *gonfiata*: la somma sui tre esiti vale ~1.05, non 1. Quel 5% e'
il margine del banco. Usare 1/quota come feature significa dare in pasto al
modello tre numeri che non sommano a uno e il cui errore non e' uniforme fra
gli esiti. Il de-vigging toglie il margine e restituisce la stima di
probabilita' che il mercato esprime davvero.

I DUE METODI, E PERCHE' NON BASTA IL PRIMO

1. Proporzionale (o multiplicativo): p_i = (1/o_i) / somma(1/o_j).
   Assume che il book carichi il margine in proporzione alla probabilita' di
   ciascun esito. E' l'ipotesi piu' semplice ed e' quasi certamente falsa:
   il margine sull'esito improbabile e' sistematicamente piu' alto. Chi
   normalizza e basta eredita la distorsione favorito-sfavorito, cioe'
   sovrastima gli esiti a quota alta.

2. Shin (1992): il margine e' la difesa del book contro gli scommettitori
   informati, che sono una frazione z del volume. Sotto quel modello

       p_i = [ sqrt(z^2 + 4(1-z) * pi_i^2 / PI) - z ] / (2 (1-z))

   con pi_i = 1/o_i e PI = somma(pi_i), e z scelto perche' somma(p_i) = 1.
   Ne esce un margine che grava di piu' sugli esiti improbabili: e'
   empiricamente meglio calibrato, e z stesso e' informativo (misura quanta
   asimmetria informativa il book percepisce su quella partita).

Si calcolano entrambi. Shin e' il default; il proporzionale resta come
colonna separata per poter misurare quanto la scelta pesa sull'RPS.

QUALE BOOK, E PERCHE' L'APERTURA E NON LA CHIUSURA
La verifica di copertura sui dati reali (vedi `--coverage`) dice che:
  - B365 in apertura e' l'unico con terzina completa al 100% su tutte e 13 le
    stagioni. E' il riferimento;
  - Pinnacle, che sarebbe il book piu' efficiente, e' morto: 52% nel 2025/26,
    0% nel 2026/27. Inutilizzabile in produzione, per quanto sia ottimo nello
    storico. Resta solo come ripiego sulle stagioni vecchie;
  - le colonne aggregate `Max`/`Avg` e le chiusure `*C` partono dal 2019/20;
    prima c'erano gli aggregati Betbrain `Bb*`, che finiscono nel 2018/19.
    Le due famiglie non si sovrappongono mai: sono lo stesso dato con due nomi
    in due epoche diverse.

La scelta e' **l'apertura**, coerente in backtest e in produzione (regola 6),
e la ragione e' l'orizzonte T-24h: la quota di chiusura si forma pochi minuti
prima del calcio d'inizio e incorpora le formazioni ufficiali. Usarla in
addestramento darebbe un modello che in produzione non si puo' alimentare, e
che nel backtest sembrerebbe migliore di quanto sia. Le colonne di chiusura
vengono comunque calcolate, ma stanno in `FEATURES_CLOSING` e NON vanno usate
come feature: servono a misurare il drift come diagnostica di mercato.

Uso:
    python -m src.features.market                # costruisce le feature
    python -m src.features.market --coverage     # tabella copertura per stagione
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .. import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("market")

KEYS = ["league", "season", "home_team", "away_team"]

# Catene di ripiego, in ordine di preferenza. Il primo book con la terzina
# completa vince, riga per riga. Serve solo a tappare buchi sporadici: la
# colonna `mkt_1x2_source` registra chi ha risposto, e il log dice quante
# righe hanno usato un ripiego.
BOOKS_1X2 = ["B365", "BW", "IW", "PS", "WH", "VC", "Avg", "BbAv"]
BOOKS_1X2_CLOSING = ["B365C", "BWC", "IWC", "PSC", "WHC", "VCC", "AvgC"]

# Over/Under 2.5. Qui il ripiego non e' sporadico ma strutturale: B365 sui gol
# non esiste prima del 2019/20, e quelle cinque stagioni le copre la media
# Betbrain. E' un consenso di mercato, non un singolo book, quindi ha un
# margine leggermente diverso: da qui `mkt_ou_source`, che il GBM puo' usare
# come categoriale per assorbire lo scalino.
BOOKS_OU = ["B365", "P", "Avg", "BbAv", "BFE"]
BOOKS_OU_CLOSING = ["B365C", "PC", "AvgC", "BFEC"]

# Soglia del mercato gol usata. Cambiarla significa cambiare le colonne lette.
OU_LINE = 2.5


# ---------------------------------------------------------------------------
# Selezione delle quote
# ---------------------------------------------------------------------------

def cols_1x2(book: str) -> tuple[str, ...]:
    return (f"{book}H", f"{book}D", f"{book}A")


def cols_ou(book: str) -> tuple[str, ...]:
    return (f"{book}>{OU_LINE}", f"{book}<{OU_LINE}")


def pick_odds(
    df: pd.DataFrame,
    books: list[str],
    cols_of,
    label: str,
) -> tuple[np.ndarray, pd.Series]:
    """
    Prima terzina (o coppia) completa disponibile, riga per riga.

    Una quota si accetta solo se e' finita e maggiore di 1: le quote decimali
    minori o uguali a 1 sono errori di dato, non offerte reali, e in un
    reciproco produrrebbero probabilita' oltre l'unita'.
    """
    n = len(df)
    k = len(cols_of(books[0]))
    odds = np.full((n, k), np.nan)
    source = np.full(n, "", dtype=object)

    for book in books:
        cols = list(cols_of(book))
        if any(c not in df.columns for c in cols):
            continue
        block = df[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        usable = np.isfinite(block).all(axis=1) & (block > 1.0).all(axis=1)
        empty = ~np.isfinite(odds).all(axis=1)
        fill = usable & empty
        if fill.any():
            odds[fill] = block[fill]
            source[fill] = book

    got = np.isfinite(odds).all(axis=1)
    primary = int((source == books[0]).sum())
    log.info(
        "%s: %d/%d righe coperte (%.1f%%), di cui %d dal book primario %s",
        label, got.sum(), n, 100 * got.sum() / n, primary, books[0],
    )
    fallback = pd.Series(source[got & (source != books[0])]).value_counts()
    if not fallback.empty:
        log.info("%s: ripieghi usati -> %s", label, fallback.to_dict())

    return odds, pd.Series(np.where(got, source, None), index=df.index, dtype=object)


# ---------------------------------------------------------------------------
# De-vigging
# ---------------------------------------------------------------------------

def devig_proportional(odds: np.ndarray) -> np.ndarray:
    """Normalizzazione semplice: il margine si spalma in proporzione a 1/quota."""
    pi = 1.0 / odds
    return pi / pi.sum(axis=1, keepdims=True)


def _shin_p(pi: np.ndarray, overround: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Probabilita' di Shin a z fissato. Formula chiusa, il difficile e' z."""
    root = np.sqrt(z**2 + 4.0 * (1.0 - z) * pi**2 / overround)
    return (root - z) / (2.0 * (1.0 - z))


def devig_shin(odds: np.ndarray, iters: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """
    De-vigging di Shin. Restituisce (probabilita', z).

    z si trova per bisezione e non con Newton perche' la somma delle p_i e'
    monotona decrescente in z e i bordi sono noti: in z=0 la somma vale
    sqrt(overround) >= 1, e cresce z fino ad annullare l'eccesso. Sessanta
    bisezioni su [0, 0.99] danno precisione ben oltre il necessario, e
    vettorializzate costano meno di una iterazione di Newton scalare per riga.

    Caso limite: se l'overround e' <= 1 (succede sulle colonne `Max`, dove il
    massimo di mercato puo' produrre un libro in arbitraggio) il modello di
    Shin non ha soluzione con z positivo. Li' si ricade sul proporzionale con
    z=0, che e' esattamente il limite della formula.
    """
    n = len(odds)
    p = np.full(odds.shape, np.nan)
    z_out = np.full(n, np.nan)

    valid = np.isfinite(odds).all(axis=1) & (odds > 1.0).all(axis=1)
    if not valid.any():
        return p, z_out

    pi = 1.0 / odds[valid]
    overround = pi.sum(axis=1, keepdims=True)

    lo = np.zeros((valid.sum(), 1))
    hi = np.full((valid.sum(), 1), 0.99)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        excess = _shin_p(pi, overround, mid).sum(axis=1, keepdims=True) - 1.0
        lo = np.where(excess > 0, mid, lo)
        hi = np.where(excess > 0, hi, mid)
    z = 0.5 * (lo + hi)

    ps = _shin_p(pi, overround, z)
    # Rifinitura: la bisezione lascia un residuo dell'ordine di 1e-18, ma la
    # somma dev'essere esattamente 1 perche' a valle ci si costruisce l'RPS.
    ps = ps / ps.sum(axis=1, keepdims=True)

    # Libro in arbitraggio: nessun z positivo risolve, si torna al proporzionale.
    degenerate = (overround <= 1.0).ravel()
    if degenerate.any():
        ps[degenerate] = (pi / overround)[degenerate]
        z[degenerate] = 0.0
        log.warning("%d righe con overround <= 1: de-vigging proporzionale", degenerate.sum())

    p[valid] = ps
    z_out[valid] = z.ravel()
    return p, z_out


# ---------------------------------------------------------------------------
# Grandezze implicite sulla scala dei gol
# ---------------------------------------------------------------------------

def implied_total_goals(p_over: np.ndarray, line: float = OU_LINE) -> np.ndarray:
    """
    Lambda totale che, sotto Poisson, riproduce la probabilita' di over.

    Serve a portare il mercato sulla stessa scala del target. La P(over) e'
    strettamente crescente in lambda, quindi bisezione su [0.05, 8]: nessun
    campionato ha mai una media credibile fuori da li'.
    """
    out = np.full(len(p_over), np.nan)
    valid = np.isfinite(p_over) & (p_over > 0) & (p_over < 1)
    if not valid.any():
        return out

    target = p_over[valid]
    k = int(np.floor(line))  # over 2.5 <=> almeno 3 gol <=> sf(2, lambda)
    lo = np.full(target.shape, 0.05)
    hi = np.full(target.shape, 8.0)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        too_low = poisson.sf(k, mid) < target
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    out[valid] = 0.5 * (lo + hi)
    return out


def implied_lambdas(
    p_home: np.ndarray,
    total: np.ndarray,
    max_goals: int = config.MAX_GOALS,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Scompone il lambda totale in lambda casa e trasferta.

    Dati il totale implicito dal mercato gol e la P(1) implicita dall'1X2, si
    cerca la supremazia d tale che, con lambda_casa = (totale + d)/2 e
    lambda_trasferta = (totale - d)/2 e due Poisson indipendenti, la
    probabilita' di vittoria casalinga coincida con quella del mercato.

    E' il benchmark market-only richiesto dal documento: una previsione a gol
    costruita solo dalle quote, confrontabile riga per riga con il modello.
    L'indipendenza fra i due Poisson e' una semplificazione deliberata — e'
    esattamente la correzione sui punteggi bassi che Dixon-Coles introdurra' —
    ma qui basta, perche' serve un riferimento, non il modello finale.
    """
    n = len(total)
    lam_h = np.full(n, np.nan)
    lam_a = np.full(n, np.nan)
    valid = np.isfinite(p_home) & np.isfinite(total) & (total > 0)
    if not valid.any():
        return lam_h, lam_a

    ph = p_home[valid]
    tot = total[valid]
    goals = np.arange(max_goals + 1)

    def p_home_win(d: np.ndarray) -> np.ndarray:
        """P(gol casa > gol trasferta) con due Poisson indipendenti."""
        h = np.clip((tot + d) / 2.0, 1e-6, None)[:, None]
        a = np.clip((tot - d) / 2.0, 1e-6, None)[:, None]
        pmf_h = poisson.pmf(goals[None, :], h)
        pmf_a = poisson.pmf(goals[None, :], a)
        joint = pmf_h[:, :, None] * pmf_a[:, None, :]
        win = np.triu(np.ones((max_goals + 1, max_goals + 1)), k=1).T
        return (joint * win[None, :, :]).sum(axis=(1, 2))

    # La supremazia non puo' superare il totale: lambda negativi non esistono.
    lo = np.maximum(-tot + 1e-6, -6.0)
    hi = np.minimum(tot - 1e-6, 6.0)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        too_low = p_home_win(mid) < ph
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    d = 0.5 * (lo + hi)

    lam_h[valid] = (tot + d) / 2.0
    lam_a[valid] = (tot - d) / 2.0
    return lam_h, lam_a


# ---------------------------------------------------------------------------
# Assemblaggio
# ---------------------------------------------------------------------------

def market_block(df: pd.DataFrame, books_1x2: list[str], books_ou: list[str], prefix: str) -> pd.DataFrame:
    """Un blocco completo di feature di mercato per una famiglia di colonne."""
    out = pd.DataFrame(index=df.index)

    odds, source = pick_odds(df, books_1x2, cols_1x2, f"{prefix}1X2")
    p_shin, z = devig_shin(odds)
    p_prop = devig_proportional(odds)

    out[f"{prefix}p_home"] = p_shin[:, 0]
    out[f"{prefix}p_draw"] = p_shin[:, 1]
    out[f"{prefix}p_away"] = p_shin[:, 2]
    out[f"{prefix}p_home_prop"] = p_prop[:, 0]
    out[f"{prefix}p_draw_prop"] = p_prop[:, 1]
    out[f"{prefix}p_away_prop"] = p_prop[:, 2]
    out[f"{prefix}overround"] = (1.0 / odds).sum(axis=1)
    out[f"{prefix}shin_z"] = z
    out[f"{prefix}1x2_source"] = source

    # Supremazia in log-odds: piu' lineare della differenza di probabilita',
    # che si schiaccia agli estremi.
    with np.errstate(divide="ignore", invalid="ignore"):
        out[f"{prefix}supremacy"] = np.log(p_shin[:, 0] / p_shin[:, 2])
        # Entropia: quanto il mercato considera incerta la partita. Alta
        # incertezza sull'esito e' correlata a partite bloccate, quindi e'
        # informativa sui gol anche al netto dell'1X2.
        out[f"{prefix}entropy"] = -(p_shin * np.log(np.clip(p_shin, 1e-12, None))).sum(axis=1)

    ou_odds, ou_source = pick_odds(df, books_ou, cols_ou, f"{prefix}O/U {OU_LINE}")
    p_ou, z_ou = devig_shin(ou_odds)
    out[f"{prefix}p_over25"] = p_ou[:, 0]
    out[f"{prefix}p_under25"] = p_ou[:, 1]
    out[f"{prefix}ou_overround"] = (1.0 / ou_odds).sum(axis=1)
    out[f"{prefix}ou_shin_z"] = z_ou
    out[f"{prefix}ou_source"] = ou_source

    total = implied_total_goals(p_ou[:, 0])
    out[f"{prefix}total_goals"] = total
    lam_h, lam_a = implied_lambdas(p_shin[:, 0], total)
    out[f"{prefix}lambda_home"] = lam_h
    out[f"{prefix}lambda_away"] = lam_a

    return out


def validate(df: pd.DataFrame, prefix: str = "mkt_") -> None:
    """
    Controlli che devono valere per costruzione. Se saltano, il de-vigging e'
    rotto e ogni cosa a valle e' rumore: meglio fermarsi qui.
    """
    trio = [f"{prefix}p_home", f"{prefix}p_draw", f"{prefix}p_away"]
    have = df[trio].notna().all(axis=1)
    s = df.loc[have, trio].sum(axis=1)
    assert np.allclose(s, 1.0, atol=1e-9), f"le probabilita' 1X2 non sommano a 1: max scarto {abs(s - 1).max():.2e}"

    pair = [f"{prefix}p_over25", f"{prefix}p_under25"]
    have_ou = df[pair].notna().all(axis=1)
    s_ou = df.loc[have_ou, pair].sum(axis=1)
    assert np.allclose(s_ou, 1.0, atol=1e-9), "le probabilita' over/under non sommano a 1"

    over = df[f"{prefix}overround"].dropna()
    assert (over > 1.0).mean() > 0.99, "overround <= 1 su troppe righe: colonne quote sbagliate?"

    z = df[f"{prefix}shin_z"].dropna()
    assert (z >= 0).all() and (z < 0.5).all(), "z di Shin fuori scala"

    # Firma del metodo: Shin carica il margine sugli esiti improbabili, quindi
    # rispetto al proporzionale alza il favorito e abbassa lo sfavorito.
    # Il favorito e' l'esito a probabilita' massima fra i tre, non "la casa":
    # su 2 righe delle 4580 il favorito e' il pareggio.
    shin = df.loc[have, trio].to_numpy()
    prop = df.loc[have, [c + "_prop" for c in trio]].to_numpy()
    r = np.arange(len(shin))
    fav, dog = shin.argmax(axis=1), shin.argmin(axis=1)
    assert (shin[r, fav] - prop[r, fav] >= -1e-12).all(), "Shin non alza il favorito: formula sbagliata"
    assert (shin[r, dog] - prop[r, dog] <= 1e-12).all(), "Shin non abbassa lo sfavorito: formula sbagliata"

    log.info("controlli superati su %d righe con 1X2 e %d con over/under", have.sum(), have_ou.sum())


# Feature calcolabili a T-24h: sono queste che vanno nel modello.
FEATURES_T24 = [
    "mkt_p_home", "mkt_p_draw", "mkt_p_away",
    "mkt_p_home_prop", "mkt_p_draw_prop", "mkt_p_away_prop",
    "mkt_overround", "mkt_shin_z", "mkt_supremacy", "mkt_entropy",
    "mkt_p_over25", "mkt_p_under25", "mkt_ou_overround", "mkt_ou_shin_z",
    "mkt_total_goals", "mkt_lambda_home", "mkt_lambda_away",
    "mkt_1x2_source", "mkt_ou_source",
]

# NON usare come feature: la quota di chiusura si forma dopo le formazioni
# ufficiali, fuori dall'orizzonte T-24h. Restano per diagnostica di mercato.
FEATURES_CLOSING = [
    "close_p_home", "close_p_draw", "close_p_away", "close_p_over25",
    "close_overround", "close_shin_z", "close_total_goals",
    "drift_p_home", "drift_p_away", "drift_p_over25", "drift_abs",
]


def build(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        path = config.INTERIM / "matches_master.parquet"
        df = pd.read_parquet(path)
        log.info("caricato %s: %d righe", path.name, len(df))

    out = df[KEYS + ["date"]].copy()

    opening = market_block(df, BOOKS_1X2, BOOKS_OU, prefix="mkt_")
    out = pd.concat([out, opening], axis=1)
    validate(out, prefix="mkt_")

    closing = market_block(df, BOOKS_1X2_CLOSING, BOOKS_OU_CLOSING, prefix="close_")
    keep = [c for c in closing.columns if c in FEATURES_CLOSING]
    out = pd.concat([out, closing[keep]], axis=1)

    # Drift: quanto il mercato si e' mosso fra apertura e chiusura. Non e' una
    # feature (usa informazione posteriore a T-24h) ma dice se la partita ha
    # avuto notizie rilevanti dopo il momento della previsione: utile per
    # capire *dove* il modello sbaglia, non per fargli indovinare.
    #
    # ATTENZIONE sulle prime cinque stagioni: B365C non esiste prima del
    # 2019/20 e la chiusura arriva da Pinnacle (1900 righe). Li' il drift
    # confronta l'apertura di un book con la chiusura di un altro, quindi
    # contiene anche la differenza di opinione fra i due, non solo il
    # movimento nel tempo. Dal 2019/20 in poi il confronto e' B365 su B365 ed
    # e' pulito.
    out["drift_p_home"] = out["close_p_home"] - out["mkt_p_home"]
    out["drift_p_away"] = out["close_p_away"] - out["mkt_p_away"]
    out["drift_p_over25"] = out["close_p_over25"] - out["mkt_p_over25"]
    out["drift_abs"] = out[["drift_p_home", "drift_p_away"]].abs().sum(axis=1)

    dst = config.PROCESSED / "features_market.parquet"
    out.to_parquet(dst, index=False)
    log.info("scritto %s: %d righe, %d colonne", dst.name, len(out), out.shape[1])

    cov = out.groupby("season")[["mkt_p_home", "mkt_p_over25", "close_p_home"]].apply(
        lambda g: g.notna().mean().mul(100).round(1)
    )
    log.info("copertura %% per stagione:\n%s", cov.to_string())
    return out


# ---------------------------------------------------------------------------

def coverage_report(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Copertura per stagione delle terzine 1X2 e delle coppie over/under, book
    per book. E' la verifica che ha deciso quale book usare: si rilancia dopo
    ogni ingestion, perche' i book vanno e vengono (Pinnacle si e' spento nel
    2025/26 senza preavviso).
    """
    if df is None:
        df = pd.read_parquet(config.INTERIM / "matches_master.parquet")

    rows: dict[str, pd.Series] = {}
    for book in BOOKS_1X2 + BOOKS_1X2_CLOSING:
        cols = list(cols_1x2(book))
        if any(c in df.columns for c in cols):
            present = [c for c in cols if c in df.columns]
            ok = df[present].notna().all(axis=1) if len(present) == 3 else pd.Series(False, index=df.index)
            rows[book] = ok.groupby(df["season"]).mean().mul(100)
    for book in BOOKS_OU + BOOKS_OU_CLOSING:
        cols = list(cols_ou(book))
        if all(c in df.columns for c in cols):
            rows[f"{book}_OU"] = df[cols].notna().all(axis=1).groupby(df["season"]).mean().mul(100)

    table = pd.DataFrame(rows).round(1)
    table["n"] = df.groupby("season").size()
    return table


def main() -> None:
    ap = argparse.ArgumentParser(description="Feature di mercato: de-vigging delle quote")
    ap.add_argument("--coverage", action="store_true", help="stampa la copertura per stagione e esce")
    args = ap.parse_args()

    if args.coverage:
        pd.set_option("display.width", 250)
        pd.set_option("display.max_columns", 100)
        print(coverage_report().to_string())
        return

    build()


if __name__ == "__main__":
    main()
