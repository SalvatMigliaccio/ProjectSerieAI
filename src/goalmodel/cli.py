"""
Un solo entry point per tutti i comandi: `goalmodel <comando> [opzioni]`.

PERCHE' ESISTE. Finche' i comandi si lanciavano come `python -m src.<modulo>`
funzionavano solo dalla radice del repository, e solo se `src/` era importabile
da li'. Con il pacchetto installato (`pip install -e .`) il
comando `goalmodel` e' sulla PATH e gira da qualsiasi directory, uguale su
Linux e su Windows: e' la stessa ragione per cui `ingest.py` e' uscito dalla
radice.

COME DELEGA, E PERCHE' COSI'. Questo modulo non reimplementa nessun
argomento: riscrive `sys.argv` e chiama il `main()` del modulo giusto, che
ha gia' il suo argparse. Quindi ogni opzione documentata in `docs/COMANDI.md`
continua a valere identica, `--help` di ogni comando resta quello scritto dal
suo autore, e aggiungere un'opzione non richiede di toccare questo file.

I vecchi `python -m goalmodel.<modulo>` continuano a funzionare: i `main()`
sono intatti. Questo e' un secondo modo di chiamarli, non un rimpiazzo.
"""

from __future__ import annotations

import importlib
import sys

# comando -> (modulo, descrizione). L'ordine e' quello del ciclo di lavoro,
# non alfabetico: e' come si legge l'aiuto.
COMANDI: dict[str, tuple[str, str]] = {
    "ingest":        ("goalmodel.ingest", "scarica i dati grezzi dalle fonti"),
    "normalize":     ("goalmodel.normalize", "nomi squadra e join -> matches_master"),
    "features-form":    ("goalmodel.features.form", "medie mobili leakage-safe"),
    "features-market":  ("goalmodel.features.market", "de-vigging delle quote"),
    "features-context": ("goalmodel.features.context", "riposo, congestione, coppe, derby"),
    "features-players": ("goalmodel.features.players", "minuti e gol+assist indisponibili"),
    "features-sets":    ("goalmodel.features.sets", "stato dei set di feature"),
    "evaluate":      ("goalmodel.evaluation.evaluate", "walk-forward, RPS, confronti appaiati"),
    "power":         ("goalmodel.evaluation.power_analysis", "effetto minimo rilevabile"),
    "baseline":      ("goalmodel.models.baseline", "controlli sulla matrice dei risultati"),
    "dixon-coles":   ("goalmodel.models.dixon_coles", "M3: taratura e controlli"),
    "gbm":           ("goalmodel.models.gbm", "M4/M5/M6: taratura, importanza"),
    "predict":       ("goalmodel.prediction.predict", "previsione singola (uso avanzato)"),
    "predict-round": ("goalmodel.prediction.predict_round", "giornata: da aperta a predetta"),
    "close-round":   ("goalmodel.prediction.close_round", "giornata: da giocata a chiusa"),
    "rounds":        ("goalmodel.prediction.rounds", "stato di ogni giornata"),
    "backtest":      ("goalmodel.prediction.backtest_log", "metriche reali dal registro"),
    "report":        ("goalmodel.reporting.report", "rigenera il report HTML"),
}


def _aiuto() -> str:
    larghezza = max(len(c) for c in COMANDI)
    righe = [f"  {c:<{larghezza}}  {d}" for c, (_, d) in COMANDI.items()]
    return (
        "uso: goalmodel <comando> [opzioni]\n\n"
        "I due comandi della giornata sono 'predict-round' e 'close-round':\n"
        "decidono da soli su quale giornata agire.\n\n"
        "comandi:\n" + "\n".join(righe) + "\n\n"
        "L'aiuto di un comando: goalmodel <comando> --help\n"
        "Tempi misurati e casi particolari: docs/COMANDI.md\n"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_aiuto())
        return 0
    if argv[0] in ("-V", "--version"):
        from goalmodel import __version__
        print(f"goalmodel {__version__}")
        return 0

    comando, resto = argv[0], argv[1:]
    if comando not in COMANDI:
        import difflib
        vicini = difflib.get_close_matches(comando, COMANDI, n=3, cutoff=0.5)
        suggerimento = f"\nForse intendevi: {', '.join(vicini)}" if vicini else ""
        print(f"comando sconosciuto: '{comando}'{suggerimento}\n", file=sys.stderr)
        print(_aiuto(), file=sys.stderr)
        return 2

    modulo, _ = COMANDI[comando]
    # Il modulo delegato legge sys.argv con il suo argparse: gli si passa una
    # riga di comando che sembra la sua, cosi' i messaggi di errore e di aiuto
    # nominano `goalmodel <comando>` e non il percorso del modulo.
    sys.argv = [f"goalmodel {comando}", *resto]
    return importlib.import_module(modulo).main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
