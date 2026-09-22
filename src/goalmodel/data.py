"""
L'accesso ai dati su disco, in un posto solo.

PERCHE' ESISTE. `pd.read_parquet(config.INTERIM / "matches_master.parquet")`
compariva in undici punti fra moduli, test e script, e ognuno ripeteva il
percorso, la gestione dell'assenza e la conversione delle date. Cambiare
formato — o anche solo migliorare il messaggio d'errore — voleva dire
modificarli tutti.

Peggio: ogni chiamante gestiva l'assenza del file a modo suo, quindi lo stesso
problema si presentava all'utente in undici modi diversi, da un
`FileNotFoundError` nudo a un messaggio che nominava un comando che non
esisteva piu'.

COSA STA QUI E COSA NO. Qui ci sono solo le primitive di lettura: dove
stanno i file, come si leggono, cosa dire quando mancano. Non c'e' nessuna
conoscenza di quali blocchi di feature esistano — quella sta in
`features/registry.py`, perche' e' una questione di feature e perche'
altrimenti questo modulo dovrebbe importare `features/`, che a sua volta
legge da qui.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import pandas as pd

from . import config, schema

log = logging.getLogger("data")

MASTER = config.INTERIM / "matches_master.parquet"


def _leggi(path, descrizione: str, rimedio: str,
           columns: list[str] | None = None) -> pd.DataFrame:
    """
    Un parquet, con un messaggio che dice COSA LANCIARE quando manca.

    Il rimedio fa parte dell'errore di proposito: un `FileNotFoundError` su un
    percorso dentro `data/` non dice niente a chi ha appena clonato il
    repository, e `data/` non e' versionata — quindi in un clone pulito questo
    e' lo stato NORMALE, non un guasto.
    """
    if not path.exists():
        raise FileNotFoundError(f"{descrizione} assente ({path}). Lancia: {rimedio}")
    df = pd.read_parquet(path, columns=columns)

    # IL CONTRATTO SI VERIFICA QUI, non dove il dato serve. E' l'unico punto
    # che tutti attraversano, ed e' il piu' vicino al disco: un tipo sbagliato
    # scoperto tre livelli piu' avanti si manifesta come NaN, non come errore.
    # `columns=` chiede un sottoinsieme, e allora si verifica quel
    # sottoinsieme: pretendere colonne che il chiamante non ha chiesto sarebbe
    # un falso allarme.
    contratto = schema.PER_NOME.get(descrizione)
    if contratto is not None:
        if columns is not None:
            richieste = set(columns)
            contratto = replace(
                contratto,
                obbligatorie=tuple(c for c in contratto.obbligatorie if c in richieste),
                testuali=tuple(c for c in contratto.testuali if c in richieste),
                temporali=tuple(c for c in contratto.temporali if c in richieste),
                chiave=contratto.chiave if richieste >= set(contratto.chiave) else (),
            )
        contratto.verifica(df, rimedio)
    return df


def load_raw(nome: str) -> pd.DataFrame:
    """Un parquet grezzo da `data/raw`, come lo ha scritto l'ingestion."""
    df = _leggi(config.RAW / f"{nome}.parquet", nome,
                f"goalmodel ingest --stage <stage>  (per '{nome}')")
    log.info("caricato %-24s %6d righe, %3d colonne", nome, len(df), df.shape[1])
    return df


def load_master(columns: list[str] | None = None,
                date: bool = True) -> pd.DataFrame:
    """
    `matches_master`: risultati, quote e statistiche uniti sulla quadrupla.

    `date=True` converte la colonna data in datetime. Quasi tutti i chiamanti
    lo facevano per conto loro subito dopo la lettura, e chi se lo dimenticava
    confrontava stringhe con Timestamp — un confronto che in pandas non
    solleva, restituisce solo il risultato sbagliato.
    """
    df = _leggi(MASTER, "matches_master", "goalmodel normalize --build", columns)
    if date and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def master_esiste() -> bool:
    """Per chi deve DECIDERE se il dataset c'e', non leggerlo."""
    return MASTER.exists()
