"""
Il ciclo settimanale in un comando solo.

COSA FA, NELL'ORDINE
  1. aggiorna i risultati (`matches`, `understat`) - soccerdata riscarica da
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
cambiano non si aggiorna la riga vecchia - falsificherebbe il track record a
posteriori - semmai si registra sotto un `model_version` diverso.

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
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod
from . import report as report_mod
from .report import quando, selezioni

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
    return predict_mod.run(use_next=True, dry_run=dry_run, quiet=True)


def track_record() -> dict | None:
    """
    Passo 6: risultati veri agganciati alle previsioni gia' registrate.

    Non e' fatale: se il registro non esiste ancora, il ciclo e' comunque
    riuscito - semplicemente non c'e' ancora niente da valutare.
    """
    log.info("--- [6] aggancio dei risultati e track record")
    from . import backtest_log as bl

    try:
        preds = bl.load_log()
    except FileNotFoundError:
        log.info("registro non ancora creato: nessun track record da aggiornare")
        return None

    joined = bl.attach_results(preds)
    valide = joined[~joined["post_kickoff"]]
    risolte = valide[valide["FTR"].notna()]
    attesa = valide[valide["FTR"].isna()]

    out = {
        "totali": len(joined),
        "escluse": int(joined["post_kickoff"].sum()),
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

def _titolo(testo: str) -> None:
    print(f"\n{SEP}\n  {testo}\n{SEP}")


def _tabella_previsioni(preds: pd.DataFrame) -> None:
    """
    Una riga per partita, con il pronostico in evidenza.

    Le probabilita' si stampano in percentuale intera e non in decimali: a
    tre cifre dopo la virgola l'occhio non distingue 0.351 da 0.394, che e'
    esattamente la differenza fra un pronostico e l'altro.
    """
    if preds.empty:
        return
    intest = (f"  {'quando':<15} {'partita':<28} "
              f"{'1':>6} {'X':>6} {'2':>6}   {'gol attesi':>11} {'o2.5':>6} {'gg':>5}   quote")
    print(intest)
    print("  " + "-" * (len(intest) - 2))

    for _, r in preds.iterrows():
        p = [r["p_home"], r["p_draw"], r["p_away"]]
        fav = int(np.argmax(p))
        celle = []
        for i, v in enumerate(p):
            # Il favorito e' marcato con '>': niente colori, che su terminali
            # diversi si vedono come sequenze di escape.
            celle.append(f"{'>' if i == fav else ' '}{v:>4.0%}")
        partita = f"{r['home_team']} - {r['away_team']}"
        gol = f"{r['lambda_home']:.2f} - {r['lambda_away']:.2f}"
        quote = (f"{r['odds_home']:.2f}/{r['odds_draw']:.2f}/{r['odds_away']:.2f}"
                 if pd.notna(r["odds_home"]) else "-")
        print(f"  {quando(r.get('kickoff')):<15} {partita:<28} "
              f"{celle[0]:>6} {celle[1]:>6} {celle[2]:>6}   {gol:>11} "
              f"{r['p_over25']:>6.0%} {r['p_btts']:>5.0%}   {quote}")
    print("\n  '>' = esito piu' probabile secondo il mercato de-viggato.")
    print("  Orari in ora italiana. gg = gol-gol.")


def _tabella_selezioni(preds: pd.DataFrame) -> None:
    tab = selezioni(preds)
    if tab.empty:
        return
    _titolo("SELEZIONI PIU' PROBABILI  |  una per partita, poi la classifica")
    intest = f"  {'quando':<15} {'partita':<26} {'mercato':<26} {'prob':>6} {'q.equa':>7}"

    print("  LA PIU' PROBABILE DI OGNI PARTITA")
    print(intest)
    print("  " + "-" * (len(intest) - 2))
    for _, r in tab.sort_values("probabilita", ascending=False).drop_duplicates("partita").iterrows():
        print(f"  {quando(r['kickoff']):<15} {r['partita']:<26} "
              f"{r['mercato']:<26} {r['probabilita']:>6.1%} {r['quota_equa']:>7.2f}")

    print("\n  CLASSIFICA COMPLETA (prime 12)")
    print(intest)
    print("  " + "-" * (len(intest) - 2))
    for _, r in tab.head(12).iterrows():
        print(f"  {quando(r['kickoff']):<15} {r['partita']:<26} "
              f"{r['mercato']:<26} {r['probabilita']:>6.1%} {r['quota_equa']:>7.2f}")

    print("\n  q.equa = quota a cui la puntata varrebbe ZERO. Quella del book")
    print("  sara' sempre piu' bassa: la differenza e' il suo margine, ~5%.")
    print("  Probabilita' alta = varianza bassa, NON vantaggio: sul test set")
    print("  la doppia chance piu' sicura vince l'80.6% e rende -2.9%.")


def _sezione_target(preds: pd.DataFrame) -> None:
    """La squadra seguita in evidenza, con i punteggi esatti."""
    squadra = config.SQUADRA_TARGET
    if preds.empty:
        return
    sel = preds[(preds["home_team"] == squadra) | (preds["away_team"] == squadra)]
    if sel.empty:
        print(f"\n  ({squadra} non gioca in questa giornata)")
        return

    for _, r in sel.iterrows():
        _titolo(f"{squadra.upper()} | {r['home_team']} - {r['away_team']}")
        casa = r["home_team"] == squadra
        print(f"  {quando(r.get('kickoff'))}   ({squadra} in "
              f"{'casa' if casa else 'trasferta'})\n")
        print(f"    vittoria {r['home_team']:<12} {r['p_home']:>6.1%}")
        print(f"    pareggio {'':<12} {r['p_draw']:>6.1%}")
        print(f"    vittoria {r['away_team']:<12} {r['p_away']:>6.1%}")
        print(f"\n    gol attesi   {r['home_team']} {r['lambda_home']:.2f}"
              f"  -  {r['lambda_away']:.2f} {r['away_team']}")
        print(f"    over 2.5     {r['p_over25']:.1%}"
              f"        gol-gol   {r['p_btts']:.1%}")

        top = predict_mod.top_scorelines(r["lambda_home"], r["lambda_away"], n=5)
        print(f"\n    risultati esatti piu' probabili "
              f"(coprono il {sum(t[2] for t in top):.0%}):")
        for i, j, prob in top:
            barra = "#" * max(1, round(prob * 100))
            print(f"      {i}-{j}  {prob:>5.1%}  {barra}")


def _riepilogo(esito: predict_mod.Esito, tr: dict | None, falliti: list[str],
               dry_run: bool, report: Path | None = None) -> None:
    _titolo("RIEPILOGO")

    if falliti:
        print(f"  ! stage di rete falliti, proseguito con i dati presenti:")
        print(f"    {', '.join(falliti)}\n")

    nuove = max(len(esito.preds) - esito.duplicate, 0)
    suffisso = "  (dry-run: NON registrate)" if dry_run else ""
    print(f"  previste e registrate   {nuove:3d}{suffisso}")
    print(f"  gia' in registro        {esito.duplicate:3d}  (non riscritte)")
    print(f"  senza quote             {len(esito.skipped):3d}")

    if not esito.skipped.empty:
        print("\n  Partite non coperte dallo snapshot quote:")
        for _, r in esito.skipped.iterrows():
            partita = f"{r['home_team']} - {r['away_team']}"
            print(f"    {quando(r.get('kickoff')):<15} {partita:<28} "
                  f"manca: {r.get('manca', 'quote')}")
        print("    Normale se il resto del turno si gioca in settimana.")
        print("    Rilancia piu' avanti: le nuove si aggiungono, le vecchie no.")

    print("\n  TRACK RECORD")
    if tr is None:
        print("    registro non ancora creato")
    else:
        print(f"    previsioni valide     {tr['totali'] - tr['escluse']:3d}"
              f"   risolte {tr['risolte']:3d}   in attesa {tr['in_attesa']:3d}")
        if tr["escluse"]:
            print(f"    ESCLUSE               {tr['escluse']:3d}  scritte dopo il "
                  f"calcio d'inizio: non sono previsioni")
        if "rps" in tr:
            delta = tr["rps"] - config.TEST_RPS_REFERENCE
            print(f"    RPS cumulativo        {tr['rps']:.4f}  contro "
                  f"{config.TEST_RPS_REFERENCE:.4f} del backtest ({delta:+.4f})")
            print(f"    accuratezza           {tr['accuratezza']:.1%}")
            if tr["risolte"] < 100:
                print(f"    -> {tr['risolte']} partite sono troppo poche perche' "
                      f"quel numero significhi qualcosa.")
        if not tr["ultime"].empty:
            print("\n    ultimi risultati agganciati:")
            for _, r in tr["ultime"].iterrows():
                partita = f"{r['home_team']} - {r['away_team']}"
                esatto = f"{int(r['FTHG'])}-{int(r['FTAG'])}"
                print(f"      {r['match_date']}  {partita:<28} {esatto:>5}"
                      f"   (1 {r['p_home']:.0%}  X {r['p_draw']:.0%}  2 {r['p_away']:.0%})")

    print(f"\n  registro: {predict_mod.PREDICTIONS_LOG}")
    if report is not None:
        print(f"  report:   {report}   (doppio clic per aprirlo)")
        if esito.matchday is not None:
            stagione = (esito.preds["season"].iloc[0] if not esito.preds.empty
                        else config.CURRENT_SEASON)
            print(f"  archivio: {report_mod.ARCHIVIO}"
                  f"\\giornata_{stagione}_{esito.matchday:02d}.html")
    print(SEP)


# ---------------------------------------------------------------------------

def _silenzia(verbose: bool) -> None:
    """
    Abbassa il rumore dei moduli chiamati.

    Il ciclo esegue sei moduli che parlano molto: il de-vigging da solo stampa
    otto righe di copertura per book. Sono utili quando si lancia quel modulo
    da solo, sono rumore quando si vuole leggere una tabella di previsioni.
    Gli avvisi passano comunque: e' l'informazione che non si deve perdere.
    """
    if verbose:
        return
    for nome in ("normalize", "form", "market", "predict", "evaluate",
                 "ingest", "backtest_log", "baseline"):
        logging.getLogger(nome).setLevel(logging.WARNING)


def run_weekly(dry_run: bool = False, skip_ingest: bool = False,
               verbose: bool = False) -> int:
    """Il ciclo completo. Restituisce il codice di uscita."""
    _silenzia(verbose)
    adesso = pd.Timestamp.now(tz="Europe/Rome")
    _titolo(f"CICLO SETTIMANALE  |  {quando(adesso.tz_convert('UTC'))}  (ora italiana)")

    try:
        falliti = aggiorna_dati(skip_ingest)
        ricostruisci()
        esito = prevedi(dry_run)
    except PassoFallito as exc:
        log.error("%s", exc)
        return 1

    tr = track_record()

    if not esito.preds.empty:
        giornata = esito.matchday if esito.matchday is not None else "?"
        _titolo(f"GIORNATA {giornata}  |  {len(esito.preds)} partite")
        _tabella_previsioni(esito.preds)
        _tabella_selezioni(esito.preds)
        _sezione_target(esito.preds)

    # Il report e' l'ultimo passo e non e' fatale: se salta, le previsioni sono
    # gia' nel registro — che e' l'unica cosa irrecuperabile — e il report si
    # rigenera con 'python -m src.report'. Fermare il ciclo qui non salverebbe
    # niente e nasconderebbe il riepilogo a terminale.
    try:
        report = report_mod.build(
            esito,
            falliti=falliti,
            registro=report_mod.DRY_RUN if dry_run else report_mod.SCRITTO,
        )
    except Exception as exc:
        log.error("report non scritto: %s", exc)
        log.error("le previsioni sono comunque nel registro. "
                  "Riprova con 'python -m src.report'.")
        report = None
    _riepilogo(esito, tr, falliti, dry_run, report)

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
    ap.add_argument("--verbose", action="store_true",
                    help="mostra tutti i log dei moduli chiamati")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    sys.exit(run_weekly(dry_run=args.dry_run, skip_ingest=args.skip_ingest,
                        verbose=args.verbose))


if __name__ == "__main__":
    main()
