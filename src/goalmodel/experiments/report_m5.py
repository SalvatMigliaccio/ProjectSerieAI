"""
Il report di M5 su una giornata: 1X2, doppia chance, under/over e gol/no gol.

DIAGNOSTICA, COME `predici_gbm`. Sul test set M5 e' indistinguibile dal
mercato (+0.00011, IC [-0.00056, +0.00077]) e il registro resta su M1. Questo
file mostra cosa direbbe M5 su ogni mercato, accanto a cio' che dice il
mercato, e — a partite giocate — se ci ha preso. Non e' un track record: le
previsioni di M5 vivono in `experiments/output/`, non nel registro.

I MERCATI SI RICALCOLANO DAI DUE LAMBDA, NON SI SALVANO. E' la stessa regola
della "selezione migliore" (CLAUDE.md): il file della giornata conserva i gol
attesi di M5 e del mercato, e ogni mercato ne discende esatto attraverso la
stessa matrice dei risultati di `models.baseline.all_markets`. Salvare le
probabilita' dei mercati vorrebbe dire congelare una scelta che si vorra'
ritoccare, e avere due copie dello stesso fatto che possono divergere.

PERCHE' NON NEL REPORT DI PRODUZIONE. `reporting/` e' produzione, e la regola
di isolamento dice che un esperimento non la modifica. Da qui si importano il
foglio di stile e l'escaping di `reporting.pagina` — si leggono, non si
cambiano — cosi' i due report si somigliano senza dipendere l'uno dall'altro.

Uso:
    python -m goalmodel.experiments.report_m5                 # ultima giornata salvata
    python -m goalmodel.experiments.report_m5 --matchday 6
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, experiments
from ..models.baseline import all_markets, fair_odds
from ..reporting.pagina import CSS, _esc, quando

log = logging.getLogger("report_m5")

# I mercati mostrati, a gruppi. Ogni gruppo e' una scelta fra alternative che
# si escludono (o, per la doppia chance, le tre coppie possibili): dentro un
# gruppo si evidenzia la piu' probabile secondo M5.
GRUPPI: list[tuple[str, list[str]]] = [
    ("Esito finale", ["1", "X", "2"]),
    ("Doppia chance", ["1X", "X2", "12"]),
    ("Gol / no gol", ["gol-gol", "no gol"]),
    ("Under / over 1.5", ["under 1.5", "over 1.5"]),
    ("Under / over 2.5", ["under 2.5", "over 2.5"]),
    ("Under / over 3.5", ["under 3.5", "over 3.5"]),
]

ETICHETTE = {"gol-gol": "Gol", "no gol": "No gol"}

CSS_M5 = """
.riass td.pick{text-align:left;white-space:nowrap}
.riass .p{color:var(--muted);font-size:12.5px;margin-left:.3rem}
.partita h3{margin:0 0 .2rem}
.partita .meta{color:var(--muted);font-size:13px;margin:0 0 .7rem}
.gruppo td{border-bottom:none;padding:.3rem .55rem}
.gruppo tr.sep td{border-top:1px solid var(--line);padding-top:.55rem}
.gruppo td.g{color:var(--muted);font-size:12px;text-transform:uppercase;
letter-spacing:.05em;white-space:nowrap}
.ok{color:var(--acc);font-weight:700}.ko{color:var(--err)}
.scroll{overflow-x:auto}
"""


def file_giornata(season: str, matchday: int) -> Path:
    return experiments.percorso(f"previsioni_m5_{season}_{matchday:02d}.csv")


def report_path(season: str, matchday: int) -> Path:
    return experiments.percorso(f"report_m5_{season}_{matchday:02d}.html")


def ultima_salvata() -> tuple[str, int] | None:
    """La giornata dell'ultimo file salvato, in ordine di stagione e giornata."""
    files = sorted(experiments.OUTPUT.glob("previsioni_m5_*_*.csv"))
    if not files:
        return None
    _, _, season, giornata = files[-1].stem.split("_")
    return season, int(giornata)


