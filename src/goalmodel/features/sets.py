"""
Registro dei set di feature: ogni colonna appartiene a un set nominato, e ogni
set ha uno stato.

PERCHE' ESISTE. Fino a qui le feature vivevano in un pacchetto indistinto di
61 colonne, e cosa entrasse nel modello lo decidevano due costanti in
`models/gbm.py` (`BLOCCHI_SCARTATI`, `BLOCCHI_NON_MISURATI`). Quel meccanismo
ha gia' prodotto un effetto collaterale: quando il blocco giocatori e' stato
ammesso, anche l'M4 che `report.py` usa per la sezione di divergenza ha
cominciato a vedere quelle colonne, senza che nessuno lo decidesse per il
report. Un modello deve DICHIARARE i set che usa, non ereditare quello che
qualcun altro ha deciso di lasciar dentro.

LE REGOLE
  - ogni modello sperimentale dichiara i suoi set: `sets=["BASE", "GIOCATORI"]`;
  - BASE e' congelato: la sua lista di colonne e' scritta qui per esteso, e
    `tests/test_sets.py` fallisce se cambia di una colonna;
  - i set sperimentali si AGGIUNGONO a BASE, non lo sostituiscono;
  - un set scartato resta qui e nel dataset, marcato come tale, cosi' si puo'
    rimisurare su un perimetro piu' largo senza ricostruirlo.

COSA QUESTO MODULO NON TOCCA. `models/gbm.py` e i suoi `BLOCCHI_*` restano
come sono, perche' `report.py` — che e' produzione — dipende da loro. Il
registro e' uno strato nuovo per gli esperimenti. Convertire il report ai set
dichiarati sarebbe un cambiamento di produzione, e va deciso a parte.

IL MERCATO NON E' UNA COLONNA DI BASE. M5 lo usa come punto di partenza
(`init_score = log(mkt_lambda)`), M1 lo usa come previsione. Nessuno dei due
lo passa come feature, quindi non compare nelle liste qui sotto: sta in
`features/market.py` e ne esce solo sotto forma di ancoraggio.

Uso:
    goalmodel features-sets            # stato dei set sul dataset reale
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("sets")

CONGELATO = "congelato"
PROVVISORIO = "provvisorio"
SCARTATO = "scartato"
DA_MISURARE = "da misurare"
STATI = (CONGELATO, PROVVISORIO, SCARTATO, DA_MISURARE)


@dataclass(frozen=True)
class FeatureSet:
    nome: str
    stato: str
    colonne: tuple[str, ...]
    descrizione: str


# ---------------------------------------------------------------------------
# BASE — congelato. Scritto per esteso, non derivato da un modulo che puo'
# cambiare: se `features/form.py` un giorno producesse una colonna in piu',
# BASE non deve accorgersene.
# ---------------------------------------------------------------------------

_STATISTICHE_FORMA = (
    "goals", "np_xg", "ppda", "deep", "shots", "sot", "xpts", "points",
)
_VISTE = ("home", "away", "diff")

_BASE_COLONNE = tuple(
    f"{vista}_{stat}_{verso}_ewm"
    for vista in _VISTE
    for stat in _STATISTICHE_FORMA
    for verso in ("for", "against")
) + (
    "home_matches_season", "home_matches_total",
    "away_matches_season", "away_matches_total",
)

# Il numero si fissa a parte: se la comprensione sopra venisse "sistemata" e
# producesse altro, il conteggio smette di tornare prima ancora del test.
N_BASE = 52
assert len(_BASE_COLONNE) == N_BASE, f"BASE ha {len(_BASE_COLONNE)} colonne, non {N_BASE}"


# ---------------------------------------------------------------------------
# I set sperimentali. Le liste sono copiate qui e non importate dai moduli di
# feature, per la stessa ragione di BASE: il registro e' la fonte, i moduli
# di feature producono colonne che il registro deve riconoscere.
# ---------------------------------------------------------------------------

_CONTESTO_COLONNE = (
    "home_rest_days", "away_rest_days", "diff_rest_days",
    "home_matches_14d", "away_matches_14d", "diff_matches_14d",
    "home_cup_14d", "away_cup_14d", "diff_cup_14d",
    "is_midweek", "is_derby", "derby_intensity",
)

_GIOCATORI_COLONNE = (
    "home_quota_minuti_assenti", "away_quota_minuti_assenti",
    "diff_quota_minuti_assenti",
    "home_quota_ga_assente", "away_quota_ga_assente",
    "diff_quota_ga_assente",
    "home_n_assenti", "away_n_assenti", "diff_n_assenti",
)

# Stesse statistiche e versi di BASE, sulla sola sede in cui la squadra gioca.
_FORMA_VENUE_COLONNE = tuple(
    f"{vista}_{stat}_{verso}_ewm_sede"
    for vista in _VISTE
    for stat in _STATISTICHE_FORMA
    for verso in ("for", "against")
)
assert len(_FORMA_VENUE_COLONNE) == 48

SETS: dict[str, FeatureSet] = {
    "BASE": FeatureSet(
        "BASE", CONGELATO, _BASE_COLONNE,
        "Forma recente, medie mobili a half-life 6, piu' i contatori di "
        "partite. E' il set su cui poggia M5; il mercato entra come "
        "ancoraggio, non come colonna.",
    ),
    "CONTESTO": FeatureSet(
        "CONTESTO", SCARTATO, _CONTESTO_COLONNE,
        "Riposo, congestione, coppe europee, derby. Misurato due volte "
        "(senza e con coppe), intervallo sempre a cavallo dello zero.",
    ),
    "GIOCATORI": FeatureSet(
        "GIOCATORI", PROVVISORIO, _GIOCATORI_COLONNE,
        "Quota di minuti e di gol+assist indisponibili, numero di assenti. "
        "-0.00027 con IC [-0.00054, -0.00001], p=0.0225: supera la regola ma "
        "non Bonferroni a m=3 (soglia 0.0083). Verifiche di robustezza "
        "(15 set 2026): segno negativo su 5 semi su 5 (da -0.00016 a "
        "-0.00032), ma intervallo sotto zero solo in 2; la variante "
        "simmetrica (solo differenze) resta sotto zero in 5 su 5 e vale "
        "quanto la completa, quindi l'asimmetria 20:1 era una "
        "rappresentazione. Non promuove: le verifiche possono solo "
        "declassare. Contro il mercato resta indistinguibile.",
    ),
    "FORMA_VENUE": FeatureSet(
        "FORMA_VENUE", SCARTATO, _FORMA_VENUE_COLONNE,
        "Blocco D: forma condizionata alla sede — la squadra di casa sulle "
        "sue sole partite in casa, quella in trasferta sulle sole in "
        "trasferta, half-life 10 (FORM_HALFLIFE_VENUE). Costruito in "
        "memoria da experiments/forma_venue.py, nessun parquet. Misurato "
        "SOLO in validazione (16 set 2026), quindi a costo zero: media di 5 "
        "semi +0.00001, IC [-0.00035, +0.00039]. Nullo, e il modello le usa "
        "eccome — 50.5% del guadagno con il 48% delle colonne: sono un altro "
        "modo di dire la stessa cosa di BASE, non informazione nuova.",
    ),
}


# ---------------------------------------------------------------------------

def colonne(sets: list[str] | tuple[str, ...]) -> list[str]:
    """Le colonne di un elenco di set, nell'ordine dichiarato."""
    ignoti = [s for s in sets if s not in SETS]
    if ignoti:
        raise KeyError(f"set sconosciuti: {ignoti}. Registrati: {sorted(SETS)}")
    if "BASE" not in sets:
        # Non e' vietato, ma e' quasi sempre un errore: un set sperimentale da
        # solo misura qualcosa che non e' "BASE piu' il blocco".
        log.warning("set %s senza BASE: stai misurando il blocco da solo", list(sets))
    out: list[str] = []
    for s in sets:
        for c in SETS[s].colonne:
            if c not in out:
                out.append(c)
    return out


