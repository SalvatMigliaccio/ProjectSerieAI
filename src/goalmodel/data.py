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


def load_raw(nome: str, rimedio: str | None = None) -> pd.DataFrame:
    """
    Un parquet grezzo da `data/raw`, come lo ha scritto l'ingestion.

    `rimedio` serve a chi sa NOMINARE lo stage: il messaggio predefinito dice
    `--stage <stage>`, che e' onesto ma inutile a chi ha appena clonato.
    """
    df = _leggi(config.RAW / f"{nome}.parquet", nome,
                rimedio or f"goalmodel ingest --stage <stage>  (per '{nome}')")
    log.info("caricato %-24s %6d righe, %3d colonne", nome, len(df), df.shape[1])
    return df


def load_raw_opzionale(nome: str) -> pd.DataFrame | None:
    """
    Come `load_raw`, ma `None` quando il file non c'e'.

    PERCHE' UNA FUNZIONE A PARTE E NON UN FLAG. Un file assente non e' sempre
    un guasto: le coppe, le quote del turno e i blocchi di feature possono
    mancare, e il chiamante lo dice a modo suo — c'e' chi avvisa e prosegue
    senza due colonne, chi restituisce un frame vuoto. Quella decisione e' sua
    e resta sua: qui si sposta solo la lettura, cioe' l'unica cosa che dovra'
    cambiare quando i dati staranno in Postgres (ADR 0002).

    `None` e non un DataFrame vuoto di proposito: "non c'e'" e "c'e' e non
    contiene niente" sono stati diversi, e confonderli e' il modo tipico di
    far sparire un problema di ingestion in un grafico vuoto.
    """
    if not (config.RAW / f"{nome}.parquet").exists():
        return None
    return load_raw(nome)


def processed_esiste(nome: str) -> bool:
    """Per chi deve DECIDERE se un blocco c'e', non leggerlo."""
    return (config.PROCESSED / f"{nome}.parquet").exists()


def load_processed(nome: str, rimedio: str | None = None) -> pd.DataFrame:
    """Un parquet di `data/processed`: feature o previsioni gia' calcolate."""
    return _leggi(config.PROCESSED / f"{nome}.parquet", nome,
                  rimedio or f"il comando che produce '{nome}'")


def load_processed_opzionale(nome: str) -> pd.DataFrame | None:
    """`None` se quel file non e' stato ancora prodotto."""
    if not processed_esiste(nome):
        return None
    return load_processed(nome)


def nomi_processed(pattern: str) -> list[str]:
    """
    I nomi (senza estensione) dei file di `data/processed` che combaciano.

    Serve a chi SCOPRE cosa c'e' invece di sapere cosa cercare — per esempio
    un walk-forward per blocco, uno per file. La glob e' conoscenza del
    formato su disco, quindi sta qui e non nel chiamante: quando i dati
    staranno in Postgres questa diventa una query, e chi chiama non cambia.
    """
    return sorted(p.stem for p in config.PROCESSED.glob(f"{pattern}.parquet"))


def load_whoscored_schedules() -> pd.DataFrame | None:
    """
    I calendari WhoScored, uno per stagione, gia' concatenati.

    Sono l'unica cosa che traduce il `game_id` di WhoScored nella quadrupla.
    Stanno in una CARTELLA e non in un file solo perche' `ingest_missing` li
    scrive una stagione per volta ed e' riprendibile; chi legge pero' li vuole
    insieme, e ripetere la glob dal chiamante significa ripetere anche il
    percorso.

    `None` se la cartella non c'e' o e' vuota: e' lo stato normale di un clone
    pulito, non un guasto. Chi chiama decide cosa farne.
    """
    cartella = config.RAW / "whoscored"
    pezzi = [pd.read_parquet(p)[["season", "game_id", "home_team", "away_team"]]
             for p in sorted(cartella.glob("schedule_*.parquet"))]
    if not pezzi:
        return None
    return pd.concat(pezzi, ignore_index=True).drop_duplicates("game_id")


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