def mercati(tab: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tutti i mercati per M5 e per il mercato, dalle colonne dei lambda salvate.

    Lo stesso `rho` per entrambi: e' quello con cui `predict` costruisce la
    matrice di M1 e di M5, quindi le probabilita' 1X2 qui coincidono con
    quelle salvate nel file. Se non coincidessero, il report mostrerebbe un
    modello diverso da quello che ha predetto.
    """
    m5 = all_markets(tab["lam casa M5"].to_numpy(float),
                     tab["lam fuori M5"].to_numpy(float), rho=config.DC_RHO)
    mk = all_markets(tab["lam casa mkt"].to_numpy(float),
                     tab["lam fuori mkt"].to_numpy(float), rho=config.DC_RHO)
    m5.index = mk.index = tab.index
    return m5, mk


def preso(mercato: str, gh: int, ga: int) -> bool:
    """Se il mercato e' uscito, dato il risultato finale."""
    tot = gh + ga
    if mercato == "1":
        return gh > ga
    if mercato == "X":
        return gh == ga
    if mercato == "2":
        return gh < ga
    if mercato == "1X":
        return gh >= ga
    if mercato == "X2":
        return gh <= ga
    if mercato == "12":
        return gh != ga
    if mercato == "gol-gol":
        return gh > 0 and ga > 0
    if mercato == "no gol":
        return gh == 0 or ga == 0
    verso, linea = mercato.split()
    return tot > float(linea) if verso == "over" else tot < float(linea)


def _quando(ko) -> str:
    """L'orario, o niente: i file salvati prima che esistesse la colonna non lo hanno."""
    return "" if ko is None or pd.isna(ko) else quando(ko)


def _pct(p: float) -> str:
    return f"{p:.1%}"


def _scelta(m5: pd.Series, gruppo: list[str]) -> str:
    """Il mercato piu' probabile del gruppo secondo M5."""
    return max(gruppo, key=lambda m: m5[m])


def _segno(gh, ga, mercato: str) -> str:
    if pd.isna(gh) or pd.isna(ga):
        return ""
    if preso(mercato, int(gh), int(ga)):
        return "<span class='ok' title='uscito'>&#10003;</span>"
    return "<span class='ko' title='non uscito'>&#10007;</span>"


def _html_riassunto(tab: pd.DataFrame, m5: pd.DataFrame) -> str:
    """Una riga per partita: la scelta di M5 sui tre mercati principali."""
    principali = [("1X2", ["1", "X", "2"]), ("Gol / no gol", ["gol-gol", "no gol"]),
                  ("U/O 2.5", ["under 2.5", "over 2.5"])]
    righe = []
    for i, r in tab.iterrows():
        celle = []
        for _, gruppo in principali:
            s = _scelta(m5.loc[i], gruppo)
            celle.append(
                f"<td class='pick'><strong>{_esc(ETICHETTE.get(s, s))}</strong>"
                f"<span class='p'>{_pct(m5.loc[i, s])}</span> "
                f"{_segno(r.get('FTHG'), r.get('FTAG'), s)}</td>")
        risultato = ("" if pd.isna(r.get("FTHG"))
                     else f"{int(r['FTHG'])}-{int(r['FTAG'])}")
        righe.append(
            f"<tr><td class='l q'>{_esc(_quando(r['kickoff_utc']))}</td>"
            f"<td class='l'><strong>{_esc(r['partita'])}</strong></td>"
            f"{''.join(celle)}<td>{_esc(risultato)}</td></tr>")
    return (
        "<div class='card scroll'><table class='riass'><thead><tr>"
        "<th class='l'>quando</th><th class='l'>partita</th>"
        "<th class='l'>1X2</th><th class='l'>gol / no gol</th>"
        "<th class='l'>under / over 2.5</th><th>risultato</th>"
        f"</tr></thead><tbody>{''.join(righe)}</tbody></table></div>")


def _html_partita(r: pd.Series, m5: pd.Series, mk: pd.Series) -> str:
    """Tutti i mercati di una partita: M5, mercato, scarto, quota equa di M5."""
    gh, ga = r.get("FTHG"), r.get("FTAG")
    giocata = not (pd.isna(gh) or pd.isna(ga))
    righe = []
    for nome, gruppo in GRUPPI:
        scelta = _scelta(m5, gruppo)
        for j, m in enumerate(gruppo):
            # Arrotondato prima di scegliere segno e colore: uno scarto di
            # -0.04 punti si stampava "-0.0" in rosso, cioe' un movimento che
            # non c'e'.
            scarto = round((m5[m] - mk[m]) * 100, 1) + 0.0
            classe_scarto = "su" if scarto > 0 else "giu" if scarto < 0 else "q"
            prob = _pct(m5[m])
            prob = f"<span class='fav'>{prob}</span>" if m == scelta else prob
            righe.append(
                f"<tr{' class=sep' if j == 0 else ''}>"
                f"<td class='g'>{_esc(nome) if j == 0 else ''}</td>"
                f"<td class='l'>{_esc(ETICHETTE.get(m, m))}</td>"
                f"<td>{prob}</td><td class='q'>{_pct(mk[m])}</td>"
                f"<td class='{classe_scarto}'>{scarto:+.1f}</td>"
                f"<td>{float(fair_odds(m5[m])):.2f}</td>"
                + (f"<td>{_segno(gh, ga, m)}</td>" if giocata else "")
                + "</tr>")
    meta = (f"{_esc(_quando(r['kickoff_utc']))} &middot; gol attesi M5 "
            f"{r['lam casa M5']:.2f} - {r['lam fuori M5']:.2f} "
            f"<span class='q'>(mercato {r['lam casa mkt']:.2f} - "
            f"{r['lam fuori mkt']:.2f})</span>")
    if giocata:
        meta += f" &middot; <strong>finita {int(gh)}-{int(ga)}</strong>"
    return (
        f"<div class='card partita'><h3>{_esc(r['partita'])}</h3>"
        f"<p class='meta'>{meta}</p><div class='scroll'><table class='gruppo'>"
        "<thead><tr><th class='l'></th><th class='l'>mercato</th><th>M5</th>"
        "<th>mercato</th><th>scarto (pt)</th><th>quota equa M5</th>"
        + ("<th>esito</th>" if giocata else "")
        + f"</tr></thead><tbody>{''.join(righe)}</tbody></table></div></div>")


def _html_bilancio(tab: pd.DataFrame, m5: pd.DataFrame) -> str:
    """A partite giocate: quante scelte di M5 sono uscite, per gruppo."""
    giocate = tab[tab["FTHG"].notna()] if "FTHG" in tab else tab.iloc[0:0]
    if giocate.empty:
        return ("<p class='sub'>Nessuna partita ancora giocata: gli esiti compaiono "
                "rigenerando il report a risultati usciti.</p>")
    righe = []
    for nome, gruppo in GRUPPI:
        presi = sum(preso(_scelta(m5.loc[i], gruppo), int(r["FTHG"]), int(r["FTAG"]))
                    for i, r in giocate.iterrows())
        attese = sum(m5.loc[i, _scelta(m5.loc[i], gruppo)] for i in giocate.index)
        righe.append(f"<tr><td class='l'>{_esc(nome)}</td><td>{presi} su {len(giocate)}</td>"
                     f"<td class='q'>{attese:.1f}</td></tr>")
    return (
        "<div class='card'><table><thead><tr><th class='l'>scelta di M5</th>"
        "<th>uscite</th><th>attese da M5</th></tr></thead>"
        f"<tbody>{''.join(righe)}</tbody></table></div>"
        "<p class='sub'>\"Attese\" e' la somma delle probabilita' che M5 dava alle sue "
        "scelte: se le uscite le stanno vicino, il modello e' calibrato su questa "
        "giornata. Dieci partite non dicono di piu'.</p>")


def componi(tab: pd.DataFrame, season: str, matchday: int) -> str:
    m5, mk = mercati(tab)
    adesso = pd.Timestamp.now(tz="UTC")
    scritte = pd.to_datetime(tab["timestamp_prediction"], utc=True)
    stagione = f"20{season[:2]}-{season[2:]}" if len(season) == 4 else season
    partite = "".join(_html_partita(r, m5.loc[i], mk.loc[i]) for i, r in tab.iterrows())

    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M5 - giornata {matchday} - Serie A {_esc(stagione)}</title>
<style>{CSS}{CSS_M5}</style></head><body><main>
<h1>Giornata {matchday} <span class='q'>&middot; Serie A {_esc(stagione)} &middot; modello LightGBM (M5)</span></h1>
<p class="sub">Report del {_esc(quando(adesso))} (ora italiana) &middot;
previsioni scritte fra {_esc(quando(scritte.min()))} e {_esc(quando(scritte.max()))}</p>
<p><span class='tag w'>diagnostica: non e' il modello di produzione</span>
<span class='tag'>{len(tab)} partite</span></p>

<p class='nota'><strong>M5 e' il GBM ancorato al mercato</strong>: parte dalle quote
di apertura e le corregge con le feature di forma. Sul test set (1140 partite) e'
<strong>indistinguibile dal mercato</strong>, quindi le previsioni ufficiali restano
quelle di M1. La colonna "scarto" dice di quanti punti percentuali M5 si sposta dal
mercato: uno scarto grande e' una partita da capire, non un'occasione. La quota
equa e' quella a cui la puntata varrebbe zero; quella del book sara' piu' bassa.</p>

<h2><span class="n">1</span>Le scelte di M5</h2>
{_html_riassunto(tab, m5)}

<h2><span class="n">2</span>Com'e' andata</h2>
{_html_bilancio(tab, m5)}

<h2><span class="n">3</span>Tutti i mercati, partita per partita</h2>
{partite}

<footer>Generato da <code>goalmodel.experiments.report_m5</code> dal file
<code>{_esc(file_giornata(season, matchday).name)}</code>. Le previsioni di M5 non
entrano nel registro: questo report e' una vista e si rigenera con
<code>python -m goalmodel.experiments.report_m5 --matchday {matchday}</code>.</footer>
</main></body></html>"""


def con_risultati(tab: pd.DataFrame) -> pd.DataFrame:
    """Aggancia i risultati veri sulla quadrupla, dove ci sono."""
    from ..features.dataset import load_dataset

    veri = load_dataset()[["season", "home_team", "away_team", "FTHG", "FTAG"]].copy()
    veri["season"] = veri["season"].astype(str)
    out = tab.merge(veri, on=["season", "home_team", "away_team"], how="left",
                    validate="one_to_one")
    out.index = tab.index
    return out


def scrivi(season: str, matchday: int, risultati: bool = True) -> Path | None:
    """Legge il file della giornata e scrive il report accanto. Restituisce il percorso."""
    src = file_giornata(season, matchday)
    if not src.exists():
        log.warning("nessuna previsione di M5 salvata per %s giornata %d", season, matchday)
        return None
    tab = pd.read_csv(src)
    tab["season"] = tab["season"].astype(str)
    mancano = {"lam casa M5", "lam fuori M5", "lam casa mkt", "lam fuori mkt",
               "timestamp_prediction"} - set(tab.columns)
    if mancano:
        raise ValueError(f"{src.name}: mancano le colonne {sorted(mancano)}")
    if "kickoff_utc" not in tab:
        tab["kickoff_utc"] = pd.NaT
    tab = tab.sort_values("kickoff_utc", kind="stable").reset_index(drop=True)
    if risultati:
        tab = con_risultati(tab)

    # Coerenza con il file: i mercati ricalcolati dai lambda devono ridare
    # l'1X2 salvato. Se non lo fanno, il report mostrerebbe un altro modello.
    m5, _ = mercati(tab)
    scarto = np.abs(m5["1"].to_numpy() - tab["1 M5"].to_numpy(float)).max()
    if scarto > 1e-6:
        raise ValueError(f"1X2 ricalcolato diverso da quello salvato ({scarto:.2e}): "
                         "il rho di predict e quello del report non coincidono")

    dst = report_path(season, matchday)
    dst.write_text(componi(tab, season, matchday), encoding="utf-8")
    return dst


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="Report HTML delle previsioni di M5 su una giornata.")
    ap.add_argument("--matchday", type=int, help="giornata (default: l'ultima salvata)")
    ap.add_argument("--season", default=None, help="stagione, es. 2627 (default: l'ultima salvata)")
    ap.add_argument("--senza-risultati", action="store_true",
                    help="non aggancia i risultati (piu' veloce, non carica il dataset)")
    args = ap.parse_args()

    experiments.proteggi_produzione()
    ultima = ultima_salvata()
    if ultima is None:
        raise SystemExit("nessuna previsione di M5 salvata: lancia prima "
                         "'python -m goalmodel.experiments.predici_gbm'")
    season = args.season or ultima[0]
    matchday = args.matchday or ultima[1]
    dst = scrivi(str(season), matchday, risultati=not args.senza_risultati)
    if dst is not None:
        print(f"scritto {dst}")


if __name__ == "__main__":
    main()
