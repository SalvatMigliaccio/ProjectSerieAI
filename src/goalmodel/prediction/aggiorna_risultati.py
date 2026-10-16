"""
I risultati delle partite appena giocate, la sera stessa — senza toccare il registro.

PERCHE' ESISTE. Fra `predict_round` del venerdi' e `close_round` del martedi'
nessun comando scaricava niente: una partita del sabato restava "risultato in
attesa" in dashboard per tre giorni, anche se Understat il punteggio lo aveva
la sera stessa. Questo comando aggiorna le fonti dei risultati e ricostruisce
il dataset; l'API li mostra subito come PROVVISORI (`status: played`), con la
stessa catena di fonti che `close_round` usera' per archiviarli.

COSA NON FA, ED E' LA PARTE IMPORTANTE.
  - Non scrive nel registro e non chiude giornate. Il track record si scrive
    solo con `predict_round` e `close_round`: questo comando non sa nemmeno
    dove sia `predictions_log.csv`.
  - Non scarica il calendario FBref. E' il passo lento (un browser automatico,
    venti minuti e oltre sui Big 5) e qui non serve: i punteggi arrivano da
    Understat e football-data, gli orari non cambiano fra un giorno e l'altro.
  - Non entra in `logs/last_run.json`: `/api/status` parla di cio' che ha
    toccato il track record, e questo non lo tocca. Stessa regola di M5.

UNDERSTAT PRIMA DI FOOTBALL-DATA. E' la fonte che ha il risultato la sera
stessa; football-data aggiorna un paio di volte a settimana. Se il passo di
football-data fallisse, quello che serve davvero sarebbe gia' scaricato.

I passi di rete non sono fatali, come negli altri comandi; la ricostruzione
si', perche' un dataset a meta' sarebbe peggio di quello di ieri.

Uso:
    goalmodel aggiorna-risultati
    python -m goalmodel.prediction.aggiorna_risultati
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from .. import config
from . import rounds

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("aggiorna_risultati")

STAGE = ("understat", "matches")

# Quanti giorni indietro guardare per il riepilogo a terminale. Copre un turno
# dal venerdi' al lunedi' anche lanciando il comando il martedi' mattina.
GIORNI_RIEPILOGO = 5


def ultime_partite(giorni: int = GIORNI_RIEPILOGO) -> pd.DataFrame:
    """Le partite di produzione degli ultimi giorni, con il risultato se c'e'."""
    from .. import risultati

    sched = rounds.calendario()
    adesso = pd.Timestamp.now(tz="UTC")
    recenti = sched[(sched["kickoff"] <= adesso)
                    & (sched["kickoff"] >= adesso - pd.Timedelta(days=giorni))]
    noti = risultati.risultati()
    noti["season"] = noti["season"].astype(str)
    recenti = recenti.assign(season=recenti["season"].astype(str))
    return recenti.merge(
        noti[[*config.JOIN_KEYS, "FTHG", "FTAG", "fonte_risultato"]],
        on=config.JOIN_KEYS, how="left",
    ).sort_values("kickoff")


def run(skip_ingest: bool = False, verbose: bool = False) -> int:
    rounds.silenzia(verbose)
    rounds.titolo("AGGIORNAMENTO DEI RISULTATI  |  il registro non viene toccato")

    falliti = rounds.aggiorna_dati(STAGE, skip_ingest)
    try:
        rounds.ricostruisci()
    except rounds.PassoFallito as exc:
        log.error("%s", exc)
        rounds.riepilogo_due_righe(
            "niente: la ricostruzione del dataset e' fallita",
            "correggere l'errore sopra; il dataset di prima e' rimasto com'era")
        return 1

    partite = ultime_partite()
    giocate = partite[partite["FTHG"].notna()]
    attesa = partite[partite["FTHG"].isna()]

    if not partite.empty:
        print(f"\n  partite degli ultimi {GIORNI_RIEPILOGO} giorni")
        for r in partite.itertuples(index=False):
            ko = r.kickoff.tz_convert("Europe/Rome").strftime("%a %d/%m %H:%M")
            esito = (f"{int(r.FTHG)}-{int(r.FTAG)}  ({r.fonte_risultato})"
                     if pd.notna(r.FTHG) else "risultato non ancora disponibile")
            print(f"  g{r.matchday:<3} {ko}  {r.home_team:>14} - {r.away_team:<14} {esito}")

    fatto = (f"{len(giocate)} risultati disponibili sulle {len(partite)} partite "
             f"degli ultimi {GIORNI_RIEPILOGO} giorni; in dashboard come provvisori")
    if falliti:
        fatto += f" (non aggiornati: {', '.join(falliti)})"
    sospeso = (f"{len(attesa)} partite senza risultato: rilancia piu' tardi"
               if len(attesa) else
               "niente; l'archivio lo scrive 'goalmodel close-round' a giornata finita")
    rounds.riepilogo_due_righe(fatto, sospeso)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Scarica i risultati appena usciti, senza toccare il registro")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="non scarica: ricostruisce e riepiloga con i dati presenti")
    ap.add_argument("--verbose", action="store_true",
                    help="mostra tutti i log dei moduli chiamati")
    args = ap.parse_args()
    sys.exit(run(skip_ingest=args.skip_ingest, verbose=args.verbose))


if __name__ == "__main__":
    main()
