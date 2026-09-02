"""
Normalizzazione dei nomi squadra e join delle fonti in un'unica tabella partite.

Questo modulo risolve il problema numero uno del progetto: fonti diverse
chiamano la stessa squadra in modi diversi ("Inter" / "Internazionale" /
"Inter Milan"), e un join ingenuo perde righe silenziosamente senza sollevare
alcun errore.

Strategia:

1. La chiave di join NON e' la data. In un campionato all'italiana la coppia
   (lega, stagione, squadra_casa, squadra_trasferta) e' univoca, e questo
   evita del tutto i problemi di fuso orario e di partite rinviate che
   affliggono i join per data. La data resta come controllo di coerenza.

2. I nomi si normalizzano con un dizionario esplicito in
   manual/team_name_map.json, che mappa i nomi delle fonti secondarie sui
   nomi di football-data.co.uk (tabella base).

3. Il dizionario non si scrive a mano da zero: `--report` confronta gli
   insiemi di nomi per lega e stagione, elenca i disallineamenti e genera
   manual/team_name_map_suggested.json con proposte basate su fuzzy matching.
   Tu lo rivedi, correggi gli errori e lo rinomini in team_name_map.json.

Uso:
    python -m src.normalize --report     # diagnosi, genera la mappa proposta
    python -m src.normalize --build      # costruisce interim/matches_master.parquet
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
from pathlib import Path

import pandas as pd

from . import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("normalize")

JOIN_KEYS = ["league", "season", "home_team", "away_team"]

# Tabella base e fonti da agganciare. La base definisce lo spazio dei nomi
# canonico e il numero di righe atteso.
BASE_SOURCE = "matches"
SECONDARY_SOURCES = ["understat_team_match"]


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_raw(name: str) -> pd.DataFrame:
    """Carica un parquet da data/raw, con messaggio chiaro se manca."""
    path = config.RAW / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} non trovato. Lancia prima: python ingest.py --stage <stage>"
        )
    df = pd.read_parquet(path)
    log.info("caricato %-24s %6d righe, %3d colonne", name, len(df), df.shape[1])
    return df


def load_name_map() -> dict[str, str]:
    """
    Carica il dizionario di mapping. Se non esiste, ritorna un dizionario
    vuoto: il codice funziona comunque, semplicemente non corregge nulla.
    """
    if not config.TEAM_NAME_MAP.exists():
        log.warning(
            "%s non trovato: nessuna correzione dei nomi applicata. "
            "Lancia --report per generarne uno.",
            config.TEAM_NAME_MAP.name,
        )
        return {}
    with config.TEAM_NAME_MAP.open(encoding="utf-8") as fh:
        mapping = json.load(fh)
    log.info("mapping nomi: %d voci", len(mapping))
    return mapping


# ---------------------------------------------------------------------------
# Normalizzazione
# ---------------------------------------------------------------------------

def clean_name(s: pd.Series) -> pd.Series:
    """Pulizia minimale, applicata sempre e prima del mapping esplicito."""
    return (
        s.astype("string")
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )


def apply_name_map(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Applica pulizia e mapping alle colonne home_team e away_team."""
    out = df.copy()
    for col in ("home_team", "away_team"):
        if col not in out.columns:
            raise KeyError(f"colonna '{col}' assente: colonne presenti {list(out.columns)}")
        out[col] = clean_name(out[col])
        if mapping:
            out[col] = out[col].map(lambda x: mapping.get(x, x))
    return out


def normalize_season(df: pd.DataFrame) -> pd.DataFrame:
    """
    Uniforma il formato stagione. soccerdata puo' restituire '2425' oppure
    '2024-2025' a seconda della fonte: si riduce tutto a 4 cifre.
    """
    out = df.copy()
    s = out["season"].astype("string").str.replace("-", "", regex=False)
    # '20242025' -> '2425'
    s = s.where(s.str.len() != 8, s.str[2:4] + s.str[6:8])
    out["season"] = s
    return out


# ---------------------------------------------------------------------------
# Diagnosi dei disallineamenti
# ---------------------------------------------------------------------------

def teams_by_season(df: pd.DataFrame) -> dict[tuple[str, str], set[str]]:
    """Insieme delle squadre presenti, per (lega, stagione)."""
    out: dict[tuple[str, str], set[str]] = {}
    for (lg, sn), grp in df.groupby(["league", "season"], observed=True):
        out[(str(lg), str(sn))] = set(grp["home_team"]) | set(grp["away_team"])
    return out


def diff_report(base: pd.DataFrame, other: pd.DataFrame, other_name: str) -> dict[str, str]:
    """
    Confronta gli insiemi di nomi tra tabella base e fonte secondaria,
    per ogni (lega, stagione). Ritorna le proposte di mapping.

    Il fuzzy matching e' solo un suggerimento: va SEMPRE rivisto a mano.
    difflib puo' accoppiare squadre diverse con nomi simili (per esempio
    due club della stessa citta').
    """
    base_teams = teams_by_season(base)
    other_teams = teams_by_season(other)

    suggestions: dict[str, str] = {}
    total_unmatched = 0

    common_keys = sorted(set(base_teams) & set(other_teams))
    only_base = sorted(set(base_teams) - set(other_teams))
    only_other = sorted(set(other_teams) - set(base_teams))

    if only_base:
        log.warning("(lega, stagione) presenti solo nella base: %s", only_base[:10])
    if only_other:
        log.warning("(lega, stagione) presenti solo in %s: %s", other_name, only_other[:10])

    for key in common_keys:
        unmatched = other_teams[key] - base_teams[key]
        if not unmatched:
            continue
        candidates = sorted(base_teams[key] - other_teams[key])
        log.warning("%s %s: %d nomi non allineati", other_name, key, len(unmatched))
        for name in sorted(unmatched):
            total_unmatched += 1
            close = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
            proposal = close[0] if close else ""
            marker = "->" if proposal else "??"
            log.warning("    %-28s %s %s", name, marker, proposal or "NESSUN CANDIDATO")
            if name not in suggestions:
                suggestions[name] = proposal

    if total_unmatched == 0:
        log.info("%s: tutti i nomi gia' allineati", other_name)
    else:
        log.warning("%s: %d nomi da mappare", other_name, total_unmatched)

    return suggestions


