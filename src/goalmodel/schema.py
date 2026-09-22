"""
Il contratto dei file su disco, verificato quando si leggono.

PERCHE' ESISTE. Fino a qui il contratto fra un livello e l'altro era
implicito: i nomi delle colonne stavano sparsi nel codice e in `CLAUDE.md`, e
niente li verificava. Un contratto implicito non e' gratis, e' solo invisibile
finche' non si rompe — e quando si rompe non fa rumore.

I TRE MODI IN CUI SI ROMPE IN SILENZIO, tutti gia' visti in questo progetto:

  1. **Un tipo che cambia.** `season` come intero invece che come stringa fa
     fallire il merge sulla quadrupla nel 100% dei casi senza sollevare
     niente: si ottengono NaN dappertutto e sembra che manchino i dati. E'
     successo con le date in `players.py`, ed e' scritto nel suo docstring.
  2. **Una colonna che sparisce.** Il merge e' `how="left"`, quindi le righe
     restano e il valore diventa NaN. LightGBM i NaN li accetta senza fiatare
     e il modello degenera in silenzio — come quando `test_form` scriveva 90
     righe sintetiche sopra `features_form.parquet`.
  3. **Una chiave che si duplica.** La regola non negoziabile n.5 dice che la
     quadrupla e' univoca. Se smettesse di esserlo, ogni merge senza
     `validate=` moltiplicherebbe righe, e le metriche resterebbero
     plausibili.

DOVE SI VERIFICA, E PERCHE' PROPRIO LI'. Al confine di lettura, in `data.py`.
E' l'unico punto che tutti attraversano, ed e' il piu' vicino possibile al
disco: piu' avanti si scopre il problema, piu' e' difficile capire da dove
venga. Verificare costa qualche millisecondo su 4610 righe.

COSA NON E'. Non e' un elenco di tutte le 222 colonne di `matches_master`.
Quello sarebbe una seconda copia del dataset, che diverge dal primo alla
prima ingestion e va tenuta allineata a mano. Qui stanno **solo le colonne la
cui assenza o il cui tipo sbagliato produrrebbe un errore silenzioso**: le
chiavi, le date, i risultati. Le colonne di feature hanno gia' il loro
registro in `features/sets.py`, e i blocchi il loro in `features/registry.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from . import config

log = logging.getLogger("schema")

KEYS = tuple(config.JOIN_KEYS)


class SchemaNonRispettato(ValueError):
    """Un file su disco non e' quello che il codice si aspetta."""


@dataclass(frozen=True)
class Schema:
    """
    Cosa deve valere per un file, e come dirlo quando non vale.

    `chiave_unica=False` non significa "non ci interessa": significa che il
    file ha duplicati **attesi**, e allora `duplicati_ammessi_se_nullo` dice
    quale colonna li distingue. Ammettere duplicati senza saper dire quali e'
    lo stesso che non controllare.
    """

    nome: str
    obbligatorie: tuple[str, ...] = ()
    # Colonne che devono restare testuali. Un intero qui non solleva: fa
    # fallire il merge sulla quadrupla su tutte le righe.
    testuali: tuple[str, ...] = ()
    temporali: tuple[str, ...] = ()
    chiave: tuple[str, ...] = ()
    chiave_unica: bool = True
    duplicati_ammessi_se_nullo: str | None = None
    note: str = ""

    def verifica(self, df: pd.DataFrame, rimedio: str) -> None:
        """Solleva al primo problema, nominando il rimedio."""
        presenti = set(df.columns)

        mancanti = [c for c in self.obbligatorie if c not in presenti]
        if mancanti:
            raise SchemaNonRispettato(
                f"{self.nome}: mancano le colonne {mancanti}. "
                f"Il file c'e' ma non e' quello che il codice si aspetta. "
                f"Rimedio: {rimedio}"
            )

        for c in self.testuali:
            if c in presenti and not _e_testo(df[c]):
                raise SchemaNonRispettato(
                    f"{self.nome}: la colonna '{c}' e' {df[c].dtype}, non testo. "
                    f"Un merge sulla quadrupla fra tipi diversi non solleva: "
                    f"restituisce zero corrispondenze e sembra che manchino i "
                    f"dati. Rimedio: {rimedio}"
                )

        for c in self.temporali:
            if c in presenti and not pd.api.types.is_datetime64_any_dtype(df[c]):
                raise SchemaNonRispettato(
                    f"{self.nome}: la colonna '{c}' e' {df[c].dtype}, non una "
                    f"data. Confrontare stringhe con Timestamp non solleva, "
                    f"da' solo il risultato sbagliato. Rimedio: {rimedio}"
                )

        self._verifica_chiave(df, presenti, rimedio)

    def _verifica_chiave(self, df: pd.DataFrame, presenti: set[str],
                         rimedio: str) -> None:
        chiave = [c for c in self.chiave if c in presenti]
        if len(chiave) != len(self.chiave):
            return

        nulle = {c: int(df[c].isna().sum()) for c in chiave if df[c].isna().any()}
        if nulle:
            raise SchemaNonRispettato(
                f"{self.nome}: valori nulli nella chiave {nulle}. Le righe con "
                f"chiave nulla spariscono da ogni merge senza un messaggio. "
                f"Rimedio: {rimedio}"
            )

        doppie = df.duplicated(subset=chiave, keep=False)
        if not doppie.any():
            return

        if self.chiave_unica:
            esempi = df.loc[doppie, chiave].head(4).to_dict("records")
            raise SchemaNonRispettato(
                f"{self.nome}: la chiave {chiave} non e' univoca "
                f"({int(doppie.sum())} righe). E' la regola non negoziabile "
                f"n.5, e ogni merge senza `validate=` moltiplicherebbe righe "
                f"restando plausibile. Esempi: {esempi}. Rimedio: {rimedio}"
            )

        # Duplicati ammessi, ma solo quelli che sappiamo riconoscere.
        marcatore = self.duplicati_ammessi_se_nullo
        if marcatore is None or marcatore not in presenti:
            return
        gruppi = df.loc[doppie].groupby(list(chiave), dropna=False)
        inattesi = [
            nome for nome, g in gruppi
            # In un gruppo di duplicati una sola riga puo' essere quella
            # "vera": tutte le altre devono portare il marcatore nullo.
            if int(g[marcatore].notna().sum()) > 1
        ]
        if inattesi:
            raise SchemaNonRispettato(
                f"{self.nome}: {len(inattesi)} chiavi duplicate che NON sono "
                f"spiegate da '{marcatore}' nullo: {inattesi[:3]}. I duplicati "
                f"attesi sono le partite fuori dal calendario di campionato; "
                f"questi non lo sono. Rimedio: {rimedio}"
            )


