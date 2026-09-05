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

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("weekly")

SEP = "=" * 72
REPORTS = config.PROCESSED / "reports"


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

GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def _quando(ko) -> str:
    """Data e ora in ora italiana: e' quella che l'utente ha in testa."""
    if pd.isna(ko):
        return "  ?"
    loc = pd.Timestamp(ko).tz_convert("Europe/Rome")
    return f"{GIORNI[loc.weekday()]} {loc:%d/%m} {loc:%H:%M}"


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
        print(f"  {_quando(r.get('kickoff')):<15} {partita:<28} "
              f"{celle[0]:>6} {celle[1]:>6} {celle[2]:>6}   {gol:>11} "
              f"{r['p_over25']:>6.0%} {r['p_btts']:>5.0%}   {quote}")
    print("\n  '>' = esito piu' probabile secondo il mercato de-viggato.")
    print("  Orari in ora italiana. gg = gol-gol.")


def selezioni(preds: pd.DataFrame, minimo: float = 0.65) -> pd.DataFrame:
    """
    Tutti i mercati di tutte le partite, ordinati per probabilita'.

    E' la vista che serve a chi cerca la giocata "sicura": in cima gli esiti
    che il modello ritiene piu' probabili, con la quota equa accanto.

    ATTENZIONE A COSA SIGNIFICA. Probabilita' alta vuol dire varianza bassa,
    non vantaggio. La quota equa e' il prezzo a valore atteso zero, e quella
    del book sara' sempre piu' bassa: le probabilita' di M1 derivano da quelle
    stesse quote con il margine tolto. Misurato sul test set: puntando
    sull'esito piu' probabile si vince il 53.9% delle volte con ROI -1.8%, e
    la doppia chance piu' sicura vince l'80.6% delle volte con ROI -2.9%.
    """
    from .models.baseline import all_markets, fair_odds

    if preds.empty:
        return pd.DataFrame()
    mk = all_markets(preds["lambda_home"].to_numpy(float),
                     preds["lambda_away"].to_numpy(float))
    mk.index = preds.index

    righe = []
    for i, r in preds.iterrows():
        for mercato, p in mk.loc[i].items():
            if p < minimo:
                continue
            righe.append({
                "kickoff": r.get("kickoff"),
                "partita": f"{r['home_team']} - {r['away_team']}",
                "mercato": _etichetta(mercato, r),
                "probabilita": float(p),
                "quota_equa": float(fair_odds(p)),
            })
    tab = pd.DataFrame(righe)
    if tab.empty:
        return tab
    return tab.sort_values("probabilita", ascending=False).reset_index(drop=True)


def _etichetta(mercato: str, r: pd.Series) -> str:
    """Nomi leggibili: '1X' da solo non dice quale squadra."""
    mappa = {
        "1": f"1 ({r['home_team']})",
        "2": f"2 ({r['away_team']})",
        "1X": f"1X ({r['home_team']} o pari)",
        "X2": f"X2 (pari o {r['away_team']})",
        "12": "12 (nessun pareggio)",
        "casa segna": f"{r['home_team']} segna",
        "fuori segna": f"{r['away_team']} segna",
    }
    return mappa.get(mercato, mercato)


