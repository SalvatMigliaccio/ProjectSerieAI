"""
Feature di forma recente: medie mobili esponenziali, leakage-safe.

IDEA CENTRALE
La tabella partite e' "larga": una riga per partita, con colonne separate per
casa e trasferta. Ma la forma e' una proprieta' della SQUADRA, non della
partita. Quindi si passa a formato "lungo" (una riga per squadra per partita),
si calcolano le medie mobili per squadra ordinate nel tempo, e si torna al
formato largo agganciando i valori a casa e trasferta.

LE DUE REGOLE CHE RENDONO TUTTO CORRETTO

1. Il valore assegnato a una partita e' lo stato PRIMA di quella partita.
   Nel ciclo si scrive l'output e solo dopo si aggiorna lo stato. Cosi' una
   feature non puo' mai contenere informazione della partita che deve predire.

2. Le finestre NON si azzerano al cambio stagione. Si attenuano: al confine
   lo stato viene tirato verso la media di lega di una quota pari a
   config.SEASON_REGRESSION, a rappresentare l'incertezza da mercato e cambi
   tecnici. Azzerare renderebbe il modello cieco fino a novembre.

PERCHE' UN CICLO E NON pandas.ewm()
`ewm()` non sa niente dei confini di stagione e non permette di intervenire
sullo stato. Con ~9000 righe (4500 partite x 2 squadre) un ciclo Python
impiega meno di un secondo, e in cambio si ottiene controllo esatto.

BURN-IN
La prima stagione serve a inizializzare le medie di lega e gli stati delle
squadre. Non va usata per l'addestramento: e' marcata con `is_burn_in`.

Uso:
    python -m src.features.form
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("form")

# Statistiche da mediare: nome breve -> (colonna casa, colonna trasferta).
# Il nome breve diventa il prefisso delle feature generate.
STATS: dict[str, tuple[str, str]] = {
    "goals":  ("FTHG", "FTAG"),
    "np_xg":  ("home_np_xg", "away_np_xg"),
    "ppda":   ("home_ppda", "away_ppda"),
    "deep":   ("home_deep_completions", "away_deep_completions"),
    "shots":  ("HS", "AS"),
    "sot":    ("HST", "AST"),
    "xpts":   ("home_expected_points", "away_expected_points"),
    "points": ("home_points", "away_points"),
}

KEYS = ["league", "season", "home_team", "away_team"]


# ---------------------------------------------------------------------------
# Formato lungo
# ---------------------------------------------------------------------------

def to_long(df: pd.DataFrame) -> pd.DataFrame:
    """
    Da una riga per partita a due righe per partita, una per squadra.

    Per ogni statistica si producono due colonne:
      <stat>_for      quanto la squadra ha prodotto
      <stat>_against  quanto ha concesso

    Dal punto di vista della squadra in trasferta i due valori si invertono:
    e' il motivo per cui non basta un semplice melt.
    """
    frames = []

    for venue, (own_idx, opp_idx) in (("home", (0, 1)), ("away", (1, 0))):
        block = pd.DataFrame({
            "league": df["league"].values,
            "season": df["season"].values,
            "date": pd.to_datetime(df["date"]).values,
            "team": df["home_team" if venue == "home" else "away_team"].values,
            "opponent": df["away_team" if venue == "home" else "home_team"].values,
            "venue": venue,
            "home_team": df["home_team"].values,
            "away_team": df["away_team"].values,
        })
        for stat, cols in STATS.items():
            missing = [c for c in cols if c not in df.columns]
            if missing:
                log.warning("statistica '%s' saltata: colonne mancanti %s", stat, missing)
                continue
            block[f"{stat}_for"] = pd.to_numeric(df[cols[own_idx]], errors="coerce").values
            block[f"{stat}_against"] = pd.to_numeric(df[cols[opp_idx]], errors="coerce").values
        frames.append(block)

    long = pd.concat(frames, ignore_index=True)
    long = long.sort_values(["team", "date"]).reset_index(drop=True)
    log.info("formato lungo: %d righe, %d squadre", len(long), long["team"].nunique())
    return long


# ---------------------------------------------------------------------------
# Medie di lega, calcolate solo sul passato
# ---------------------------------------------------------------------------

def league_means(long: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """
    Media di lega per ogni (lega, stagione, statistica), calcolata sulle
    stagioni STRETTAMENTE PRECEDENTI.

    Usare la media di tutto lo storico sarebbe leakage: il valore di
    riferimento per il 2018 conterrebbe dati del 2024. L'expanding mean
    spostata di una posizione risolve il problema.

    Conseguenza: la prima stagione non ha una media disponibile, ed e'
    infatti marcata come burn-in.
    """
    per_season = (
        long.groupby(["league", "season"], observed=True)[value_cols]
        .mean()
        .sort_index()
    )
    prior = (
        per_season.groupby(level="league", observed=True)
        .apply(lambda g: g.expanding().mean().shift(1))
    )
    # L'apply puo' aggiungere un livello 'league' duplicato: lo si rimuove.
    if prior.index.nlevels > per_season.index.nlevels:
        prior = prior.droplevel(0)
    return prior


# ---------------------------------------------------------------------------
# Media esponenziale con attenuazione a fine stagione
# ---------------------------------------------------------------------------

def ewma_by_team(
    long: pd.DataFrame,
    value_cols: list[str],
    means: pd.DataFrame,
    halflife: float,
    regression: float,
) -> pd.DataFrame:
    """
    Media esponenziale per squadra, con attenuazione al cambio stagione.

    alpha deriva dall'half-life: dopo `halflife` partite il peso di
    un'osservazione si e' dimezzato.
    """
    alpha = 1.0 - 0.5 ** (1.0 / halflife)
    log.info("half-life %.0f partite -> alpha %.3f", halflife, alpha)

    out = {c: np.full(len(long), np.nan) for c in value_cols}
    played_season = np.zeros(len(long), dtype=int)
    played_total = np.zeros(len(long), dtype=int)

    for _, idx in long.groupby("team", observed=True).groups.items():
        idx = np.asarray(idx)
        state: dict[str, float] = {}
        prev_season = None
        n_season = n_total = 0

        for pos in idx:
            row = long.iloc[pos]
            season, league = row["season"], row["league"]

            if prev_season is not None and season != prev_season:
                n_season = 0
                # Attenuazione: lo stato viene tirato verso la media di lega.
                try:
                    ref = means.loc[(league, season)]
                except KeyError:
                    ref = None
                if ref is not None:
                    for c in list(state):
                        m = ref.get(c, np.nan)
                        if not np.isnan(m):
                            state[c] = m + (1 - regression) * (state[c] - m)

            # Output PRIMA dell'aggiornamento: qui sta la garanzia anti-leakage.
            for c in value_cols:
                out[c][pos] = state.get(c, np.nan)
            played_season[pos] = n_season
            played_total[pos] = n_total

            for c in value_cols:
                v = row[c]
                if pd.isna(v):
                    continue
                state[c] = v if c not in state else state[c] + alpha * (v - state[c])

            n_season += 1
            n_total += 1
            prev_season = season

    res = pd.DataFrame(out, index=long.index)
    res["matches_played_season"] = played_season
    res["matches_played_total"] = played_total
    return res


# ---------------------------------------------------------------------------
# Ritorno al formato largo
# ---------------------------------------------------------------------------

def to_wide(long: pd.DataFrame, form: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Riaggancia le medie mobili alla riga partita, lato casa e lato trasferta."""
    feat_cols = value_cols + ["matches_played_season", "matches_played_total"]
    merged = pd.concat([long[KEYS + ["venue", "date"]], form[feat_cols]], axis=1)

    parts = []
    for venue in ("home", "away"):
        sub = merged[merged["venue"] == venue].drop(columns="venue")
        # La data e' una proprieta' della partita, non della squadra: si tiene
        # una volta sola, dal lato casa, per non generare date_x / date_y.
        if venue == "away":
            sub = sub.drop(columns="date")
        sub = sub.rename(columns={c: f"{venue}_{c}_ewm" for c in value_cols})
        sub = sub.rename(columns={
            "matches_played_season": f"{venue}_matches_season",
            "matches_played_total": f"{venue}_matches_total",
        })
        parts.append(sub.set_index(KEYS))

    wide = parts[0].join(parts[1], how="outer").reset_index()
    wide = wide.sort_values("date").reset_index(drop=True)

    # Differenziali: spesso piu' informativi dei livelli assoluti, perche'
    # una partita e' un confronto, non due misure indipendenti.
    for c in value_cols:
        wide[f"diff_{c}_ewm"] = wide[f"home_{c}_ewm"] - wide[f"away_{c}_ewm"]

    return wide


