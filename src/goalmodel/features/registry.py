"""
I blocchi di feature: quali esistono, come si costruiscono, come si caricano.

PERCHE' ESISTE — E' LA CORREZIONE DI B2, NON UN RIORDINO
Lo stesso elenco viveva in due posti che non si parlavano:

    evaluate.load_dataset()   univa  market, form, context, players
    rounds.ricostruisci()     ricostruiva  form, market, context

Manca `players` nella seconda. Conseguenza, a ogni giornata: `matches_master`
cresce, tre parquet di feature si riallineano, e `features_players.parquet`
resta congelato all'ultima costruzione manuale — mentre `load_dataset`
continua a unirlo e a darlo in pasto ai modelli. Il merge e' `how="left"`,
quindi le righe nuove ricevono NaN, e LightGBM i NaN li accetta senza fiatare.

Nessun errore, nessun avviso, un blocco che invecchia. Tenere l'elenco in un
posto solo non e' eleganza: e' l'unico modo perche' quella divergenza non
possa ripresentarsi.

COSA SIGNIFICA `obbligatorio`
`form` e `market` sono le fondamenta: senza, non c'e' nessun modello da
addestrare, e la loro assenza dev'essere fatale. `context` e `players`
dipendono da ingestion facoltative — le coppe, e le ~24 ore di scraping fra
WhoScored e FBref — quindi la loro assenza e' uno stato legittimo, da
segnalare e basta.

Attenzione a non confondere due cose: COSTRUIRE il blocco giocatori e'
veloce, e' pura aggregazione dei parquet grezzi. E' SCARICARE quei parquet a
costare ore. Per questo `build` sta nel ciclo settimanale e l'ingestion no.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .. import config
from . import context, form, market, players

log = logging.getLogger("registry")


@dataclass(frozen=True)
class Blocco:
    nome: str
    build: Callable[..., pd.DataFrame]
    comando: str
    obbligatorio: bool
    descrizione: str

    @property
    def parquet(self) -> Path:
        return config.PROCESSED / f"{self.nome}.parquet"

    @property
    def disponibile(self) -> bool:
        return self.parquet.exists()


# L'ordine e' quello di costruzione: `market` e `form` leggono solo
# matches_master, `context` e `players` anche i grezzi delle loro ingestion.
BLOCCHI: tuple[Blocco, ...] = (
    Blocco("features_form", form.build, "goalmodel features-form",
           obbligatorio=True, descrizione="medie mobili esponenziali"),
    Blocco("features_market", market.build, "goalmodel features-market",
           obbligatorio=True, descrizione="de-vigging delle quote"),
    Blocco("features_context", context.build, "goalmodel features-context",
           obbligatorio=False, descrizione="riposo, congestione, coppe, derby"),
    Blocco("features_players", players.build, "goalmodel features-players",
           obbligatorio=False, descrizione="minuti e gol+assist indisponibili"),
)


def per_nome(nome: str) -> Blocco:
    for b in BLOCCHI:
        if b.nome == nome:
            return b
    raise KeyError(f"blocco sconosciuto: {nome}. Noti: {[b.nome for b in BLOCCHI]}")


def mancanti() -> list[Blocco]:
    """I blocchi il cui parquet non c'e'. Vuoto = dataset completo."""
    return [b for b in BLOCCHI if not b.disponibile]


def unisci(base: pd.DataFrame, keys: list[str] | None = None) -> pd.DataFrame:
    """
    Aggiunge a `base` le colonne di ogni blocco disponibile.

    Un blocco obbligatorio assente solleva; uno facoltativo viene saltato con
    un avviso che dice quale comando lo costruirebbe. La `date` di ciascun
    blocco si scarta: e' una proprieta' della partita, la porta gia' `base`, e
    tenerla genererebbe date_x/date_y.
    """
    keys = keys or config.JOIN_KEYS
    df = base
    for b in BLOCCHI:
        if not b.disponibile:
            if b.obbligatorio:
                raise FileNotFoundError(
                    f"{b.parquet.name} assente ed e' obbligatorio "
                    f"({b.descrizione}). Lancia: {b.comando}"
                )
            log.warning("%s assente: blocco '%s' non disponibile. Lancia '%s'.",
                        b.parquet.name, b.descrizione, b.comando)
            continue
        extra = pd.read_parquet(b.parquet)
        extra = extra.drop(columns=[c for c in extra.columns if c == "date"])
        df = df.merge(extra, on=keys, how="left", validate="one_to_one")
    return df