def _e_testo(s: pd.Series) -> bool:
    """`str`, `string` e `object` vanno bene; un numero no."""
    return (pd.api.types.is_string_dtype(s)
            or pd.api.types.is_object_dtype(s)) and not pd.api.types.is_numeric_dtype(s)


# ---------------------------------------------------------------------------
# Gli schemi dichiarati
# ---------------------------------------------------------------------------

MATCHES_MASTER = Schema(
    nome="matches_master",
    obbligatorie=(*KEYS, "date", "FTHG", "FTAG", "FTR"),
    testuali=KEYS,
    temporali=("date",),
    chiave=KEYS,
    chiave_unica=True,
    note="Il dataset del modello. La quadrupla e' univoca: verificato su 4610 righe.",
)

# LA QUADRUPLA QUI NON E' UNIVOCA, ED E' UN FATTO DEL CALCIO, NON UN BUG.
# Spezia-Hellas Verona 2022/23 compare due volte: la partita di campionato del
# 5 marzo 2023 (0-0, giornata 25) e lo SPAREGGIO SALVEZZA dell'11 giugno
# (1-3). Sono due partite diverse fra le stesse squadre nella stessa stagione,
# ed e' esattamente il caso che la regola n.5 non prevede — la regola dice "in
# un campionato all'italiana", e uno spareggio non lo e'.
#
# Come si riconosce: lo spareggio ha `week` nullo, perche' non appartiene a
# nessuna giornata. Tutti i consumatori lo escludono con `dropna(["week"])`;
# quelli che invece facevano `drop_duplicates` tenevano la prima riga
# dell'ordine del parquet, che e' un dettaglio di come il file e' stato
# scritto, non una decisione.
FBREF_SCHEDULE = Schema(
    nome="fbref_schedule",
    obbligatorie=(*KEYS, "date", "week"),
    testuali=KEYS,
    temporali=("date",),
    chiave=KEYS,
    chiave_unica=False,
    duplicati_ammessi_se_nullo="week",
    note="Include le partite non ancora giocate e gli spareggi (week nullo).",
)

MATCHES = Schema(
    nome="matches",
    obbligatorie=(*KEYS, "date"),
    testuali=KEYS,
    chiave=KEYS,
    chiave_unica=True,
    note="Il grezzo di football-data, prima dell'unione con Understat.",
)

# Solo i file che hanno un contratto da rispettare. Gli altri grezzi si
# leggono senza verifica: dichiarare uno schema per ognuno costerebbe piu' di
# quanto renda, e un file che nessuno usa come chiave non puo' rompersi in
# silenzio.
PER_NOME: dict[str, Schema] = {
    s.nome: s for s in (MATCHES_MASTER, FBREF_SCHEDULE, MATCHES)
}
