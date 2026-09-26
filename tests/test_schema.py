"""
Il contratto dei file su disco morde davvero.

PERCHE' ESISTE. Uno schema che non e' mai stato visto fallire non e' uno
schema: e' documentazione che gira. Ognuno dei casi qui sotto e' un modo in
cui questo progetto ha gia' perso dati **in silenzio**, o in cui potrebbe:
un tipo cambiato che azzera un merge, una colonna sparita che diventa NaN, una
chiave duplicata che moltiplica righe lasciando le metriche plausibili.

Nessun file vero viene letto: si costruiscono i frame a mano e si chiama
`Schema.verifica` direttamente.

    python -m tests.test_schema
"""

import pandas as pd
import pytest

from goalmodel import config, schema

KEYS = config.JOIN_KEYS
RIMEDIO = "goalmodel normalize --build"


def _master(n: int = 3, **override) -> pd.DataFrame:
    """Un `matches_master` minimo ma a norma."""
    df = pd.DataFrame({
        "league": pd.array(["ITA-Serie A"] * n, dtype="string"),
        "season": pd.array(["2324"] * n, dtype="string"),
        "home_team": pd.array([f"Casa{i}" for i in range(n)], dtype="string"),
        "away_team": pd.array([f"Fuori{i}" for i in range(n)], dtype="string"),
        "date": pd.to_datetime(["2024-01-01"] * n),
        "FTHG": [1.0] * n, "FTAG": [0.0] * n,
        "FTR": pd.array(["H"] * n, dtype="string"),
    })
    return df.assign(**override)


def test_il_caso_buono_passa() -> None:
    """Prima di tutto: lo schema non deve fermare il dato giusto."""
    schema.MATCHES_MASTER.verifica(_master(), RIMEDIO)
    print("  un master a norma passa                        ok")


def test_colonna_mancante() -> None:
    df = _master().drop(columns=["FTR"])
    with pytest.raises(schema.SchemaNonRispettato, match="FTR"):
        schema.MATCHES_MASTER.verifica(df, RIMEDIO)
    print("  una colonna obbligatoria che sparisce           ok")


def test_stagione_numerica() -> None:
    """
    Il caso che non solleva da nessuna parte e che rompe tutto.

    `season` come intero contro `season` come stringa produce un merge con
    ZERO corrispondenze: non un errore, solo NaN dappertutto, e sembra che
    manchino i dati invece che i tipi.
    """
    df = _master(season=[2324, 2324, 2324])
    with pytest.raises(schema.SchemaNonRispettato, match="non testo"):
        schema.MATCHES_MASTER.verifica(df, RIMEDIO)
    print("  season numerica invece che testuale             ok")


def test_data_come_stringa() -> None:
    df = _master(date=["2024-01-01", "2024-01-02", "2024-01-03"])
    with pytest.raises(schema.SchemaNonRispettato, match="non una"):
        schema.MATCHES_MASTER.verifica(df, RIMEDIO)
    print("  date come stringhe invece che datetime          ok")


def test_chiave_duplicata() -> None:
    """La regola non negoziabile n.5, verificata invece che assunta."""
    df = _master(2)
    doppia = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    with pytest.raises(schema.SchemaNonRispettato, match="non e' univoca"):
        schema.MATCHES_MASTER.verifica(doppia, RIMEDIO)
    print("  quadrupla duplicata nel master                  ok")


def test_chiave_nulla() -> None:
    df = _master(2)
    df.loc[0, "home_team"] = None
    with pytest.raises(schema.SchemaNonRispettato, match="nulli nella chiave"):
        schema.MATCHES_MASTER.verifica(df, RIMEDIO)
    print("  chiave con valori nulli                         ok")


# ---------------------------------------------------------------------------
# Il calendario: duplicati SI', ma solo quelli che sappiamo spiegare
# ---------------------------------------------------------------------------

def _schedule(week) -> pd.DataFrame:
    """Due righe con la stessa quadrupla: Spezia-Verona 2022/23."""
    return pd.DataFrame({
        "league": pd.array(["ITA-Serie A"] * 2, dtype="string"),
        "season": pd.array(["2223"] * 2, dtype="string"),
        "home_team": pd.array(["Spezia"] * 2, dtype="string"),
        "away_team": pd.array(["Hellas Verona"] * 2, dtype="string"),
        "date": pd.to_datetime(["2023-03-05", "2023-06-11"]),
        "week": week,
    })


def test_lo_spareggio_e_un_duplicato_ammesso() -> None:
    """
    Spezia-Hellas Verona 2022/23 compare due volte nel calendario vero: la
    partita di campionato e lo spareggio salvezza. Non e' un bug del dato, e'
    un caso che la regola n.5 non prevede — e si riconosce dal fatto che lo
    spareggio non appartiene a nessuna giornata.
    """
    schema.FBREF_SCHEDULE.verifica(_schedule([25, None]), RIMEDIO)
    print("  campionato + spareggio: duplicato ammesso       ok")


def test_due_partite_di_campionato_uguali_no() -> None:
    """
    Se entrambe le righe hanno una giornata, non e' uno spareggio: e' un
    doppione, e ammetterlo perche' "il calendario ha duplicati attesi"
    sarebbe non controllare.
    """
    with pytest.raises(schema.SchemaNonRispettato, match="NON sono"):
        schema.FBREF_SCHEDULE.verifica(_schedule([25, 38]), RIMEDIO)
    print("  due partite di campionato identiche: rifiutate  ok")


@pytest.mark.richiede_dati
def test_il_sottoinsieme_non_da_falsi_allarmi() -> None:
    """
    `load_master(columns=[...])` chiede meno colonne di proposito: pretendere
    quelle non richieste sarebbe un allarme su un problema che non c'e'.
    """
    from goalmodel import data

    sub = data.load_master(columns=[*KEYS, "FTHG"])
    assert list(sub.columns) == [*KEYS, "FTHG"]
    print("  lettura di un sottoinsieme: nessun falso allarme ok")


def main() -> None:
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
