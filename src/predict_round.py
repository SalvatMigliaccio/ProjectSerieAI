"""
Fa avanzare una giornata da APERTA a PREDETTA.

COSA FA, NELL'ORDINE
  1. aggiorna risultati, calendario e quote del turno imminente;
  2. ricostruisce `matches_master` e le feature;
  3. individua da solo la prima giornata con partite ancora predicibili;
  4. riaddestra il modello su tutto lo storico e predice TUTTE le sue partite;
  5. scrive nel registro, saltando quelle gia' presenti;
  6. genera il report HTML e lo apre.

NON CHIEDE CHE GIORNATA E NON SA CHE GIORNO E'
La giornata la deduce da `src/rounds.py`, che guarda quote, registro e
risultati. Non compare nessun giorno della settimana: se le quote non ci sono
ancora la giornata risulta FUTURA e il comando lo dice, che e' l'informazione
giusta indipendentemente da quando lo si lancia.

GIORNATA COPERTA A META'
Il file delle quote e' una finestra sul turno imminente: puo' contenerne sei su
dieci perche' le altre si giocano piu' avanti. In quel caso si predicono e si
registrano quelle sei, e si dice esplicitamente quali restano scoperte. Al
lancio successivo si completano le mancanti senza duplicare niente: la
deduplicazione del registro e' su (partita, modello). La giornata resta
"predetta in parte" finche' non sono coperte tutte.

RILANCIARLO NON FA DANNI
E' idempotente. Una partita gia' registrata non viene riscritta nemmeno se le
quote sono cambiate: falsificherebbe il track record a posteriori. Se non c'e'
niente da fare esce con codice 0 e un messaggio esplicito.

Uso:
    python -m src.predict_round
    python -m src.predict_round --round 5
    python -m src.predict_round --dry-run --skip-ingest
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod
from . import report as report_mod
from . import rounds
from .report import quando, selezioni

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("predict_round")

# `schedule` non e' fra gli stage strettamente necessari a prevedere, ma e' la
# fonte della giornata e dell'orario di calcio d'inizio: senza aggiornarlo, un
# rinvio deciso durante la settimana non si vedrebbe e la giornata verrebbe
# dedotta su un calendario vecchio.
STAGE = ("matches", "understat", "schedule", "fixtures")


# ---------------------------------------------------------------------------
# Vista a terminale
# ---------------------------------------------------------------------------

def tabella_previsioni(preds: pd.DataFrame) -> None:
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


def tabella_selezioni(preds: pd.DataFrame) -> None:
    tab = selezioni(preds)
    if tab.empty:
        return
    rounds.titolo("SELEZIONI PIU' PROBABILI  |  una per partita, poi la classifica")
    intest = f"  {'quando':<15} {'partita':<26} {'mercato':<26} {'prob':>6} {'q.equa':>7}"

    print("  LA PIU' PROBABILE DI OGNI PARTITA")
    print(intest)
    print("  " + "-" * (len(intest) - 2))
    migliori = tab.sort_values("probabilita", ascending=False).drop_duplicates("partita")
    for _, r in migliori.iterrows():
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


def sezione_target(preds: pd.DataFrame) -> None:
    """La squadra seguita in evidenza, con i punteggi esatti."""
    squadra = config.SQUADRA_TARGET
    if preds.empty:
        return
    sel = preds[(preds["home_team"] == squadra) | (preds["away_team"] == squadra)]
    if sel.empty:
        print(f"\n  ({squadra} non gioca in questa giornata)")
        return

    for _, r in sel.iterrows():
        rounds.titolo(f"{squadra.upper()} | {r['home_team']} - {r['away_team']}")
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


def stampa_scoperte(esito: predict_mod.Esito) -> None:
    """Quali partite della giornata restano senza previsione, e perche'."""
    if esito.skipped.empty:
        return
    print("\n  PARTITE NON PREDETTE: quote di apertura incomplete")
    for _, r in esito.skipped.iterrows():
        partita = f"{r['home_team']} - {r['away_team']}"
        manca = r.get("manca", "quote")
        print(f"    {quando(r.get('kickoff')):<15} {partita:<28} manca: {manca}")
    print("    Normale se il resto del turno si gioca piu' avanti: lo snapshot")
    print("    copre solo il turno imminente. Rilancia quando le quote usciranno:")
    print("    le nuove si aggiungono, quelle gia' registrate non si toccano.")


# ---------------------------------------------------------------------------

