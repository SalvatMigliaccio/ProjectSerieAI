"""
`goalmodel db`: portare i dati in Postgres e dimostrare che sono gli stessi.

    goalmodel db --stato        cosa c'e' nel catalogo
    goalmodel db --carica       ogni parquet di data/ -> Postgres
    goalmodel db --verifica     ogni parquet confrontato con la sua tabella

PERCHE' LA VERIFICA E' UN COMANDO E NON UNA PROMESSA. ADR 0002 lo chiedeva in
chiaro: `test_production_unchanged` confronta bit a bit, e il passaggio dal
driver va MISURATO. Qui si misura su tutti i file veri, non su un campione:
stesse colonne nello stesso ordine, stessi dtype, stessi valori, e per i float
gli stessi 64 bit — NaN compresi, che `==` non saprebbe confrontare.

Il passaggio a `GOALMODEL_STORAGE=postgres` (ADR 0006) si fa solo dopo una
`--verifica` pulita, e in `doppio` va rilanciata dopo ogni giornata: e' cio' che
dice se le due copie stanno divergendo.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa

from . import pg, storage

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("archivio")


def file_presenti() -> list[tuple[str, str]]:
    """Ogni dato su file, come (livello, nome). Le sottocartelle comprese."""
    return [(livello, p.relative_to(cartella).with_suffix("").as_posix())
            for livello, cartella in storage.CARTELLE.items()
            for p in sorted(cartella.rglob("*.parquet"))]


def differenze(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """
    Cosa distingue due DataFrame. Vuoto = identici.

    Prima la forma (colonne, ordine, dtype), poi i valori. I float si
    confrontano sui bit: `assert_frame_equal` con `check_exact` tratta due NaN
    come uguali, ma non distingue NaN da NA in un `Float64` ne' -0.0 da 0.0,
    e `test_production_unchanged` si'.
    """
    if list(a.columns) != list(b.columns):
        return [f"colonne diverse: {set(a.columns) ^ set(b.columns) or 'ordine'}"]
    if len(a) != len(b):
        return [f"righe: {len(a)} contro {len(b)}"]
    diverse = [f"{c}: dtype {a[c].dtype} contro {b[c].dtype}"
               for c in a.columns if a[c].dtype != b[c].dtype]
    if diverse:
        return diverse
    for c in a.columns:
        if a[c].dtype == np.float64:
            if not np.array_equal(a[c].to_numpy().view(np.uint64),
                                  b[c].to_numpy().view(np.uint64)):
                diverse.append(f"{c}: float diversi nei bit")
        elif isinstance(a[c].dtype, pd.Float64Dtype):
            na_a, na_b = a[c].isna().to_numpy(), b[c].isna().to_numpy()
            va = a[c].to_numpy(dtype="float64", na_value=0.0).view(np.uint64)
            vb = b[c].to_numpy(dtype="float64", na_value=0.0).view(np.uint64)
            if not (np.array_equal(na_a, na_b) and np.array_equal(va, vb)):
                diverse.append(f"{c}: Float64 diversi (valori o NA)")
        else:
            try:
                pd.testing.assert_series_equal(a[c], b[c], check_exact=True)
            except AssertionError:
                diverse.append(f"{c}: valori diversi")
            except TypeError:
                # Colonne annidate (le `incidents` di WhoScored): pandas non le
                # sa confrontare, arrow si'.
                if not pa.array(a[c]).equals(pa.array(b[c])):
                    diverse.append(f"{c}: valori annidati diversi")
    return diverse


def carica(solo: list[str] | None = None) -> int:
    """Ogni parquet in Postgres. Restituisce quanti ne ha scritti."""
    fatti = 0
    for livello, nome in file_presenti():
        if solo and nome not in solo:
            continue
        t0 = time.time()
        df = pd.read_parquet(storage.percorso(livello, nome))
        pg.scrivi(livello, nome, df)
        log.info("  %-10s %-36s %7d righe  %.1fs", livello, nome, len(df), time.time() - t0)
        fatti += 1
    return fatti


def verifica() -> int:
    """Confronta ogni file con la sua tabella. Restituisce quanti divergono."""
    divergenti = 0
    su_file = set(file_presenti())
    for livello, nome in sorted(su_file):
        da_file = pd.read_parquet(storage.percorso(livello, nome))
        try:
            da_pg = pg.leggi(livello, nome)
        except pg.DatoAssente:
            log.error("  %-10s %-36s ASSENTE in Postgres", livello, nome)
            divergenti += 1
            continue
        diff = differenze(da_file, da_pg)
        if diff:
            divergenti += 1
            log.error("  %-10s %-36s DIVERSO: %s", livello, nome, "; ".join(diff[:4]))
        else:
            log.info("  %-10s %-36s identico  (%d x %d)", livello, nome, *da_file.shape)
    in_pg = {(r.livello, r.nome) for r in pg.stato().itertuples()}
    for livello, nome in sorted(in_pg - su_file):
        log.warning("  %-10s %-36s solo in Postgres (nessun file)", livello, nome)
    return divergenti


def main() -> int:
    ap = argparse.ArgumentParser(prog="goalmodel db", description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stato", action="store_true", help="il catalogo dei dati in Postgres")
    g.add_argument("--carica", action="store_true", help="ogni parquet di data/ in Postgres")
    g.add_argument("--verifica", action="store_true",
                   help="confronta bit a bit ogni parquet con la sua tabella")
    ap.add_argument("--solo", nargs="+", metavar="NOME",
                    help="con --carica: solo questi dati (es. matches_master)")
    args = ap.parse_args()

    if args.stato:
        tab = pg.stato()
        print(tab.to_string(index=False) if len(tab) else "catalogo vuoto: goalmodel db --carica")
        return 0
    if args.carica:
        n = carica(args.solo)
        log.info("caricati %d dati. Ora: goalmodel db --verifica", n)
        return 0
    n = verifica()
    if n:
        log.error("%d dati DIVERGONO fra file e Postgres: non passare a "
                  "GOALMODEL_STORAGE=postgres", n)
        return 1
    log.info("tutti i dati coincidono bit a bit fra file e Postgres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
