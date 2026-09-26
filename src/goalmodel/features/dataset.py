"""
Il dataset del modello: risultati, giornata e tutti i blocchi di feature.

PERCHE' STA QUI E NON IN `evaluation/` (dove era). Assemblare il dataset non
e' valutare: si leggono i file, si uniscono i blocchi dal registro, si aggancia
la giornata. Nessuna metrica, nessun modello.

Finche' `load_dataset` viveva in `evaluate.py`, ogni modulo che voleva il
dataset doveva importare `evaluation` — e `features/sets.py`, `models/gbm.py`
e `models/dixon_coles.py` lo facevano **dentro le funzioni**, per non creare
un ciclo di import. Un import all'indietro nascosto in una funzione resta un
import all'indietro: e' solo piu' difficile da vedere. Spostando la funzione
al livello a cui appartiene il ciclo sparisce invece di essere aggirato.

`evaluate.py` continua a riesportare i due nomi: chi li importava da li'
non deve cambiare niente, ed e' il motivo per cui questo spostamento non
tocca nessun chiamante.
"""

from __future__ import annotations

import logging

import pandas as pd

from .. import config, data
from ..normalize import apply_name_map, load_name_map, load_raw, normalize_season
from . import registry

log = logging.getLogger("dataset")

KEYS = config.JOIN_KEYS


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
    # LO SPAREGGIO VA TOLTO PRIMA, NON LASCIATO A `drop_duplicates`. Il
    # calendario ha una quadrupla duplicata — Spezia-Hellas Verona 2022/23, la
    # partita di campionato e lo spareggio salvezza — e `drop_duplicates`
    # teneva la prima riga nell'ordine del parquet. Funzionava perche' quella
    # prima riga e' la partita di campionato, il che e' un dettaglio di come
    # il file e' stato scritto, non una decisione: se l'ordine cambiasse si
    # terrebbe lo spareggio, che ha `week` nullo, e `astype(int)` piu' sotto
    # fallirebbe su una giornata mancante. Lo spareggio non appartiene a
    # nessuna giornata: e' quello il criterio. Vedi `schema.FBREF_SCHEDULE`.
    sched = sched.dropna(subset=["week"])
    sched = sched[KEYS + ["week"]].drop_duplicates(subset=KEYS)

    out = df.merge(sched, on=KEYS, how="left", validate="one_to_one")
    missing = out["week"].isna().sum()
    if missing:
        raise ValueError(
            f"{missing} partite senza giornata: manca una voce in "
            f"{config.TEAM_NAME_MAP.name}? Lancia goalmodel normalize --report"
        )
    out = out.rename(columns={"week": "matchday"})
    out["matchday"] = out["matchday"].astype(int)
    return out


def load_dataset() -> pd.DataFrame:
    """
    Risultati, giornata e TUTTI i blocchi di feature disponibili, in un frame.

    L'elenco dei blocchi non sta piu' qui: sta in `features/registry.py`, ed e'
    lo STESSO che `rounds.ricostruisci` usa per ricostruirli. Erano due elenchi
    diversi, e la differenza — `players` presente qui e assente di la' — faceva
    invecchiare quel blocco a ogni giornata senza che niente lo segnalasse
    (audit B2).

    I parametri `with_form` e `with_context` sono spariti: nessuno li passava,
    e il registro li rende inutili, perche' un blocco entra se e solo se il suo
    parquet esiste — che e' esattamente cio' per cui `with_context` esisteva.
    """
    matches = data.load_master()
    base = matches[KEYS + ["date", "FTHG", "FTAG", "FTR"]].copy()
    df = registry.unisci(base, KEYS)

    df = add_matchday(df)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "home_team"]).reset_index(drop=True)
    log.info("dataset: %d righe, stagioni %s-%s", len(df), df["season"].min(), df["season"].max())
    return df
