"""
Il report della giornata in HTML: un file statico, autonomo, apribile col doppio
clic.

PERCHE' UN FILE E NON UNA DASHBOARD
Un server va tenuto acceso, una dashboard va aggiornata, entrambi smettono di
funzionare quando serve davvero — cioe' fra sei mesi, quando si riapre il
report di una giornata andata male per capire cosa era stato previsto. Un file
HTML con il CSS dentro e i grafici in SVG generato si apre anche fra dieci
anni, senza rete e senza dipendenze. Niente CDN, niente framework, nessuna
immagine esterna.

COSA CONTIENE, NELL'ORDINE
  1. la giornata in arrivo, con il Napoli in evidenza;
  2. cosa e' cambiato rispetto all'ultima previsione che riguardava le stesse
     squadre — un lambda isolato non dice niente, un lambda che si e' mosso si';
  3. dove il modello statistico diverge dal mercato, come DIAGNOSTICA;
  4. il track record delle previsioni vere, con la soglia del backtest;
  5. lo stato del sistema: se qualcosa non ha funzionato, si vede qui.

IL REPORT E' UNA VISTA, NON UN DATO
Si rigenera quando si vuole da `predictions_log.csv`, che invece e' l'unico
file irriproducibile del progetto. Perderlo non costa niente.

Uso:
    goalmodel report
    goalmodel report --open        # genera e apre nel browser
    goalmodel report --no-diverge  # salta la sezione 3 (che addestra M4)
"""

from __future__ import annotations

import argparse
import logging
import webbrowser
from pathlib import Path

import pandas as pd

from .. import config
from ..prediction import predict as predict_mod
from . import pagina, sezioni
from .pagina import DRY_RUN, RIGENERATO, SCRITTO, quando
from .sezioni import Diagnostica, selezioni

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("report")

# Percorso fisso: e' il file che si tiene aperto nel browser e si ricarica.
# Sta accanto al registro perche' e' la sua lettura, ma NON e' versionato —
# si rigenera, e un HTML che cambia ogni settimana in git e' solo rumore.
REPORT = config.TRACK_RECORD / "report.html"

# L'archivio, uno per giornata. Riaprire il report di tre turni fa e'
# esattamente cio' che serve per capire come sono andate le previsioni.
ARCHIVIO = config.PROCESSED / "reports"

# Rimandi per chi importava da qui prima della separazione: `predict_round` e
# `close_round` chiedono `quando` e `selezioni` a questo modulo.
__all__ = ["ARCHIVIO", "DRY_RUN", "REPORT", "RIGENERATO", "SCRITTO",
           "Diagnostica", "build", "main", "quando", "selezioni"]

# ---------------------------------------------------------------------------
# Costruzione
# ---------------------------------------------------------------------------

def build(
    esito: predict_mod.Esito,
    *,
    falliti: list[str] | tuple[str, ...] = (),
    registro: str = SCRITTO,
    diverge: bool = True,
    origine: str = "goalmodel predict-round",
) -> Path:
    """
    Calcola le cinque sezioni, le fa impaginare, scrive i due file.

    Il report esce SEMPRE, anche quando non c'e' niente da prevedere: e'
    proprio il caso in cui si vuole sapere perche', e la sezione 5 lo dice.

    `registro` dice cosa e' successo al registro mentre si costruiva questo
    report, e non e' cosmesi: un report che dichiara "10 previste" quando
    nessuna riga e' stata scritta e' peggio di nessun report, perche' a
    distanza di settimane sembra un track record e non lo e'.
    """
    diag = sezioni.Diagnostica()
    preds = esito.preds

    _, scaricato = predict_mod.load_fixtures_odds()
    if scaricato is not None:
        eta = (pd.Timestamp.now(tz="UTC") - scaricato).total_seconds() / 86400
        if eta > config.FIXTURES_MAX_AGE_DAYS:
            diag.avvisa(f"lo snapshot quote ha {eta:.1f} giorni: copre solo il turno "
                        f"imminente e viene sovrascritto. Rilancia "
                        f"'goalmodel ingest --stage fixtures'.")
    else:
        diag.avvisa("snapshot quote assente: le previsioni, se ci sono, vengono dal "
                    "ripiego manuale o dallo storico")

    if not esito.skipped.empty:
        diag.avvisa(f"{len(esito.skipped)} partite della giornata non hanno quote "
                    f"complete e non sono state predette")

    tab_cambiamenti = sezioni.cambiamenti(preds, esito.model_version, diag)
    tab_divergenza = (sezioni.divergenza(preds, diag) if diverge and not preds.empty
                      else pd.DataFrame())
    if not diverge:
        diag.avvisa("sezione divergenza saltata su richiesta (--no-diverge)")
    tr = sezioni.carica_track_record(diag)

    stagione = (str(preds["season"].iloc[0]) if not preds.empty
                else config.CURRENT_SEASON)

    html = pagina.componi(
        esito=esito, diag=diag,
        cambiamenti=tab_cambiamenti, divergenza=tab_divergenza, track=tr,
        falliti=list(falliti), registro=registro, origine=origine,
        scaricato=scaricato, stagione=stagione,
        giornata=esito.matchday if esito.matchday is not None else "?",
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(html, encoding="utf-8")

    if esito.matchday is not None:
        ARCHIVIO.mkdir(parents=True, exist_ok=True)
        (ARCHIVIO / f"giornata_{stagione}_{esito.matchday:02d}.html").write_text(
            html, encoding="utf-8"
        )
    log.info("report scritto in %s", REPORT)
    return REPORT


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Report settimanale in HTML, statico e autonomo"
    )
    ap.add_argument("--open", action="store_true", dest="apri",
                    help="apre il report nel browser dopo averlo generato")
    ap.add_argument("--no-diverge", action="store_true",
                    help="salta la sezione 3, che addestra M4 (qualche secondo)")
    args = ap.parse_args()

    # Rigenerare il report non deve MAI toccare il registro: le previsioni si
    # scrivono da 'goalmodel predict-round', una volta sola, prima del fischio.
    esito = predict_mod.run(use_next=True, dry_run=True, quiet=True)
    path = build(esito, registro=RIGENERATO, diverge=not args.no_diverge,
                 origine="goalmodel report")
    print(f"\n{path}")
    if args.apri:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
