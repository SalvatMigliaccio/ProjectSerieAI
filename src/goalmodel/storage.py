"""
Dove stanno i dati: file parquet, Postgres, o entrambi durante il passaggio.

PERCHE' ESISTE. ADR 0002 sposta i dati in Postgres. Il confine di LETTURA
(`data.py`) c'era gia'; quello di SCRITTURA no: quindici `to_parquet` sparsi
fra ingestion, normalizzazione, feature e valutazione, ognuno con il suo
percorso. Qui c'e' l'unico punto che sa dove va un dato, e i chiamanti dicono
solo COSA scrivono: `scrivi("processed", "features_form", df)`.

TRE MODALITA', scelte con `GOALMODEL_STORAGE` (ADR 0006):

  parquet   come prima. Il predefinito finche' la migrazione non e' verificata.
  doppio    scrive su file E su Postgres, legge dai file. Serve a far girare la
            pipeline vera contro il database senza dipenderne: se Postgres e'
            giu', la scrittura sui file e' gia' avvenuta e si prosegue con un
            errore nel log. `goalmodel db --verifica` dice se i due coincidono.
  postgres  legge e scrive solo Postgres. Lo stato finale: niente `data/`.

Il registro delle previsioni NON passa di qui e non passera' mai: resta su
file, versionato, scritto solo da processi locali (ADR 0002, punto 1).
"""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path

import pandas as pd

from . import config

log = logging.getLogger("storage")

VARIABILE = "GOALMODEL_STORAGE"
MODALITA = ("parquet", "doppio", "postgres")
PREDEFINITA = "parquet"

CARTELLE: dict[str, Path] = {
    "raw": config.RAW,
    "interim": config.INTERIM,
    "processed": config.PROCESSED,
}


class ScritturaVietata(PermissionError):
    """Un processo che non deve scrivere dati ha provato a farlo."""


class DatoAssente(FileNotFoundError):
    """Il dato non c'e', ne' su file ne' in Postgres. Stato normale in un clone."""


_vietato: str | None = None


def vieta_scritture(motivo: str) -> None:
    """
    Da qui in poi `scrivi` solleva, in questo processo.

    La chiamano le due guardie che gia' sostituivano `to_parquet`:
    `experiments.proteggi_produzione()` e `_guard_writes()` dell'API. Senza,
    con i dati in Postgres quelle guardie proteggerebbero un file che nessuno
    legge piu', e un esperimento potrebbe sovrascrivere le feature di
    produzione con una chiamata che non tocca il disco.
    """
    global _vietato
    _vietato = motivo


def modalita() -> str:
    valore = os.environ.get(VARIABILE, PREDEFINITA).strip().lower()
    if valore not in MODALITA:
        raise ValueError(f"{VARIABILE}={valore!r} non valida. Ammesse: {MODALITA}")
    return valore


def percorso(livello: str, nome: str) -> Path:
    """Il file di quel dato. Serve anche solo a NOMINARLO nei messaggi."""
    if livello not in CARTELLE:
        raise ValueError(f"livello sconosciuto: {livello}. Noti: {tuple(CARTELLE)}")
    return CARTELLE[livello] / f"{nome}.parquet"


def da_percorso(path: Path) -> tuple[str, str] | None:
    """
    `data/raw/fbref_schedule.parquet` -> `("raw", "fbref_schedule")`.

    Per l'API, che ragiona per percorsi (`store.Sources`): cosi' un percorso
    dentro `data/` diventa il nome del dato, e il resto della API non cambia.
    `None` se il percorso non e' un dato di `data/` — i fixture dei test.
    """
    path = Path(path).resolve()
    for livello, cartella in CARTELLE.items():
        try:
            relativo = path.relative_to(cartella.resolve())
        except ValueError:
            continue
        if path.suffix == ".parquet":
            return livello, relativo.with_suffix("").as_posix()
    return None


# ---------------------------------------------------------------------------
# Scrittura
# ---------------------------------------------------------------------------

def scrivi(livello: str, nome: str, df: pd.DataFrame) -> None:
    """Scrive il dato dove la modalita' dice. Sostituisce sempre per intero."""
    if _vietato is not None:
        raise ScritturaVietata(f"scrittura di {livello}/{nome} rifiutata: {_vietato}")
    m = modalita()
    if m in ("parquet", "doppio"):
        dst = percorso(livello, nome)
        dst.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(dst, index=False)
    if m == "parquet":
        return

    from . import pg

    if m == "postgres":
        pg.scrivi(livello, nome, df)
        return
    # doppio: il file e' gia' scritto ed e' la fonte. Postgres giu', o un tipo
    # che non sa ancora portare, non fermano la pipeline: lo dice e basta.
    try:
        pg.scrivi(livello, nome, df)
    except (*pg.errori_db(), pg.TipoNonSupportato) as exc:
        log.error("doppio: %s/%s scritto su file ma NON in Postgres (%s: %s). "
                  "'goalmodel db --verifica' lo mostrera' come divergente.",
                  livello, nome, type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Lettura
# ---------------------------------------------------------------------------

def _da_postgres() -> bool:
    return modalita() == "postgres"


def leggi(livello: str, nome: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Il dato, o `DatoAssente`. Nessun controllo di schema: lo fa `data.py`."""
    if _da_postgres():
        from . import pg

        try:
            return pg.leggi(livello, nome, columns)
        except pg.DatoAssente as exc:
            raise DatoAssente(str(exc)) from exc
    p = percorso(livello, nome)
    if not p.exists():
        raise DatoAssente(f"{p} assente")
    return pd.read_parquet(p, columns=columns)


def leggi_opzionale(livello: str, nome: str,
                    columns: list[str] | None = None) -> pd.DataFrame | None:
    """`None` se il dato non c'e'. Per i produttori che accodano a cio' che hanno."""
    try:
        return leggi(livello, nome, columns)
    except DatoAssente:
        return None


def esiste(livello: str, nome: str) -> bool:
    if _da_postgres():
        from . import pg

        return pg.versione(livello, nome) is not None
    return percorso(livello, nome).exists()


def nomi(livello: str, pattern: str) -> list[str]:
    """I nomi che combaciano con un glob: `walk_forward_*`, `whoscored/schedule_*`."""
    if _da_postgres():
        from . import pg

        return pg.nomi(livello, pattern)
    cartella = CARTELLE[livello]
    return sorted(
        n for n in (p.relative_to(cartella).with_suffix("").as_posix()
                    for p in cartella.glob(f"{pattern}.parquet"))
        if fnmatch.fnmatchcase(n, pattern)
    )


def impronta(livello: str, nome: str) -> tuple:
    """
    Cambia se e solo se il dato cambia. Per le cache dell'API.

    Su file e' (mtime, dimensione), come faceva `store._stat`; in Postgres e'
    la versione del catalogo, che ogni scrittura incrementa.
    """
    if _da_postgres():
        from . import pg

        return ("pg", livello, nome, pg.versione(livello, nome))
    p = percorso(livello, nome)
    try:
        st = p.stat()
        return (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(p), None, None)