def run(matchday: int | None = None, dry_run: bool = False,
        skip_ingest: bool = False, verbose: bool = False,
        apri: bool = True) -> int:
    """Porta una giornata da aperta a predetta. Restituisce il codice di uscita."""
    rounds.silenzia(verbose)
    adesso = pd.Timestamp.now(tz="UTC")
    rounds.titolo(f"PREDIZIONE DI UNA GIORNATA  |  {quando(adesso)}  (ora italiana)")

    try:
        falliti = rounds.aggiorna_dati(STAGE, skip_ingest)
        rounds.ricostruisci()
    except rounds.PassoFallito as exc:
        log.error("%s", exc)
        return 1

    tab = rounds.stato_giornate()
    if matchday is None:
        scelta = rounds.da_predire(tab)
        if scelta is None:
            print()
            print(rounds.perche_niente_da_fare(tab, "predire"))
            rounds.riepilogo_due_righe(
                "niente: nessuna giornata ha partite predicibili adesso",
                "attendere le quote della prossima giornata",
            )
            return 0
        matchday = int(scelta["matchday"])
    else:
        log.info("giornata forzata da riga di comando: %d", matchday)

    prima = tab[tab["matchday"] == matchday]
    n_partite = int(prima["n_partite"].iloc[0]) if not prima.empty else 0
    gia_in_registro = int(prima["n_predette"].iloc[0]) if not prima.empty else 0

    esito = predict_mod.run(matchday=matchday, dry_run=dry_run, quiet=True)

    if not esito.preds.empty:
        rounds.titolo(f"GIORNATA {matchday}  |  {len(esito.preds)} partite con quote")
        tabella_previsioni(esito.preds)
        tabella_selezioni(esito.preds)
        sezione_target(esito.preds)
    stampa_scoperte(esito)

    # Il report non e' fatale: le previsioni sono gia' nel registro, che e'
    # l'unica cosa irrecuperabile, e il report si rigenera quando si vuole.
    percorso = None
    try:
        percorso = report_mod.build(
            esito, falliti=falliti,
            registro=report_mod.DRY_RUN if dry_run else report_mod.SCRITTO,
            origine="python -m src.predict_round",
        )
    except Exception as exc:
        log.error("report non scritto: %s", exc)
        log.error("le previsioni sono comunque nel registro. "
                  "Rigeneralo con 'python -m src.report'.")

    _riepilogo(esito, matchday, n_partite, gia_in_registro, falliti,
               dry_run, percorso)

    if percorso is not None and apri and not dry_run:
        webbrowser.open(percorso.resolve().as_uri())
    return 0


def _riepilogo(esito: predict_mod.Esito, matchday: int, n_partite: int,
               gia_in_registro: int, falliti: list[str], dry_run: bool,
               percorso) -> None:
    rounds.titolo("RIEPILOGO")
    if falliti:
        print("  ! stage di rete falliti, proseguito con i dati presenti:")
        print(f"    {', '.join(falliti)}\n")

    nuove = max(len(esito.preds) - esito.duplicate, 0)
    print(f"  giornata                {matchday}")
    print(f"  partite in giornata     {n_partite:3d}")
    print(f"  gia' in registro        {gia_in_registro:3d}  (non riscritte)")
    print(f"  previste ora            {nuove:3d}"
          f"{'  (dry-run: NON registrate)' if dry_run else ''}")
    print(f"  senza quote             {len(esito.skipped):3d}")
    if percorso is not None:
        print(f"\n  report:   {percorso}")

    # Quante restano davvero da coprire: le gia' registrate piu' quelle appena
    # scritte, contro il totale della giornata. In dry-run le nuove non
    # contano, perche' nel registro non sono finite.
    coperte = gia_in_registro + (0 if dry_run else nuove)
    resta = max(n_partite - coperte, 0)
    if resta == 0:
        stato = "predetta per intero"
    elif coperte == 0:
        stato = f"ancora da predire (0/{n_partite})"
    else:
        stato = f"predetta in parte ({coperte}/{n_partite})"
    scritte = ("calcolate ma NON registrate (dry-run)" if dry_run
               else "scritte in registro")

    fatto = f"giornata {matchday}: {nuove} previsioni {scritte}, {stato}"
    if resta:
        sospeso = (f"{resta} partite ancora scoperte: rilancia quando le quote "
                   f"usciranno, poi 'python -m src.close_round' a risultati usciti")
    else:
        sospeso = ("niente da predire su questa giornata; a risultati usciti "
                   "chiudi con 'python -m src.close_round'")
    rounds.riepilogo_due_righe(fatto, sospeso)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Predice la prima giornata aperta e non ancora tutta predetta"
    )
    ap.add_argument("--round", type=int, dest="matchday",
                    help="forza una giornata invece di dedurla")
    ap.add_argument("--dry-run", action="store_true",
                    help="fa tutto tranne scrivere nel registro")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="riusa i dati gia' scaricati, non tocca la rete")
    ap.add_argument("--no-open", action="store_true",
                    help="non aprire il report nel browser")
    ap.add_argument("--verbose", action="store_true",
                    help="mostra tutti i log dei moduli chiamati")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    sys.exit(run(matchday=args.matchday, dry_run=args.dry_run,
                 skip_ingest=args.skip_ingest, verbose=args.verbose,
                 apri=not args.no_open))


if __name__ == "__main__":
    main()
