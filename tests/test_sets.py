"""
Test del registro dei set di feature e dell'isolamento degli esperimenti.

PERCHE' ESISTE. Il registro serve solo se le sue promesse reggono: BASE non
cambia, i set non si sovrappongono, ogni colonna del dataset appartiene a un
set, e un esperimento non puo' scrivere dove legge la produzione. Ognuna di
queste promesse, se si rompe, non da' errore — produce un modello che usa
colonne diverse da quelle dichiarate.

    python -m tests.test_sets
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd


from goalmodel import config  # noqa: E402
from goalmodel.features import sets  # noqa: E402

# BASE per esteso, copiato a mano e non importato: se qualcuno modifica la
# definizione in `sets.py` — anche con buone intenzioni — questo elenco non
# si aggiorna da solo, e il test si accorge che BASE non e' piu' quello
# congelato.
BASE_CONGELATO = {
    "home_goals_for_ewm", "home_goals_against_ewm",
    "home_np_xg_for_ewm", "home_np_xg_against_ewm",
    "home_ppda_for_ewm", "home_ppda_against_ewm",
    "home_deep_for_ewm", "home_deep_against_ewm",
    "home_shots_for_ewm", "home_shots_against_ewm",
    "home_sot_for_ewm", "home_sot_against_ewm",
    "home_xpts_for_ewm", "home_xpts_against_ewm",
    "home_points_for_ewm", "home_points_against_ewm",
    "away_goals_for_ewm", "away_goals_against_ewm",
    "away_np_xg_for_ewm", "away_np_xg_against_ewm",
    "away_ppda_for_ewm", "away_ppda_against_ewm",
    "away_deep_for_ewm", "away_deep_against_ewm",
    "away_shots_for_ewm", "away_shots_against_ewm",
    "away_sot_for_ewm", "away_sot_against_ewm",
    "away_xpts_for_ewm", "away_xpts_against_ewm",
    "away_points_for_ewm", "away_points_against_ewm",
    "diff_goals_for_ewm", "diff_goals_against_ewm",
    "diff_np_xg_for_ewm", "diff_np_xg_against_ewm",
    "diff_ppda_for_ewm", "diff_ppda_against_ewm",
    "diff_deep_for_ewm", "diff_deep_against_ewm",
    "diff_shots_for_ewm", "diff_shots_against_ewm",
    "diff_sot_for_ewm", "diff_sot_against_ewm",
    "diff_xpts_for_ewm", "diff_xpts_against_ewm",
    "diff_points_for_ewm", "diff_points_against_ewm",
    "home_matches_season", "home_matches_total",
    "away_matches_season", "away_matches_total",
}


def test_base_congelato() -> None:
    base = set(sets.SETS["BASE"].colonne)
    assert sets.SETS["BASE"].stato == sets.CONGELATO
    tolte, aggiunte = BASE_CONGELATO - base, base - BASE_CONGELATO
    assert not tolte and not aggiunte, (
        f"BASE e' cambiato: tolte {sorted(tolte)}, aggiunte {sorted(aggiunte)}. "
        f"BASE e' congelato: i cambiamenti vanno in un set nuovo."
    )
    print(f"  BASE congelato: {len(base)} colonne, identiche all'elenco   ok")


def test_set_disgiunti() -> None:
    visti: dict[str, str] = {}
    for fs in sets.SETS.values():
        assert fs.stato in sets.STATI, f"{fs.nome}: stato ignoto {fs.stato}"
        for c in fs.colonne:
            assert c not in visti, f"{c} sta sia in {visti[c]} sia in {fs.nome}"
            visti[c] = fs.nome
    print(f"  {len(sets.SETS)} set, nessuna colonna in due set             ok")


def test_dataset_reale() -> None:
    """Ogni colonna candidata appartiene a un set, e BASE si ricostruisce esatto."""
    from goalmodel.evaluation.evaluate import load_dataset
    from goalmodel.models.gbm import form_features

    df = load_dataset()
    candidate = form_features(df, escludi=())

    extra = sets.non_registrate(candidate)
    assert not extra, (
        f"colonne nel dataset che nessun set riconosce: {extra}. "
        f"Registrale in src/features/sets.py prima di usarle."
    )
    print(f"  {len(candidate)} colonne candidate, tutte registrate          ok")

    solo_base = form_features(df, escludi=sets.escludi_per(candidate, ["BASE"]))
    assert set(solo_base) == BASE_CONGELATO, (
        f"sets=['BASE'] non produce BASE: differenza "
        f"{sorted(set(solo_base) ^ BASE_CONGELATO)}"
    )
    print("  escludi_per(['BASE']) ricostruisce BASE esatto            ok")

    con_giocatori = form_features(
        df, escludi=sets.escludi_per(candidate, ["BASE", "GIOCATORI"]))
    atteso = BASE_CONGELATO | set(sets.SETS["GIOCATORI"].colonne)
    assert set(con_giocatori) == atteso
    print(f"  BASE+GIOCATORI = {len(con_giocatori)} colonne, niente di piu'    ok")


def test_esperimenti_non_scrivono_in_produzione() -> None:
    """
    La guardia rifiuta le cartelle protette e lascia passare le altre.

    Si prova per ultima: `proteggi_produzione` resta attiva nel processo, e
    il resto del test non deve girare con pandas modificato.
    """
    from goalmodel import experiments

    experiments.proteggi_produzione()
    df = pd.DataFrame({"a": [1]})

    for protetta in (config.PROCESSED, config.TRACK_RECORD):
        try:
            df.to_parquet(protetta / "__non_deve_esistere__.parquet")
        except PermissionError:
            pass
        else:
            (protetta / "__non_deve_esistere__.parquet").unlink(missing_ok=True)
            raise AssertionError(f"la guardia ha lasciato scrivere in {protetta}")

    with tempfile.TemporaryDirectory() as tmp:
        df.to_csv(Path(tmp) / "consentito.csv")
    print("  esperimenti: data/processed e track_record rifiutati      ok")


def main() -> None:
    test_base_congelato()
    test_set_disgiunti()
    test_dataset_reale()
    test_esperimenti_non_scrivono_in_produzione()
    print("\ntutti i controlli sul registro dei set superati")


if __name__ == "__main__":
    main()