# ---------------------------------------------------------------------------

def build(df: pd.DataFrame | None = None, save: bool = True) -> pd.DataFrame:
    """
    `save=False` serve a src/predict.py: li' il frame contiene anche le partite
    non ancora giocate, e il risultato non deve sovrascrivere il parquet delle
    feature storiche.
    """
    if df is None:
        path = config.INTERIM / "matches_master.parquet"
        df = pd.read_parquet(path)
        log.info("caricato %s: %d righe", path.name, len(df))

    long = to_long(df)
    value_cols = [c for c in long.columns if c.endswith(("_for", "_against"))]
    log.info("statistiche mediate: %s", sorted({c.rsplit("_", 1)[0] for c in value_cols}))

    means = league_means(long, value_cols)
    form = ewma_by_team(
        long, value_cols, means,
        halflife=config.FORM_HALFLIFE,
        regression=config.SEASON_REGRESSION,
    )
    wide = to_wide(long, form, value_cols)

    # Burn-in: prima stagione di ogni lega, priva di storico e di media
    # di riferimento. Da escludere dall'addestramento.
    first = wide.groupby("league", observed=True)["season"].transform("min")
    wide["is_burn_in"] = wide["season"] == first
    log.info("righe di burn-in escluse dal training: %d", int(wide["is_burn_in"].sum()))

    if save:
        out = config.PROCESSED / "features_form.parquet"
        wide.to_parquet(out, index=False)
        log.info("scritto %s: %d righe, %d colonne", out.name, len(wide), wide.shape[1])
    return wide


if __name__ == "__main__":
    build()