def _tabella_selezioni(preds: pd.DataFrame) -> None:
    tab = selezioni(preds)
    if tab.empty:
        return
    _titolo("SELEZIONI PIU' PROBABILI  |  sopra il 65%")
    print(f"  {'quando':<15} {'partita':<26} {'mercato':<26} {'prob':>6} {'q.equa':>7}")
    print("  " + "-" * 84)
    for _, r in tab.head(15).iterrows():
        print(f"  {_quando(r['kickoff']):<15} {r['partita']:<26} "
              f"{r['mercato']:<26} {r['probabilita']:>6.1%} {r['quota_equa']:>7.2f}")
    print("\n  q.equa = quota a cui la puntata varrebbe zero. Quella del book")
    print("  sara' sempre piu' bassa: e' li' che sta il suo margine, ~5%.")
    print("  Probabilita' alta = varianza bassa, NON vantaggio.")


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
        print(f"  {_quando(r.get('kickoff'))}   ({squadra} in "
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


CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e3e3de;--card:#fff;
--acc:#1a6b4a;--accbg:#e8f3ee;--warn:#8a5a1a;--warnbg:#fdf3e3}
@media (prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#e8e8e4;--muted:#9a9a94;
--line:#2e2e33;--card:#1e1e22;--acc:#6cc79b;--accbg:#1b3329;--warn:#d4a055;--warnbg:#332a1b}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem 4rem;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .2rem;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:2.4rem 0 .8rem;text-transform:uppercase;
letter-spacing:.07em;color:var(--muted);font-weight:600}
.sub{color:var(--muted);margin:0 0 .4rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:1rem 1.15rem;margin:.8rem 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:right;font-weight:600;color:var(--muted);font-size:12px;
text-transform:uppercase;letter-spacing:.05em;padding:.5rem .55rem;
border-bottom:1px solid var(--line)}
th:first-child,th.l{text-align:left}
td{padding:.55rem;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums}
td:first-child,td.l{text-align:left}
tr:last-child td{border-bottom:none}
.fav{background:var(--accbg);color:var(--acc);font-weight:700;border-radius:5px}
.q{color:var(--muted);font-size:13px}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:12px;
background:var(--accbg);color:var(--acc);font-weight:600}
.tag.w{background:var(--warnbg);color:var(--warn)}
.bar{height:9px;background:var(--acc);border-radius:3px;display:inline-block;
vertical-align:middle}
.kv{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1.4rem;font-size:14px}
.kv dt{color:var(--muted)}
.kv dd{margin:0;font-variant-numeric:tabular-nums}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
code{font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;
background:var(--bg);padding:.1rem .35rem;border-radius:4px}
"""


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _html_previsioni(preds: pd.DataFrame) -> str:
    if preds.empty:
        return "<p class='sub'>Nessuna partita prevista.</p>"
    righe = []
    for _, r in preds.iterrows():
        p = [r["p_home"], r["p_draw"], r["p_away"]]
        fav = int(np.argmax(p))
        celle = "".join(
            f"<td><span class='{'fav' if i == fav else ''}'"
            f" style='padding:.15rem .4rem'>{v:.0%}</span></td>"
            for i, v in enumerate(p)
        )
        quote = (f"{r['odds_home']:.2f} / {r['odds_draw']:.2f} / {r['odds_away']:.2f}"
                 if pd.notna(r["odds_home"]) else "-")
        righe.append(
            f"<tr><td class='l q'>{_esc(_quando(r.get('kickoff')))}</td>"
            f"<td class='l'><strong>{_esc(r['home_team'])}</strong> - "
            f"{_esc(r['away_team'])}</td>{celle}"
            f"<td>{r['lambda_home']:.2f} - {r['lambda_away']:.2f}</td>"
            f"<td>{r['p_over25']:.0%}</td><td>{r['p_btts']:.0%}</td>"
            f"<td class='q'>{quote}</td></tr>"
        )
    return (
        "<div class='card'><table><thead><tr><th class='l'>quando</th>"
        "<th class='l'>partita</th><th>1</th><th>X</th><th>2</th>"
        "<th>gol attesi</th><th>over 2.5</th><th>gol-gol</th><th>quote B365</th>"
        f"</tr></thead><tbody>{''.join(righe)}</tbody></table></div>"
    )


def _html_selezioni(preds: pd.DataFrame) -> str:
    tab = selezioni(preds)
    if tab.empty:
        return ""
    righe = "".join(
        f"<tr><td class='l q'>{_esc(_quando(r['kickoff']))}</td>"
        f"<td class='l'>{_esc(r['partita'])}</td>"
        f"<td class='l'><strong>{_esc(r['mercato'])}</strong></td>"
        f"<td>{r['probabilita']:.1%}</td>"
        f"<td class='q'>{r['quota_equa']:.2f}</td></tr>"
        for _, r in tab.head(15).iterrows()
    )
    return (
        "<h2>Selezioni piu' probabili</h2><div class='card'>"
        "<table><thead><tr><th class='l'>quando</th><th class='l'>partita</th>"
        "<th class='l'>mercato</th><th>probabilita'</th><th>quota equa</th>"
        f"</tr></thead><tbody>{righe}</tbody></table></div>"
        "<p class='sub'><strong>La quota equa e' il prezzo a valore atteso "
        "zero.</strong> Quella del bookmaker sara' sempre piu' bassa: la "
        "differenza e' il suo margine, circa il 5%. Probabilita' alta "
        "significa varianza bassa, non vantaggio. Misurato sul test set: "
        "puntando sempre sull'esito piu' probabile si vince il 53.9% delle "
        "volte con ROI -1.8%; la doppia chance piu' sicura vince l'80.6% "
        "delle volte con ROI -2.9%.</p>"
    )


def _html_target(preds: pd.DataFrame) -> str:
    squadra = config.SQUADRA_TARGET
    if preds.empty:
        return ""
    sel = preds[(preds["home_team"] == squadra) | (preds["away_team"] == squadra)]
    if sel.empty:
        return f"<h2>{_esc(squadra)}</h2><p class='sub'>Non gioca in questa giornata.</p>"

    blocchi = []
    for _, r in sel.iterrows():
        top = predict_mod.top_scorelines(r["lambda_home"], r["lambda_away"], n=5)
        massimo = max(t[2] for t in top)
        punteggi = "".join(
            f"<tr><td class='l'><strong>{i}-{j}</strong></td>"
            f"<td>{pr:.1%}</td><td class='l' style='width:60%'>"
            f"<span class='bar' style='width:{100 * pr / massimo:.0f}%'></span></td></tr>"
            for i, j, pr in top
        )
        blocchi.append(
            f"<h2>{_esc(squadra)}</h2><div class='card'>"
            f"<p class='sub' style='margin-top:0'>{_esc(_quando(r.get('kickoff')))} "
            f"&middot; {squadra} in "
            f"{'casa' if r['home_team'] == squadra else 'trasferta'}</p>"
            f"<h1 style='font-size:1.2rem;margin:.2rem 0 1rem'>"
            f"{_esc(r['home_team'])} - {_esc(r['away_team'])}</h1>"
            f"<dl class='kv'>"
            f"<dt>vittoria {_esc(r['home_team'])}</dt><dd>{r['p_home']:.1%}</dd>"
            f"<dt>pareggio</dt><dd>{r['p_draw']:.1%}</dd>"
            f"<dt>vittoria {_esc(r['away_team'])}</dt><dd>{r['p_away']:.1%}</dd>"
            f"<dt>gol attesi</dt><dd>{r['lambda_home']:.2f} - {r['lambda_away']:.2f}</dd>"
            f"<dt>over 2.5</dt><dd>{r['p_over25']:.1%}</dd>"
            f"<dt>gol-gol</dt><dd>{r['p_btts']:.1%}</dd></dl>"
            f"<p class='sub' style='margin:1.2rem 0 .3rem'>Risultati esatti piu' "
            f"probabili (coprono il {sum(t[2] for t in top):.0%})</p>"
            f"<table>{punteggi}</table></div>"
        )
    return "".join(blocchi)


def scrivi_report(esito: predict_mod.Esito, tr: dict | None,
                  falliti: list[str], dry_run: bool) -> Path | None:
    """
    Scrive il report in HTML, uno per giornata.

    Un file per giornata e non uno solo sovrascritto: il report e' la lettura
    di quella settimana, e riaprire quello di tre turni fa e' esattamente cio'
    che serve per capire come sono andate le previsioni. `ultimo.html` e' una
    copia a percorso fisso, comoda da tenere aperta nel browser.

    Il report resta una VISTA: il dato e' `predictions_log.csv`. Rigenerarlo
    non cambia nulla, perderlo nemmeno.
    """
    if esito.matchday is None:
        return None
    REPORTS.mkdir(parents=True, exist_ok=True)
    stagione = (esito.preds["season"].iloc[0] if not esito.preds.empty
                else config.CURRENT_SEASON)
    adesso = pd.Timestamp.now(tz="Europe/Rome")

    stato = []
    nuove = max(len(esito.preds) - esito.duplicate, 0)
    stato.append(f"<span class='tag'>{nuove} previste</span>")
    if esito.duplicate:
        stato.append(f"<span class='tag'>{esito.duplicate} gia' in registro</span>")
    if not esito.skipped.empty:
        stato.append(f"<span class='tag w'>{len(esito.skipped)} senza quote</span>")
    if dry_run:
        stato.append("<span class='tag w'>dry-run: non registrate</span>")
    if falliti:
        stato.append(f"<span class='tag w'>{len(falliti)} stage di rete falliti</span>")

    scoperte = ""
    if not esito.skipped.empty:
        voci = "".join(
            f"<tr><td class='l q'>{_esc(_quando(r.get('kickoff')))}</td>"
            f"<td class='l'>{_esc(r['home_team'])} - {_esc(r['away_team'])}</td>"
            f"<td class='l q'>manca {_esc(r.get('manca', 'quote'))}</td></tr>"
            for _, r in esito.skipped.iterrows()
        )
        scoperte = (
            "<h2>Partite senza quote</h2><div class='card'>"
            f"<table>{voci}</table>"
            "<p class='sub' style='margin-bottom:0'>Normale se il resto del turno "
            "si gioca in settimana: lo snapshot copre solo il turno imminente. "
            "Rilanciando piu' avanti si aggiungono, senza toccare le esistenti.</p>"
            "</div>"
        )

    track = "<p class='sub'>Registro non ancora creato.</p>"
    if tr is not None:
        voci = [
            f"<dt>previsioni valide</dt><dd>{tr['totali'] - tr['escluse']}</dd>",
            f"<dt>risolte</dt><dd>{tr['risolte']}</dd>",
            f"<dt>in attesa</dt><dd>{tr['in_attesa']}</dd>",
        ]
        if tr["escluse"]:
            voci.append(f"<dt>escluse (dopo il fischio)</dt><dd>{tr['escluse']}</dd>")
        if "rps" in tr:
            d = tr["rps"] - config.TEST_RPS_REFERENCE
            voci.append(f"<dt>RPS cumulativo</dt><dd>{tr['rps']:.4f} "
                        f"<span class='q'>(backtest {config.TEST_RPS_REFERENCE:.4f}, "
                        f"{d:+.4f})</span></dd>")
            voci.append(f"<dt>accuratezza</dt><dd>{tr['accuratezza']:.1%}</dd>")
        track = f"<div class='card'><dl class='kv'>{''.join(voci)}</dl></div>"
        if "rps" in tr and tr["risolte"] < 100:
            track += (f"<p class='sub'>{tr['risolte']} partite risolte sono troppo "
                      f"poche perche' l'RPS cumulativo significhi qualcosa: serve "
                      f"un centinaio.</p>")

    html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Giornata {esito.matchday} - Serie A {stagione}</title>
<style>{CSS}</style></head><body><main>
<h1>Giornata {esito.matchday} <span class='q'>&middot; Serie A {stagione}</span></h1>
<p class="sub">Previsioni del {_esc(_quando(adesso.tz_convert('UTC')))} (ora italiana)
&middot; modello <code>{_esc(esito.model_version)}</code></p>
<p>{' '.join(stato)}</p>
<h2>Previsioni</h2>
{_html_previsioni(esito.preds)}
{_html_selezioni(esito.preds)}
<p class="sub">Probabilita' de-viggate con il metodo di Shin dalle quote di
apertura B365. <strong>Sono il mercato</strong>, non una previsione indipendente:
il modello converte le quote in probabilita' pulite e ne ricava gol attesi e
punteggi esatti.</p>
{_html_target(esito.preds)}
{scoperte}
<h2>Track record</h2>
{track}
<footer>Generato da <code>python -m src.weekly</code>.
Il dato e' <code>data/processed/predictions_log.csv</code>, append-only:
questo report ne e' solo una vista e si puo' rigenerare.</footer>
</main></body></html>"""

    dst = REPORTS / f"giornata_{stagione}_{esito.matchday:02d}.html"
    dst.write_text(html, encoding="utf-8")
    (REPORTS / "ultimo.html").write_text(html, encoding="utf-8")
    return dst


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
            print(f"    {_quando(r.get('kickoff')):<15} {partita:<28} "
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
        print(f"  report:   {report}")
        print(f"            {REPORTS / 'ultimo.html'}  (percorso fisso)")
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
    _titolo(f"CICLO SETTIMANALE  |  {_quando(adesso.tz_convert('UTC'))}  (ora italiana)")

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

    report = scrivi_report(esito, tr, falliti, dry_run)
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