def cmd_report() -> None:
    """Diagnosi completa + generazione della mappa proposta."""
    mapping = load_name_map()
    base = normalize_season(apply_name_map(load_raw(BASE_SOURCE), mapping))

    all_suggestions: dict[str, str] = {}
    for src in SECONDARY_SOURCES:
        try:
            other = normalize_season(apply_name_map(load_raw(src), mapping))
        except FileNotFoundError as exc:
            log.warning("salto '%s': %s", src, exc)
            continue
        all_suggestions.update(diff_report(base, other, src))

    if all_suggestions:
        with config.TEAM_NAME_MAP_SUGGESTED.open("w", encoding="utf-8") as fh:
            json.dump(all_suggestions, fh, indent=2, ensure_ascii=False, sort_keys=True)
        log.info("proposte scritte in %s", config.TEAM_NAME_MAP_SUGGESTED)
        log.info(
            "RIVEDILE A MANO, correggi le voci vuote o sbagliate, "
            "poi rinomina il file in %s",
            config.TEAM_NAME_MAP.name,
        )


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def check_base_integrity(base: pd.DataFrame) -> None:
    """La chiave di join deve essere univoca sulla tabella base."""
    dup = base.duplicated(subset=JOIN_KEYS, keep=False)
    if dup.any():
        sample = base.loc[dup, JOIN_KEYS].head(10)
        raise ValueError(
            f"chiave di join non univoca su {dup.sum()} righe. "
            f"Campione:\n{sample}\n"
            "Causa tipica: formato stagione non uniforme, o partita ripetuta "
            "(campo neutro, gara ripetuta)."
        )
    log.info("chiave di join univoca su %d righe", len(base))


def check_dates(merged: pd.DataFrame, suffix: str, tolerance_days: int = 3) -> None:
    """
    Le date delle due fonti devono coincidere entro qualche giorno.
    Discrepanze maggiori indicano un accoppiamento sbagliato, tipicamente
    una stagione disallineata.
    """
    col_other = f"date{suffix}"
    if "date" not in merged.columns or col_other not in merged.columns:
        log.info("controllo date saltato: colonne non disponibili")
        return

    d1 = pd.to_datetime(merged["date"], errors="coerce")
    d2 = pd.to_datetime(merged[col_other], errors="coerce")
    delta = (d1 - d2).abs().dt.days
    bad = delta > tolerance_days

    if bad.any():
        log.warning(
            "%d righe con date discordanti oltre %d giorni: accoppiamento sospetto",
            int(bad.sum()), tolerance_days,
        )
        log.warning("\n%s", merged.loc[bad, JOIN_KEYS + ["date", col_other]].head(10))
    else:
        log.info("date coerenti su tutte le righe accoppiate")


def cmd_build() -> None:
    """Costruisce interim/matches_master.parquet."""
    mapping = load_name_map()

    base = normalize_season(apply_name_map(load_raw(BASE_SOURCE), mapping))
    base = base[base["league"].isin(config.LEAGUES)]
    check_base_integrity(base)

    n_expected = len(base)
    merged = base

    for src in SECONDARY_SOURCES:
        try:
            other = normalize_season(apply_name_map(load_raw(src), mapping))
        except FileNotFoundError as exc:
            log.warning("salto '%s': %s", src, exc)
            continue

        other = other.drop_duplicates(subset=JOIN_KEYS)
        suffix = f"_{src.split('_')[0]}"

        before = len(merged)
        merged = merged.merge(other, on=JOIN_KEYS, how="left", suffixes=("", suffix))

        if len(merged) != before:
            raise ValueError(
                f"il join con '{src}' ha cambiato il numero di righe "
                f"({before} -> {len(merged)}): chiave duplicata nella fonte secondaria."
            )

        # Copertura: quante righe hanno trovato corrispondenza.
        probe = next(
            (c for c in other.columns if c not in JOIN_KEYS and c != "date"), None
        )
        if probe:
            probe_col = probe if probe in merged.columns else probe + suffix
            matched = merged[probe_col].notna().sum()
            pct = 100 * matched / len(merged) if len(merged) else 0
            level = log.info if pct > 90 else log.warning
            level("%s: %d/%d righe accoppiate (%.1f%%)", src, matched, len(merged), pct)

        check_dates(merged, suffix)

    if len(merged) != n_expected:
        raise ValueError(f"righe attese {n_expected}, ottenute {len(merged)}")

    out = config.INTERIM / "matches_master.parquet"
    merged.to_parquet(out, index=False)
    log.info("scritto %s: %d righe, %d colonne", out, len(merged), merged.shape[1])

    # Riepilogo per lega e stagione: utile per accorgersi di buchi.
    summary = merged.groupby(["league", "season"], observed=True).size()
    log.info("partite per lega/stagione:\n%s", summary.to_string())


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--report", action="store_true", help="diagnosi nomi squadra")
    g.add_argument("--build", action="store_true", help="costruisci matches_master")
    args = parser.parse_args()

    if args.report:
        cmd_report()
    else:
        cmd_build()


if __name__ == "__main__":
    main()
