"""
Le invarianti di produzione sopravvivono a `python -O` — il difetto B3.

PERCHE' ESISTE. `python -O`, o `PYTHONOPTIMIZE=1` che puo' arrivare
dall'ambiente senza che nessuno lo scriva nel comando, **cancella tutti gli
assert**. Finche' le difese del progetto erano assert, lanciare la produzione
in quel modo toglieva esattamente i controlli dichiarati non negoziabili — a
partire da quello sull'ordine previsione/fischio d'inizio, che come racconta
CLAUDE.md ha gia' fallito una volta per un errore di fuso orario.

`ruff` con S101 attivo impedisce di scriverne di nuovi. Questo test verifica
la cosa che ruff non puo' vedere: che i controlli riscritti **scattino
davvero** quando l'interprete gira ottimizzato. Per farlo serve un processo
separato: il flag si decide all'avvio e non si puo' attivare a meta' sessione.

    python -m tests.test_invarianti_ottimizzate
"""

import subprocess
import sys

# Tre difese di tre moduli diversi: le righe future senza colonne
# post-partita, il riposo positivo nel contesto, il de-vigging che somma a
# uno. Ognuna deve sollevare, non passare in silenzio.
CONTROLLI = """
import sys
import numpy as np
import pandas as pd

from goalmodel.features import context, market
from goalmodel.prediction import predict

# Non un assert: qui sarebbe la prima riga a sparire, e il test passerebbe
# dichiarando di aver provato qualcosa che non ha provato.
if not sys.flags.optimize:
    raise SystemExit("il sottoprocesso non gira con -O: il test non prova niente")

esiti = []


def scatta(nome, fn):
    try:
        fn()
    except ValueError:
        esiti.append(nome)
    except Exception as exc:
        print(f"{nome}: eccezione inattesa {type(exc).__name__}: {exc}")


scatta("post-partita su riga futura", lambda: predict.assert_no_post_match(
    pd.DataFrame({"FTHG": [2.0], "FTAG": [1.0]})))

scatta("riposo <= 0 giorni", lambda: context.assert_no_leakage(
    pd.DataFrame(),
    pd.DataFrame({"team": ["A"], "rest_days": [0.0], "matches_14d": [1]})))

trio = ["mkt_p_home", "mkt_p_draw", "mkt_p_away"]
rotto = pd.DataFrame({
    **{c: [0.5] for c in trio},                       # somma 1.5, non 1
    **{c + "_prop": [0.5] for c in trio},
    "mkt_p_over25": [0.5], "mkt_p_under25": [0.5],
    "mkt_overround": [1.05], "mkt_shin_z": [0.02],
})
scatta("1X2 che non somma a 1", lambda: market.validate(rotto))

print(" ".join(f"[{e}]" for e in esiti))
sys.exit(0 if len(esiti) == 3 else 1)
"""


def test_invarianti_sopravvivono_a_O() -> None:
    esito = subprocess.run(  # noqa: S603 - forma a lista, sys.executable
        [sys.executable, "-O", "-c", CONTROLLI],
        capture_output=True, text=True, check=False,
    )
    assert esito.returncode == 0, (
        "con -O almeno una invariante di produzione non scatta piu':\n"
        f"{esito.stdout}{esito.stderr}"
    )
    print(f"  con -O scattano ancora: {esito.stdout.strip()}   ok")


def main() -> None:
    test_invarianti_sopravvivono_a_O()
    print("\ncontrolli sulle invarianti superati")


if __name__ == "__main__":
    main()
