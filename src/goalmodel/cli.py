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
import os
import sys

# comando -> (bersaglio, descrizione). Il bersaglio e' un modulo, e allora si
# chiama il suo `main()`, oppure "modulo:funzione" quando in un modulo solo ce
# ne sono due. Serve alla taratura: `gbm` e `dixon-coles` stanno entrambi in
# `evaluation/taratura.py`, perche' tarare e' misurare e la misura non e' il
# mestiere del modello.
#
# L'ordine e' quello del ciclo di lavoro, non alfabetico: e' come si legge
# l'aiuto.
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
    "dixon-coles":   ("goalmodel.evaluation.taratura:main_dixon_coles",
                      "M3: taratura e controlli"),
    "gbm":           ("goalmodel.evaluation.taratura:main_gbm",
                      "M4/M5/M6: taratura, importanza"),
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


# I comandi che possono arrivare a soccerdata, e quindi al resolver di Go.
# `ingest` direttamente; gli altri tre passano da `rounds.py`, che importa
# `ingest` dentro una funzione per aggiornare i dati prima di agire.
COMANDI_DI_RETE = frozenset({"ingest", "predict-round", "close-round", "rounds"})


def _assicura_resolver_go(comando: str, argv: list[str]) -> None:
    """
    Ri-esegue il processo con GODEBUG=netdns=cgo, se serve e se non c'e' gia'.

    PERCHE' UN RE-EXEC E NON UNA RIGA IN `__init__.py`. soccerdata scarica
    Understat con `tls_requests`, che non e' Python: e' un binario Go caricato
    come libreria condivisa, e il runtime Go legge GODEBUG **dall'ambiente del
    processo catturato all'exec**. `os.environ[...]` da dentro Python non lo
    raggiunge mai — verificato: impostarla come primissima istruzione, prima di
    qualunque import, non ha alcun effetto, mentre la stessa variabile messa
    nella shell funziona. L'unico punto in cui si puo' rimediare e' prima che
    l'interprete parta, cioe' ri-eseguendolo.

    COSA RISOLVE. Con la risoluzione "pura Go" quel binario interroga da solo i
    nameserver di /etc/resolv.conf e qui fallisce su OGNI host — `example.com`
    compreso — con `dial tcp: lookup ...: no such host`, mentre `getent`,
    `nslookup` sullo stesso nameserver e `requests` risolvono senza problemi.
    `netdns=cgo` fa passare Go da glibc, quindi da /etc/nsswitch.conf come
    tutto il resto del sistema.

    E' costata due diagnosi sbagliate: sembrava che Understat fosse
    irraggiungibile da questa macchina. Non lo era, e non era nemmeno un
    problema di Understat. `read_schedule()` funzionava solo dalla cache,
    quindi il guasto sembrava pure intermittente.

    Non tocca Windows (il problema e' del resolver di Go su Linux) e non
    sovrascrive una scelta gia' fatta da chi lancia: se GODEBUG contiene gia'
    un `netdns=`, quella vince e non si ri-esegue niente.
    """
    if sys.platform == "win32" or comando not in COMANDI_DI_RETE:
        return
    if "netdns=" in os.environ.get("GODEBUG", ""):
        return

    ambiente = {**os.environ,
                "GODEBUG": ",".join(filter(None, (os.environ.get("GODEBUG"),
                                                  "netdns=cgo")))}
    # Il figlio trova `netdns=` gia' impostato e non ri-esegue a sua volta:
    # la guardia sopra e' anche cio' che impedisce il ciclo infinito.
    #
    # S606 ("processo senza shell") e' proprio la forma che la regola di
    # sicurezza n.4 impone: lista di argomenti, `sys.executable`, nessuna
    # shell e nessuna interpolazione. Gli argomenti arrivano da `sys.argv` e
    # non toccano mai un interprete di comandi.
    os.execve(sys.executable,  # noqa: S606
              [sys.executable, "-m", "goalmodel.cli", comando, *argv],
              ambiente)


def main(argv: list[str] | None = None) -> int:
    # Solo l'invocazione vera da shell puo' ri-eseguirsi: i test chiamano
    # `main([...])` con argv esplicito e non devono veder sparire il processo.
    da_shell = argv is None
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

    if da_shell:
        _assicura_resolver_go(comando, resto)

    bersaglio, _ = COMANDI[comando]
    modulo, _, funzione = bersaglio.partition(":")
    # Il modulo delegato legge sys.argv con il suo argparse: gli si passa una
    # riga di comando che sembra la sua, cosi' i messaggi di errore e di aiuto
    # nominano `goalmodel <comando>` e non il percorso del modulo.
    sys.argv = [f"goalmodel {comando}", *resto]
    entry = getattr(importlib.import_module(modulo), funzione or "main")
    return entry() or 0


if __name__ == "__main__":
    raise SystemExit(main())