def escludi_per(candidate: list[str], sets: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """
    Da un elenco di set all'argomento `escludi` delle classi di `gbm.py`.

    Le classi esistenti non sanno niente dei set: accettano un elenco di
    colonne da togliere. Questa funzione traduce senza modificarle — cosi' il
    registro si usa oggi, e `gbm.py` resta quello su cui gira il report.

    `candidate` sono le colonne che `gbm.form_features(df, escludi=())`
    considererebbe. Si esclude tutto cio' che non sta nei set dichiarati:
    anche le colonne che non appartengono a nessun set, che quindi restano
    fuori per default invece di entrare di straforo.
    """
    tenute = set(colonne(sets))
    return tuple(c for c in candidate if c not in tenute)


def non_registrate(candidate: list[str]) -> list[str]:
    """Colonne candidate che nessun set riconosce."""
    note = {c for fs in SETS.values() for c in fs.colonne}
    return [c for c in candidate if c not in note]


def verifica(df: pd.DataFrame) -> pd.DataFrame:
    """Per ogni set: quante colonne dichiarate ci sono davvero nel dataset."""
    righe = []
    for fs in SETS.values():
        presenti = [c for c in fs.colonne if c in df.columns]
        righe.append({
            "set": fs.nome, "stato": fs.stato, "dichiarate": len(fs.colonne),
            "presenti": len(presenti),
            "copertura_righe": (float(df[presenti].notna().any(axis=1).mean())
                                if presenti else float("nan")),
        })
    return pd.DataFrame(righe)


def main() -> None:
    argparse.ArgumentParser(description="Stato dei set di feature").parse_args()
    from ..evaluation.evaluate import load_dataset
    from ..models.gbm import form_features

    df = load_dataset()
    pd.set_option("display.width", 160)
    print("\n=== SET DI FEATURE ===")
    print(verifica(df).to_string(index=False))

    extra = non_registrate(form_features(df, escludi=()))
    print(f"\ncolonne candidate che nessun set riconosce: {len(extra)}")
    if extra:
        print("  ", extra)


if __name__ == "__main__":
    main()
