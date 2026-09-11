"""
Test del blocco B: il peso delle assenze.

PERCHE' ESISTE. Questo blocco ha un modo di rompersi che non da' nessun
errore: usare i minuti di FINE STAGIONE invece di quelli alla data. Il codice
gira, i numeri sono plausibili, e il modello impara dal futuro — un giocatore
infortunato a ottobre risulta poco importante perche' ha pochi minuti totali,
e quel "poco importante" contiene l'informazione che si sarebbe infortunato.

Qui si conta a mano su una rosa di tre giocatori.

    python -m tests.test_players
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features import players  # noqa: E402


def rosa() -> pd.DataFrame:
    """
    Tre giocatori, tre giornate, minuti e gol+assist contabili sulle dita.

        data        Rossi            Bianchi          Verdi
        01/09       90 min, 0.5      45 min, 0.1      -
        08/09       90 min, 0.3      90 min, 0.4      30 min, 0.0
        15/09       90 min, 0.2      -                90 min, 0.6
    """
    righe = [
        ("2526", "Napoli", "Rossi",   datetime(2025, 9, 1),  90, 0.5),
        ("2526", "Napoli", "Bianchi", datetime(2025, 9, 1),  45, 0.1),
        ("2526", "Napoli", "Rossi",   datetime(2025, 9, 8),  90, 0.3),
        ("2526", "Napoli", "Bianchi", datetime(2025, 9, 8),  90, 0.4),
        ("2526", "Napoli", "Verdi",   datetime(2025, 9, 8),  30, 0.0),
        ("2526", "Napoli", "Rossi",   datetime(2025, 9, 15), 90, 0.2),
        ("2526", "Napoli", "Verdi",   datetime(2025, 9, 15), 90, 0.6),
    ]
    df = pd.DataFrame(righe, columns=["season", "team", "player", "date",
                                      "minuti", "ga"])
    df["chiave_giocatore"] = players.normalizza_nome(df["player"])
    return df


def test_stato_e_precedente() -> None:
    """
    Lo stato di una data e' la somma di cio' che viene PRIMA, esclusa la data.

    E' l'unico controllo che separa una feature calcolabile a T-24h da una che
    guarda il risultato della partita che deve predire.
    """
    stato = players.pesi_alla_data(rosa())
    vista = stato.set_index(["date", "chiave_giocatore"])

    # Alla prima giornata nessuno ha storico: `pesi_alla_data` scarta le righe
    # a zero minuti, quindi la data del 1 settembre non deve comparire.
    assert datetime(2025, 9, 1) not in set(stato["date"]), \
        "alla prima partita nessun giocatore puo' avere minuti alla data"
    print("  prima giornata: nessuno ha storico                   ok")

    # 8 settembre: conta solo il 1 settembre.
    assert vista.loc[(datetime(2025, 9, 8), "rossi"), "minuti_ad"] == 90
    assert vista.loc[(datetime(2025, 9, 8), "bianchi"), "minuti_ad"] == 45
    assert abs(vista.loc[(datetime(2025, 9, 8), "rossi"), "ga_ad"] - 0.5) < 1e-9
    print("  8/9: 90 e 45 minuti, solo dalla giornata precedente  ok")

    # 15 settembre: 1 + 8 settembre. Rossi 180, Bianchi 135, Verdi 30.
    assert vista.loc[(datetime(2025, 9, 15), "rossi"), "minuti_ad"] == 180
    assert vista.loc[(datetime(2025, 9, 15), "bianchi"), "minuti_ad"] == 135
    assert vista.loc[(datetime(2025, 9, 15), "verdi"), "minuti_ad"] == 30
    assert abs(vista.loc[(datetime(2025, 9, 15), "rossi"), "ga_ad"] - 0.8) < 1e-9
    print("  15/9: 180, 135 e 30 minuti cumulati                  ok")

    # Il controllo che vale il test: i minuti TOTALI di Rossi sono 270, e
    # 270 non deve comparire da nessuna parte. Se comparisse, il cumulato
    # includerebbe la partita corrente.
    assert 270 not in set(stato["minuti_ad"]), \
        "il cumulato include la partita corrente: e' leakage"
    print("  il totale di fine periodo non compare mai            ok")


def test_bianchi_resta_in_rosa() -> None:
    """
    Chi non gioca una partita resta in rosa con i minuti che aveva.

    E' il caso dell'assente: se sparisse dallo stato, il suo peso sarebbe zero
    proprio nelle partite in cui manca — cioe' la feature misurerebbe sempre
    zero e il blocco sembrerebbe non avere segnale per un errore di codice.
    """
    stato = players.pesi_alla_data(rosa())
    q = stato[(stato["date"] == datetime(2025, 9, 15))
              & (stato["chiave_giocatore"] == "bianchi")]
    assert len(q) == 1 and q["minuti_ad"].iloc[0] == 135, q.to_dict("records")
    print("  chi salta la partita resta in rosa con i suoi minuti ok")


def test_nomi_fra_fonti() -> None:
    """WhoScored e FBref scrivono gli accenti in modo diverso."""
    a = players.normalizza_nome(pd.Series(["Rafael Leão", "Franco Vázquez",
                                           "Ciro Immobile"]))
    b = players.normalizza_nome(pd.Series(["Rafael Leao", "Franco Vazquez",
                                           "Ciro  Immobile "]))
    assert list(a) == list(b), list(zip(a, b))
    print("  accenti e spazi normalizzati fra le due fonti        ok")


def test_peso_si_semplifica() -> None:
    """
    "Minuti pesati per produzione per 90" e' la produzione totale, e va saputo.

    minuti x (GA / (minuti/90)) = 90 x GA. Non e' un errore, e' aritmetica:
    ma chi legge "minuti pesati" si aspetta che il peso dipenda anche dai
    minuti, e non e' cosi'. Il test lo fissa perche' nessuno lo "corregga"
    pensando a un bug.
    """
    minuti = np.array([90.0, 45.0, 270.0])
    ga = np.array([0.5, 0.1, 0.9])
    ga90 = ga / (minuti / 90.0)
    assert np.allclose(minuti * ga90, 90.0 * ga)
    print("  peso = 90 x GA: la semplificazione e' fissata        ok")


def test_orario_nella_data_non_rompe_il_join() -> None:
    """
    `matches_master.date` porta l'ora, le date FBref no: il join deve reggere.

    E' il bug che ha gia' morso una volta: un merge esatto fra
    `2021-08-28 19:45:00` e `2021-08-28 00:00:00` non aggancia NIENTE e non
    solleva niente — si ottiene NaN su tutte le righe e sembra che gli assenti
    non esistano. Qui la partita ha l'ora e il join deve funzionare lo stesso.
    """
    giocatori = rosa()
    partite = pd.DataFrame([{
        "league": "ITA-Serie A", "season": "2526",
        "home_team": "Napoli", "away_team": "Roma",
        # Con l'ora attaccata, come nel dataset vero.
        "date": datetime(2025, 9, 15, 19, 45),
    }])
    assenze = pd.DataFrame([{
        "league": "ITA-Serie A", "season": "2526",
        "home_team": "Napoli", "away_team": "Roma", "team": "Napoli",
        "player": "Bianchi", "game_id": 1, "reason": "injured",
        "indisponibile": True, "chiave_giocatore": "bianchi",
    }])

    out = players.build(partite, save=False, assenze=assenze,
                        giocatori=giocatori)
    q = out.iloc[0]
    assert pd.notna(q["home_quota_minuti_assenti"]),         "join fallito: l'ora nella data ha impedito l'aggancio"

    # Bianchi ha 135 minuti alla data; la rosa ne ha 135+180+30 = 345.
    atteso = 135 / 345
    assert abs(q["home_quota_minuti_assenti"] - atteso) < 1e-9,         f"atteso {atteso:.4f}, ottenuto {q['home_quota_minuti_assenti']:.4f}"
    assert q["home_n_assenti"] == 1
    # La Roma non ha assenti dichiarati in questo test: resta NaN, non zero.
    print(f"  data con ora: quota {atteso:.3f} calcolata correttamente  ok")


def main() -> None:
    test_stato_e_precedente()
    test_bianchi_resta_in_rosa()
    test_nomi_fra_fonti()
    test_peso_si_semplifica()
    test_orario_nella_data_non_rompe_il_join()
    print("\ntutti i controlli sul blocco giocatori superati")


if __name__ == "__main__":
    main()
