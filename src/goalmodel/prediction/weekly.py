"""
`weekly` non esiste piu': e' stato diviso in due comandi che ragionano per
giornata di campionato invece che per giorno della settimana.

PERCHE'. Un ciclo "del sabato mattina" funziona finche' il calendario e'
regolare, e non lo e' mai: un turno infrasettimanale, un rinvio, una partita
spostata per la coppa, e il giorno della settimana non individua piu' niente.
Soprattutto, un comando solo doveva fare due cose con due precondizioni
opposte — predire vuole le quote e nessun risultato, chiudere vuole tutti i
risultati — quindi una delle due era sempre fuori tempo.

Questo file resta solo per non far finire in un traceback chi ha il vecchio
comando nelle dita. Non fa niente: dice cosa lanciare.
"""

from __future__ import annotations

import sys

MESSAGGIO = """
'python -m src.weekly' e' stato diviso in due comandi, che si lanciano quando
si vuole e quante volte si vuole:

    python -m src.predict_round     da APERTA a PREDETTA
        aggiorna dati e quote, trova da solo la prima giornata con partite
        ancora predicibili, le predice tutte, le registra senza duplicare,
        genera il report e lo apre.

    python -m src.close_round       da GIOCATA a CHIUSA
        aggiorna i risultati, trova la prima giornata predetta e interamente
        giocata, la aggancia, la archivia in track_record/rounds/ con l'errore
        per singola partita e aggiorna il track record cumulativo.

    python -m src.rounds --status   dove sta ogni giornata della stagione

Opzioni comuni: --round N, --dry-run, --skip-ingest.
Dettagli in COMANDI.md.
"""


def main() -> None:
    print(MESSAGGIO)
    # Codice 2: non e' "niente da fare", e' un comando che non esiste piu'.
    # Uscire con zero farebbe passare per riuscita un'automazione che in
    # realta' non ha predetto niente.
    sys.exit(2)


if __name__ == "__main__":
    main()
