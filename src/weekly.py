"""
Il ciclo settimanale in un comando solo.

COSA FA, NELL'ORDINE
  1. aggiorna i risultati (`matches`, `understat`) — soccerdata riscarica da
     sola la stagione in corso, quindi non serve dirgli niente;
  2. scarica le quote del turno imminente (`fixtures`);
  3. ricostruisce `matches_master`;
  4. ricalcola le feature di forma e di mercato;
  5. predice la prossima giornata, dedotta da sola, e la registra;
  6. aggancia i risultati veri alle previsioni gia' in registro e aggiorna il
     track record.

QUALI PASSI SONO FATALI E QUALI NO
I passi di rete (1 e 2) non fermano il ciclo: football-data risponde 503 piu'
spesso di quanto dovrebbe, e restare senza previsione perche' un sito era giu'
per dieci minuti sarebbe il modo peggiore di fallire. Si prosegue con i dati
gia' presenti, e la vecchiaia dello snapshot quote viene comunque segnalata da
`predict.py`. I passi locali (3, 4, 5) sono fatali: se il dataset o le feature
non si costruiscono, qualsiasi previsione sarebbe fatta su dati incoerenti.

IDEMPOTENZA
Si puo' rilanciare quante volte si vuole nella stessa settimana. La scrittura
nel registro salta le partite gia' previste dallo stesso modello, e il registro
resta append-only: la prima previsione fatta e' quella che conta. Se le quote
cambiano non si aggiorna la riga vecchia — falsificherebbe il track record a
posteriori — semmai si registra sotto un `model_version` diverso.

QUANDO LANCIARLO
Sabato mattina per il weekend, mercoledi' mattina per gli infrasettimanali:
dopo che football-data ha caricato lo snapshot (venerdi' 17:00 UK e martedi'
13:00 UK) e prima del primo calcio d'inizio.

Uso:
    python -m src.weekly
    python -m src.weekly --dry-run
    python -m src.weekly --skip-ingest
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Callable

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("weekly")

SEP = "=" * 72


class PassoFallito(RuntimeError):
    """Un passo fatale non e' andato a buon fine: il ciclo si ferma qui."""


def _passo(numero: str, nome: str, fn: Callable, fatale: bool) -> bool:
    """Esegue un passo, riferendo con chiarezza cosa e' successo."""
    log.info("--- [%s] %s", numero, nome)
    try:
        fn()
        return True
    except Exception as exc:
        if fatale:
            raise PassoFallito(
                f"passo [{numero}] '{nome}' fallito: {exc}\n"
                f"    Il ciclo si ferma qui: proseguire produrrebbe una "
                f"previsione su dati incoerenti."
            ) from exc
        log.warning("passo [%s] '%s' fallito: %s", numero, nome, exc)
        log.warning("non e' fatale: si prosegue con i dati gia' presenti")
        return False


# ---------------------------------------------------------------------------
# I passi
# ---------------------------------------------------------------------------

def aggiorna_dati(skip_ingest: bool) -> list[str]:
    """Passi 1 e 2. Restituisce l'elenco degli stage falliti."""
    if skip_ingest:
        log.info("--- [1-2] ingestion saltata su richiesta (--skip-ingest)")
        return []

    import ingest

    falliti = []
    for numero, nome, fn in (
        ("1a", "risultati football-data", ingest.ingest_matches),
        ("1b", "xG e PPDA Understat", ingest.ingest_understat),
        ("1c", "calendario FBref", ingest.ingest_schedule),
        ("2", "quote del turno imminente", ingest.ingest_fixtures),
    ):
        if not _passo(numero, nome, fn, fatale=False):
            falliti.append(nome)
    return falliti


def ricostruisci() -> None:
    """Passi 3 e 4, tutti fatali."""
    from .features import form, market
    from . import normalize

    _passo("3", "matches_master", normalize.cmd_build, fatale=True)
    _passo("4a", "feature di forma", form.build, fatale=True)
    _passo("4b", "feature di mercato", market.build, fatale=True)


def prevedi(dry_run: bool) -> predict_mod.Esito:
    """Passo 5: la prossima giornata, dedotta da sola."""
    log.info("--- [5] previsione della prossima giornata")
    return predict_mod.run(use_next=True, dry_run=dry_run)


