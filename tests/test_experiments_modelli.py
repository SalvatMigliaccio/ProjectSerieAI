"""
Test dei modelli sperimentali: M5Set e M5MediaSemi.

PERCHE' ESISTE. L'analisi di robustezza del blocco B non riaddestra la media
dei cinque semi: la RICOSTRUISCE a posteriori dalle previsioni salvate di
ciascun seme, con `media_log_lambda`. E' corretto solo se la ricostruzione
coincide con cio' che produrrebbe il modello `M5MediaSemi` addestrato davvero.
Se non coincidesse, l'analisi misurerebbe un modello che non esiste — senza
nessun errore a segnalarlo.

Verifica anche che dichiarare i set produca esattamente le colonne attese:
un M5Set(["BASE"]) che vedesse una colonna in piu' misurerebbe il blocco
sbagliato.

    python -m tests.test_experiments_modelli
"""


import numpy as np
import pytest

from goalmodel import config
from goalmodel.evaluation.evaluate import load_dataset
from goalmodel.experiments.modelli import M5MediaSemi, M5Set, media_log_lambda
from goalmodel.features import sets
from goalmodel.models.baseline import PRED_COLS

SEMI = (0, 1, 2)


def _dati():
    """Un taglio vero: addestra fino alla 2023/24, predice la sua prima giornata."""
    df = load_dataset()
    played = df[df["FTR"].notna() & ~df["season"].isin(config.BURN_IN_SEASONS)]
    cutoff = df.loc[df["season"] == "2324", "date"].min()
    train = played[played["date"] < cutoff]
    test = df[(df["season"] == "2324") & (df["matchday"] == 1)]
    return train, test


pytestmark = [pytest.mark.richiede_dati, pytest.mark.richiede_dataset_completo]


@pytest.fixture(scope="module")
def _taglio():
    return _dati()


@pytest.fixture(scope="module")
def train(_taglio):
    return _taglio[0]


@pytest.fixture(scope="module")
def test(_taglio):
    return _taglio[1]


def test_colonne_dichiarate(train) -> None:
    base = M5Set(["BASE"]).fit(train)
    assert set(base.features_) == set(sets.SETS["BASE"].colonne), (
        f"M5Set(['BASE']) usa colonne diverse da BASE: "
        f"{sorted(set(base.features_) ^ set(sets.SETS['BASE'].colonne))}"
    )
    print(f"  M5Set(['BASE']) usa esattamente le {len(base.features_)} colonne di BASE  ok")

    togli = ("home_quota_minuti_assenti", "away_quota_minuti_assenti")
    simm = M5Set(["BASE", "GIOCATORI"], togli=togli).fit(train)
    atteso = (set(sets.SETS["BASE"].colonne) | set(sets.SETS["GIOCATORI"].colonne)) - set(togli)
    assert set(simm.features_) == atteso
    assert not set(togli) & set(simm.features_)
    print(f"  variante simmetrica: {len(simm.features_)} colonne, le due tolte assenti   ok")

    try:
        M5Set(["BASE"], togli=("home_quota_minuti_assenti",)).fit(train)
    except ValueError:
        print("  togliere una colonna fuori dai set dichiarati e' rifiutato   ok")
    else:
        raise AssertionError("togli accettava una colonna che non sta nei set")


def test_media_ricostruita_uguale_al_modello(train, test) -> None:
    """La strada dell'analisi e quella del modello devono dare gli stessi numeri."""
    modello = M5MediaSemi(["BASE", "GIOCATORI"], semi=SEMI).fit(train)
    diretta = modello.predict(test)

    singoli = [M5Set(["BASE", "GIOCATORI"], seed=s).fit(train).predict(test)
               for s in SEMI]
    ricostruita = media_log_lambda(singoli, test.index)

    a = diretta[PRED_COLS].to_numpy(float)
    b = ricostruita[PRED_COLS].to_numpy(float)
    assert np.array_equal(a, b, equal_nan=True), (
        f"media ricostruita diversa dal modello: scarto massimo "
        f"{np.nanmax(np.abs(a - b)):.3e}"
    )
    print(f"  media ricostruita = M5MediaSemi, bit a bit su {len(test)} partite   ok")

    # I semi devono davvero differire, altrimenti la media non media niente e
    # la verifica di robustezza sui semi non verificherebbe niente.
    lam = np.vstack([s["lambda_home"].to_numpy(float) for s in singoli])
    spread = float(np.nanmax(lam.max(axis=0) - lam.min(axis=0)))
    assert spread > 0, "i semi producono previsioni identiche: il seme non agisce"
    print(f"  i semi differiscono davvero (scarto max lambda {spread:.2e})     ok")


def main() -> None:
    train, test = _dati()
    test_colonne_dichiarate(train)
    test_media_ricostruita_uguale_al_modello(train, test)
    print("\ntutti i controlli sui modelli sperimentali superati")


if __name__ == "__main__":
    main()