def track_record() -> dict | None:
    """
    Passo 6: risultati veri agganciati alle previsioni gia' registrate.

    Non e' fatale: se il registro non esiste ancora, il ciclo e' comunque
    riuscito — semplicemente non c'e' ancora niente da valutare.
    """
    log.info("--- [6] aggancio dei risultati e track record")
    from . import backtest_log as bl

    try:
        preds = bl.load_log()
    except FileNotFoundError:
        log.info("registro non ancora creato: nessun track record da aggiornare")
        return None

    joined = bl.attach_results(preds)
    risolte = joined[joined["FTR"].notna()]
    attesa = joined[joined["FTR"].isna()]

    out = {
        "totali": len(joined),
        "risolte": len(risolte),
        "in_attesa": len(attesa),
        "ultime": risolte.sort_values("match_date").tail(5),
    }
    if not risolte.empty:
        p = risolte[bl.PROB_COLS].to_numpy(dtype=float)
        y = bl.outcome_index(risolte["FTR"])
        out["rps"] = float(bl.rps(p, y).mean())
        out["accuratezza"] = float(bl.accuracy(p, y).mean())
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _riepilogo(esito: predict_mod.Esito, tr: dict | None,
               falliti: list[str], dry_run: bool) -> None:
    print(f"\n{SEP}\nRIEPILOGO SETTIMANALE\n{SEP}")

    if falliti:
        print(f"\n!  stage di rete falliti (proseguito con i dati presenti): "
              f"{', '.join(falliti)}")

    giornata = esito.matchday if esito.matchday is not None else "?"
    nuove = len(esito.preds) - esito.duplicate
    print(f"\nGIORNATA {giornata}")
    print(f"  previste ora          {max(nuove, 0):3d}"
          f"{'  (dry-run, non registrate)' if dry_run else ''}")
    print(f"  gia' in registro      {esito.duplicate:3d}  (non riscritte)")
    print(f"  scoperte senza quote  {len(esito.skipped):3d}")

    if not esito.skipped.empty:
        print("\n  partite senza quote — la giornata non e' coperta per intero:")
        for _, r in esito.skipped.iterrows():
            partita = f"{r['home_team']} - {r['away_team']}"
            print(f"    {pd.to_datetime(r['date']).strftime('%d/%m')}  "
                  f"{partita:<32} manca: {r.get('manca', 'quote')}")
        print("    Normale se il resto della giornata si gioca in settimana:")
        print("    lo snapshot copre solo il turno imminente. Rilancia piu' avanti.")

    print("\nTRACK RECORD")
    if tr is None:
        print("  registro non ancora creato")
    else:
        print(f"  previsioni registrate {tr['totali']:3d}"
              f"   risolte {tr['risolte']:3d}   in attesa {tr['in_attesa']:3d}")
        if "rps" in tr:
            delta = tr["rps"] - config.TEST_RPS_REFERENCE
            print(f"  RPS cumulativo        {tr['rps']:.4f}   "
                  f"contro {config.TEST_RPS_REFERENCE:.4f} del backtest "
                  f"({delta:+.4f})")
            print(f"  accuratezza           {tr['accuratezza']:.1%}")
            if tr["risolte"] < 100:
                print(f"  ATTENZIONE: {tr['risolte']} partite sono troppo poche "
                      f"perche' quel numero significhi qualcosa.")
                print("  Con un centinaio di partite l'errore standard sull'RPS")
                print("  e' ancora dell'ordine del divario che si vuole misurare.")
        if not tr["ultime"].empty:
            print("\n  ultimi risultati agganciati:")
            for _, r in tr["ultime"].iterrows():
                partita = f"{r['home_team']} - {r['away_team']}"
                esatto = f"{int(r['FTHG'])}-{int(r['FTAG'])}"
                print(f"    {r['match_date']}  {partita:<32} {esatto:>5}  "
                      f"(1 {r['p_home']:.0%} X {r['p_draw']:.0%} 2 {r['p_away']:.0%})")

    print(f"\n{SEP}")


def _evidenzia_target(esito: predict_mod.Esito) -> None:
    """La squadra seguita resta in evidenza, ma non filtra le previsioni."""
    squadra = config.SQUADRA_TARGET
    if esito.preds.empty:
        return
    sel = esito.preds[
        (esito.preds["home_team"] == squadra) | (esito.preds["away_team"] == squadra)
    ]
    if sel.empty:
        print(f"\n({squadra} non gioca in questa giornata, o la sua partita "
              f"e' fra quelle scoperte)")


# ---------------------------------------------------------------------------

def run_weekly(dry_run: bool = False, skip_ingest: bool = False) -> int:
    """Il ciclo completo. Restituisce il codice di uscita."""
    print(f"{SEP}\nCICLO SETTIMANALE — {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M UTC}\n{SEP}")

    try:
        falliti = aggiorna_dati(skip_ingest)
        ricostruisci()
        esito = prevedi(dry_run)
    except PassoFallito as exc:
        log.error("%s", exc)
        return 1

    tr = track_record()
    _evidenzia_target(esito)
    _riepilogo(esito, tr, falliti, dry_run)

    # Niente da fare NON e' un errore: e' il caso normale del lunedi', o del
    # secondo lancio nella stessa settimana. Uscire con codice diverso da zero
    # farebbe fallire qualsiasi automazione che lo richiami.
    if esito.vuoto:
        print("\nNiente da fare: non ci sono partite future in calendario.")
        print("Se la stagione e' in corso, serve un'ingestion aggiornata.")
    elif esito.preds.empty:
        print("\nNiente da prevedere: nessuna partita della prossima giornata")
        print("ha le quote. Rilancia piu' vicino al turno.")
    elif len(esito.preds) == esito.duplicate:
        print("\nNiente di nuovo: tutte le partite di questa giornata erano")
        print("gia' in registro. Il registro non e' stato toccato.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Ciclo settimanale completo")
    ap.add_argument("--dry-run", action="store_true",
                    help="esegue tutto tranne la scrittura nel registro")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="riusa i dati gia' scaricati, non tocca la rete")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    sys.exit(run_weekly(dry_run=args.dry_run, skip_ingest=args.skip_ingest))


if __name__ == "__main__":
    main()
