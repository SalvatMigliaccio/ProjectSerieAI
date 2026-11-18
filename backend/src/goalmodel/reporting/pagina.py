"""
L'IMPAGINAZIONE del report: CSS, grafici SVG, tabelle HTML. Nessun calcolo.

PERCHE' E' SEPARATO DAL CONTENUTO (audit A4)
Vedi `sezioni.py`. Qui dentro non si legge un file, non si addestra un
modello, non si stima niente: si ricevono i DataFrame gia' pronti e si
scrivono stringhe.

PERCHE' SVG SCRITTO A MANO E NIENTE CDN
Un server va tenuto acceso e una dashboard va aggiornata; entrambi smettono di
funzionare proprio quando servono, cioe' fra sei mesi, quando si riapre il
report di una giornata andata male. Un file con il CSS dentro e i grafici in
SVG si apre anche fra dieci anni, senza rete.
"""

from __future__ import annotations

import html
import logging

import numpy as np
import pandas as pd

from .. import config
from ..prediction import predict as predict_mod
from .sezioni import Diagnostica, TrackRecord, selezioni

log = logging.getLogger("report")

GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

# Cosa e' successo al registro mentre si costruiva il report. Va dichiarato
# nel report stesso: un file che elenca dieci previsioni senza dire se sono
# state registrate, riletto fra due mesi, sembra un track record e non lo e'.
SCRITTO = "scritto"
DRY_RUN = "dry-run"
RIGENERATO = "rigenerato"
ETICHETTA_REGISTRO = {
    SCRITTO: "aggiornato",
    DRY_RUN: "dry-run: niente scritto",
    RIGENERATO: "non toccato (report rigenerato)",
}


def quando(ko) -> str:
    """Data e ora in ora italiana: e' quella che l'utente ha in testa."""
    if ko is None or pd.isna(ko):
        return "  ?"
    loc = pd.Timestamp(ko)
    loc = loc.tz_localize("UTC") if loc.tz is None else loc
    loc = loc.tz_convert("Europe/Rome")
    return f"{GIORNI[loc.weekday()]} {loc:%d/%m} {loc:%H:%M}"


def _esc(s) -> str:
    """
    Escaping HTML per OGNI valore interpolato, virgolette comprese.

    PERCHE' LA STDLIB E NON TRE `replace` (audit B7). La versione fatta in
    casa copriva `&`, `<` e `>` e non le virgolette, quindi un valore in
    attributo poteva chiudere l'attributo. Oggi nessuna interpolazione sta in
    un attributo e il report e' un file locale aperto col doppio clic: non
    c'era un percorso sfruttabile. Ma una funzione di escaping incompleta
    applicata a macchia di leopardo e' la premessa standard di una XSS il
    giorno in cui il report smette di essere un file locale — e da quando
    esiste `backend/api/` quel giorno e' molto meno ipotetico.
    """
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------------------
# Grafici: SVG scritto a mano
# ---------------------------------------------------------------------------
#
# Niente matplotlib. Un PNG in base64 pesa dieci volte tanto, non si scala con
# la finestra e ha i colori cotti dentro: su un tema scuro resta un rettangolo
# bianco. L'SVG inline eredita i colori dal CSS della pagina e resta nitido a
# qualsiasi zoom.

def _scala(v: float, lo: float, hi: float, a: float, b: float) -> float:
    if hi == lo:
        return (a + b) / 2
    return a + (v - lo) * (b - a) / (hi - lo)


def svg_rps(tr: TrackRecord) -> str:
    """RPS cumulativo nel tempo, con la linea di riferimento del backtest."""
    d = tr.per_partita
    if d.empty:
        return ""
    y = d["rps_cumulativo"].to_numpy(float)
    n = len(y)
    rif = config.TEST_RPS_REFERENCE

    lo = min(float(np.nanmin(y)), rif) - 0.02
    hi = max(float(np.nanmax(y)), rif) + 0.02
    W, H, ML, MR, MT, MB = 760, 260, 52, 16, 16, 34

    def px(i: int) -> float:
        return _scala(i, 0, max(n - 1, 1), ML, W - MR)

    def py(v: float) -> float:
        return _scala(v, lo, hi, H - MB, MT)

    punti = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(y))
    y_rif = py(rif)

    tacche = []
    for v in np.linspace(lo, hi, 5):
        tacche.append(
            f"<line class='griglia' x1='{ML}' y1='{py(v):.1f}' x2='{W - MR}' y2='{py(v):.1f}'/>"
            f"<text class='tick' x='{ML - 8}' y='{py(v) + 4:.1f}' text-anchor='end'>{v:.3f}</text>"
        )
    # Poche etichette sull'asse x: con dieci partite a settimana diventerebbero
    # illeggibili in un mese.
    passo = max(1, n // 6)
    for i in range(0, n, passo):
        data = pd.to_datetime(d["match_date"].iloc[i]).strftime("%d/%m")
        tacche.append(f"<text class='tick' x='{px(i):.1f}' y='{H - MB + 18}' "
                      f"text-anchor='middle'>{data}</text>")

    return f"""<svg viewBox="0 0 {W} {H}" role="img" aria-label="RPS cumulativo">
{''.join(tacche)}
<line class="rif" x1="{ML}" y1="{y_rif:.1f}" x2="{W - MR}" y2="{y_rif:.1f}"/>
<text class="tick rif-testo" x="{W - MR}" y="{y_rif - 6:.1f}" text-anchor="end">backtest {rif:.4f}</text>
<polyline class="serie" points="{punti}"/>
<circle class="punto" cx="{px(n - 1):.1f}" cy="{py(y[-1]):.1f}" r="4"/>
</svg>"""


def svg_calibrazione(tr: TrackRecord) -> str:
    """Atteso contro osservato, con l'intervallo di Wilson di ogni bin."""
    tab = tr.calibrazione
    if tab.empty:
        return ""
    W, H, M = 380, 380, 44

    def px(v: float) -> float:
        return _scala(v, 0, 1, M, W - 12)

    def py(v: float) -> float:
        return _scala(v, 0, 1, H - M, 12)

    parti = [f"<line class='diagonale' x1='{px(0):.1f}' y1='{py(0):.1f}' "
             f"x2='{px(1):.1f}' y2='{py(1):.1f}'/>"]
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        parti.append(f"<text class='tick' x='{px(v):.1f}' y='{H - M + 18:.1f}' "
                     f"text-anchor='middle'>{v:.2f}</text>")
        parti.append(f"<text class='tick' x='{M - 8}' y='{py(v) + 4:.1f}' "
                     f"text-anchor='end'>{v:.2f}</text>")
    for _, r in tab.iterrows():
        x = px(float(r["atteso"]))
        parti.append(
            f"<line class='barra' x1='{x:.1f}' y1='{py(float(r['ic_basso'])):.1f}' "
            f"x2='{x:.1f}' y2='{py(float(r['ic_alto'])):.1f}'/>"
        )
        classe = "punto fuori" if bool(r["significativo"]) else "punto"
        parti.append(f"<circle class='{classe}' cx='{x:.1f}' "
                     f"cy='{py(float(r['osservato'])):.1f}' r='4'/>")
    return (f"<svg viewBox=\"0 0 {W} {H}\" role=\"img\" "
            f"aria-label=\"curva di calibrazione\">{''.join(parti)}</svg>")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e3e3de;--card:#fff;
--acc:#1a6b4a;--accbg:#e8f3ee;--warn:#8a5a1a;--warnbg:#fdf3e3;--err:#8a2a2a}
@media (prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#e8e8e4;--muted:#9a9a94;
--line:#2e2e33;--card:#1e1e22;--acc:#6cc79b;--accbg:#1b3329;--warn:#d4a055;
--warnbg:#332a1b;--err:#e08a8a}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem 4rem;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .2rem;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:2.6rem 0 .8rem;text-transform:uppercase;
letter-spacing:.07em;color:var(--muted);font-weight:600}
h2 .n{color:var(--acc);margin-right:.4rem}
h3{font-size:.95rem;margin:1.6rem 0 .5rem;font-weight:600}
.sub{color:var(--muted);margin:0 0 .4rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:1rem 1.15rem;margin:.8rem 0}
.card.warn{border-color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:right;font-weight:600;color:var(--muted);font-size:12px;
text-transform:uppercase;letter-spacing:.05em;padding:.5rem .55rem;
border-bottom:1px solid var(--line)}
th:first-child,th.l{text-align:left}
td{padding:.55rem;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums}
td:first-child,td.l{text-align:left}
tr:last-child td{border-bottom:none}
.fav{background:var(--accbg);color:var(--acc);font-weight:700;border-radius:5px;
padding:.15rem .4rem}
.q{color:var(--muted);font-size:13px}
.su{color:var(--acc)}.giu{color:var(--err)}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:12px;
background:var(--accbg);color:var(--acc);font-weight:600;margin-right:.3rem}
.tag.w{background:var(--warnbg);color:var(--warn)}
.bar{height:9px;background:var(--acc);border-radius:3px;display:inline-block;
vertical-align:middle}
.kv{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1.4rem;font-size:14px;
margin:0}
.kv dt{color:var(--muted)}
.kv dd{margin:0;font-variant-numeric:tabular-nums}
.nota{border-left:3px solid var(--line);padding:.1rem 0 .1rem .9rem;
color:var(--muted);font-size:13.5px;margin:.9rem 0}
.nota strong{color:var(--fg)}
.due{display:grid;grid-template-columns:1fr;gap:1rem}
@media(min-width:820px){.due{grid-template-columns:1.6fr 1fr}}
svg{width:100%;height:auto;display:block}
.griglia{stroke:var(--line);stroke-width:1}
.tick{fill:var(--muted);font-size:11px}
.serie{fill:none;stroke:var(--acc);stroke-width:2.2;stroke-linejoin:round}
.rif{stroke:var(--muted);stroke-width:1.4;stroke-dasharray:5 4}
.rif-testo{fill:var(--muted)}
.diagonale{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4}
.barra{stroke:var(--line);stroke-width:6;stroke-linecap:round}
.punto{fill:var(--acc)}
.punto.fuori{fill:var(--err)}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
code{font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;
background:var(--bg);padding:.1rem .35rem;border-radius:4px}
"""


# ---------------------------------------------------------------------------
# Impaginazione
# ---------------------------------------------------------------------------

def _html_giornata(preds: pd.DataFrame) -> str:
    if preds.empty:
        return "<p class='sub'>Nessuna partita prevista.</p>"
    righe = []
    for _, r in preds.iterrows():
        p = [r["p_home"], r["p_draw"], r["p_away"]]
        fav = int(np.argmax(p))
        celle = "".join(
            f"<td>{'<span class=fav>' if i == fav else ''}{v:.0%}"
            f"{'</span>' if i == fav else ''}</td>"
            for i, v in enumerate(p)
        )
        quote = (f"{r['odds_home']:.2f} / {r['odds_draw']:.2f} / {r['odds_away']:.2f}"
                 if pd.notna(r["odds_home"]) else "-")
        righe.append(
            f"<tr><td class='l q'>{_esc(quando(r.get('kickoff')))}</td>"
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
        "<p class='nota'>Probabilita' de-viggate con il metodo di Shin dalle quote "
        "di apertura B365. <strong>Sono il mercato</strong>, non una previsione "
        "indipendente: il modello converte le quote in probabilita' pulite e ne "
        "ricava gol attesi, over/under e punteggi esatti.</p>"
    )


def _html_target(preds: pd.DataFrame) -> str:
    squadra = config.SQUADRA_TARGET
    if preds.empty:
        return ""
    sel = preds[(preds["home_team"] == squadra) | (preds["away_team"] == squadra)]
    if sel.empty:
        return (f"<h3>{_esc(squadra)}</h3>"
                f"<p class='sub'>Non gioca in questa giornata.</p>")

    blocchi = []
    for _, r in sel.iterrows():
        top = predict_mod.top_scorelines(r["lambda_home"], r["lambda_away"], n=5)
        massimo = max(t[2] for t in top)
        punteggi = "".join(
            f"<tr><td class='l'><strong>{i}-{j}</strong></td>"
            f"<td>{pr:.1%}</td><td class='l' style='width:65%'>"
            f"<span class='bar' style='width:{100 * pr / massimo:.0f}%'></span></td></tr>"
            for i, j, pr in top
        )
        blocchi.append(
            f"<h3>{_esc(squadra)} &middot; {_esc(r['home_team'])} - "
            f"{_esc(r['away_team'])}</h3><div class='card'><div class='due'>"
            f"<div><dl class='kv'>"
            f"<dt>quando</dt><dd>{_esc(quando(r.get('kickoff')))}</dd>"
            f"<dt>dove</dt><dd>{_esc(squadra)} in "
            f"{'casa' if r['home_team'] == squadra else 'trasferta'}</dd>"
            f"<dt>vittoria {_esc(r['home_team'])}</dt><dd>{r['p_home']:.1%}</dd>"
            f"<dt>pareggio</dt><dd>{r['p_draw']:.1%}</dd>"
            f"<dt>vittoria {_esc(r['away_team'])}</dt><dd>{r['p_away']:.1%}</dd>"
            f"<dt>gol attesi</dt><dd>{r['lambda_home']:.2f} - {r['lambda_away']:.2f}</dd>"
            f"<dt>over 2.5</dt><dd>{r['p_over25']:.1%}</dd>"
            f"<dt>gol-gol</dt><dd>{r['p_btts']:.1%}</dd></dl></div>"
            f"<div><p class='sub' style='margin-top:0'>Primi cinque punteggi esatti "
            f"(coprono il {sum(t[2] for t in top):.0%})</p>"
            f"<table>{punteggi}</table></div></div></div>"
        )
    return "".join(blocchi)


def _html_una_per_partita(preds: pd.DataFrame) -> str:
    """
    Una selezione sola per partita: la piu' probabile fra tutti i mercati.

    PERCHE' SEPARATA DALLA CLASSIFICA. La classifica generale ordina tutti i
    mercati di tutte le partite insieme: una partita squilibrata ci compare
    tre volte e una equilibrata non ci compare affatto. Serve a vedere dove
    stanno le probabilita' piu' alte del turno, non a dire cosa fare partita
    per partita — che e' la domanda diversa a cui risponde questa tabella.

    LA SOGLIA QUI NON SI APPLICA. `selezioni` scarta sotto il 65% perche' la
    classifica non si riempia di roba mediocre, ma qui scartare significherebbe
    lasciare una partita SENZA riga, cioe' non rispondere proprio dove la
    risposta e' meno ovvia. Si prende il massimo disponibile, qualunque sia, e
    la colonna della probabilita' dice da sola quanto e' poco.
    """
    if preds.empty:
        return ""
    tab = selezioni(preds, minimo=0.0)
    if tab.empty:
        return ""
    scelte = (tab.sort_values("probabilita", ascending=False)
                 .drop_duplicates("partita"))

    righe = "".join(
        f"<tr><td class='l q'>{_esc(quando(r['kickoff']))}</td>"
        f"<td class='l'>{_esc(r['partita'])}</td>"
        f"<td class='l'><strong>{_esc(r['mercato'])}</strong></td>"
        f"<td>{r['probabilita']:.1%}</td>"
        f"<td class='q'>{r['quota_equa']:.2f}</td></tr>"
        for _, r in scelte.iterrows()
    )
    return (
        "<h3>Una selezione per partita</h3><div class='card'>"
        "<table><thead><tr><th class='l'>quando</th><th class='l'>partita</th>"
        "<th class='l'>mercato piu' probabile</th><th>probabilita'</th>"
        f"<th>quota equa</th></tr></thead><tbody>{righe}</tbody></table></div>"
        "<p class='nota'>Il mercato piu' probabile di ogni partita, fra 1X2, "
        "doppia chance, over/under, gol-gol e squadra che segna. "
        "<strong>Non e' un consiglio di giocata</strong>: e' la stessa "
        "distribuzione della tabella qui sopra, letta una partita alla volta. "
        "Tutte le righe hanno valore atteso negativo contro le quote del book, "
        "e le probabilita' basse in fondo alla colonna dicono quali partite "
        "il modello non sa come leggere.</p>"
    )


def _html_con_quota(preds: pd.DataFrame,
                    minima: float = config.QUOTA_MINIMA_SELEZIONE) -> str:
    """
    Una selezione per partita fra quelle che pagano almeno `minima`.

    LA DOMANDA A CUI RISPONDE. La tabella precedente da' il mercato piu'
    probabile, e quasi sempre e' una quasi-certezza che paga 1.07: probabile
    quanto si vuole, ma non la si gioca. Qui si scarta tutto cio' che sta
    sotto la quota minima e si prende, fra quel che resta, la probabilita'
    piu' alta — cioe' il miglior compromesso fra quanto e' probabile e quanto
    paga.

    QUELLO CHE QUESTA SEZIONE NON FA, E VA DETTO OGNI VOLTA. Alzare la quota
    **non migliora il valore atteso**. Il margine del book e' identico su
    tutti i mercati derivati dalle stesse quote — 5.19% misurato — quindi
    filtrare per quota sposta la varianza e la vincita potenziale, non il
    vantaggio, che resta negativo dappertutto. E' misurato, non dedotto: sul
    test set nessuna soglia di probabilita' produce profitto, e la doppia
    chance piu' sicura vince l'80.6% delle volte rendendo -2.9%, con
    intervallo che esclude lo zero.

    Il filtro e' sulla quota EQUA, l'unica che esiste per tutti i mercati.

    LE DUE COLONNE NON SI CONFRONTANO COME MARGINE. Verrebbe da leggere lo
    scarto fra quota equa e quota B365 come il margine del book, e sarebbe
    sbagliato: la quota equa non e' la quota de-viggata, e' `1/p` con `p`
    ricostruita dalla matrice Poisson. M1 parte dalle quote, le de-vigga, ne
    ricava due lambda incrociando supremazia e totale, e da quei lambda
    rifa' l'1X2 — e un Poisson indipendente non riproduce esattamente una
    terzina qualsiasi. Lo scarto e' quindi margine PIU' errore di
    ricostruzione, e i due possono avere segno opposto: su Atalanta-Cagliari
    l'equa vale 1.63 contro 1.57 del book, cioe' il modello crede alla
    squadra di casa MENO della quota lorda di margine. Non e' un'occasione al
    contrario, e' la ricostruzione.
    """
    if preds.empty:
        return ""
    tab = selezioni(preds, minimo=0.0)
    if tab.empty:
        return ""
    tab = tab[tab["quota_equa"] >= minima]
    if tab.empty:
        return (f"<h3>Selezioni con quota almeno {minima:.2f}</h3>"
                f"<p class='sub'>Nessun mercato di questa giornata paga "
                f"{minima:.2f} o piu'.</p>")

    scelte = (tab.sort_values("probabilita", ascending=False)
                 .drop_duplicates("partita"))

    righe = []
    for _, r in scelte.iterrows():
        book = (f"{float(r['quota_book']):.2f}" if pd.notna(r["quota_book"])
                else "<span class='q'>-</span>")
        righe.append(
            f"<tr><td class='l q'>{_esc(quando(r['kickoff']))}</td>"
            f"<td class='l'>{_esc(r['partita'])}</td>"
            f"<td class='l'><strong>{_esc(r['mercato'])}</strong></td>"
            f"<td>{r['probabilita']:.1%}</td>"
            f"<td class='q'>{r['quota_equa']:.2f}</td>"
            f"<td>{_esc(book)}</td></tr>"
        )
    return (
        f"<h3>Selezioni con quota almeno {minima:.2f}</h3><div class='card'>"
        "<table><thead><tr><th class='l'>quando</th><th class='l'>partita</th>"
        "<th class='l'>mercato</th><th>probabilita'</th><th>quota equa</th>"
        f"<th>quota B365</th></tr></thead><tbody>{''.join(righe)}</tbody>"
        "</table></div>"
        f"<p class='nota'><strong>Alzare la quota non migliora il valore "
        f"atteso.</strong> Il margine del bookmaker e' identico su tutti i "
        f"mercati derivati dalle stesse quote — 5.19% misurato — quindi questo "
        f"filtro sposta la vincita potenziale e la varianza, non il vantaggio: "
        f"resta negativo su ogni riga. Sul test set nessuna soglia produce "
        f"profitto, e la doppia chance piu' sicura vince l'80.6% delle volte "
        f"rendendo <strong>-2.9%</strong>, con intervallo che esclude lo zero.</p>"
        f"<p class='nota'><strong>Le due colonne di quota non si confrontano "
        f"come margine.</strong> La quota equa e' 1/p con p ricostruita dalla "
        f"matrice Poisson, non la quota de-viggata: il modello parte dalle "
        f"quote, ne ricava i gol attesi incrociando supremazia e totale, e da "
        f"li' rifa' l'1X2 — e un Poisson indipendente non riproduce esattamente "
        f"una terzina qualsiasi. Lo scarto fra le colonne e' quindi margine "
        f"<em>piu'</em> errore di ricostruzione, e i due possono avere segno "
        f"opposto: dove l'equa e' piu' alta della quota B365, il modello crede "
        f"a quell'esito meno di quanto faccia il book gia' al lordo del "
        f"margine. Non e' un'occasione al contrario, e' la ricostruzione. "
        f"La colonna B365 resta vuota dove il registro non ha la quota del "
        f"book: si quota solo l'1X2, e stimare le altre con un margine medio "
        f"significherebbe inventare un numero con l'aria di essere misurato.</p>"
    )


def _html_selezioni(preds: pd.DataFrame) -> str:
    tab = selezioni(preds)
    if tab.empty:
        return ""
    righe = "".join(
        f"<tr><td class='l q'>{_esc(quando(r['kickoff']))}</td>"
        f"<td class='l'>{_esc(r['partita'])}</td>"
        f"<td class='l'><strong>{_esc(r['mercato'])}</strong></td>"
        f"<td>{r['probabilita']:.1%}</td>"
        f"<td class='q'>{r['quota_equa']:.2f}</td></tr>"
        for _, r in tab.head(12).iterrows()
    )
    return (
        "<h3>Selezioni piu' probabili</h3><div class='card'>"
        "<table><thead><tr><th class='l'>quando</th><th class='l'>partita</th>"
        "<th class='l'>mercato</th><th>probabilita'</th><th>quota equa</th>"
        f"</tr></thead><tbody>{righe}</tbody></table></div>"
        "<p class='nota'><strong>La quota equa e' il prezzo a valore atteso "
        "zero.</strong> Quella del bookmaker sara' sempre piu' bassa: la "
        "differenza e' il suo margine, circa il 5%. Probabilita' alta significa "
        "varianza bassa, non vantaggio. Misurato sul test set: puntando sempre "
        "sull'esito piu' probabile si vince il 53.9% delle volte con ROI -1.8%; "
        "la doppia chance piu' sicura vince l'80.6% delle volte con ROI -2.9%.</p>"
    )


def _html_cambiamenti(tab: pd.DataFrame) -> str:
    if tab.empty:
        return ("<p class='sub'>Nessuna previsione precedente da confrontare: "
                "il confronto compare dalla seconda giornata registrata in poi.</p>")

    note = tab[tab["prima"].notna()]
    if note.empty:
        return ("<p class='sub'>Nessuna delle squadre di questa giornata era gia' "
                "stata prevista: e' la loro prima comparsa nel registro.</p>")

    righe = []
    for _, r in note.iterrows():
        d = float(r["delta"])
        classe = "su" if d > 0 else "giu"
        segno = f"{d:+.2f}"
        righe.append(
            f"<tr><td class='l'><strong>{_esc(r['squadra'])}</strong></td>"
            f"<td class='l q'>{_esc(r['prima_data'].strftime('%d/%m'))} "
            f"{'in casa con' if r['prima_dove'] == 'casa' else 'in trasferta con'} "
            f"{_esc(r['prima_avversario'])}</td>"
            f"<td>{r['prima']:.2f}</td>"
            f"<td class='l q'>{'in casa con' if r['dove'] == 'casa' else 'in trasferta con'} "
            f"{_esc(r['avversario'])}</td>"
            f"<td>{r['lambda']:.2f}</td>"
            f"<td class='{classe}'>{segno}</td></tr>"
        )
    mancano = int(tab["prima"].isna().sum())
    coda = (f"<p class='sub'>{mancano} squadre non hanno una previsione "
            f"precedente in registro.</p>" if mancano else "")
    return (
        "<div class='card'><table><thead><tr><th class='l'>squadra</th>"
        "<th class='l'>previsione precedente</th><th>gol attesi</th>"
        "<th class='l'>questa giornata</th><th>gol attesi</th><th>&Delta;</th>"
        f"</tr></thead><tbody>{''.join(righe)}</tbody></table></div>{coda}"
        "<p class='nota'>Fra due previsioni cambiano insieme la squadra, "
        "l'avversario e il campo: <strong>lo scarto non e' una misura di forma "
        "e non e' un errore</strong>. Serve a rendere leggibile un numero che da "
        "solo non lo e' — 1.12 non dice niente, 1.45 &rarr; 1.12 passando dalla "
        "casa alla trasferta si'. Confronto a parita' di modello.</p>"
    )


def _html_divergenza(tab: pd.DataFrame) -> str:
    intro = (
        "<p class='nota'><strong>Questa sezione e' diagnostica, non un segnale di "
        "scommessa.</strong> Il test set ha stabilito che nessun modello statistico "
        "batte il mercato: M4 senza quote copre l'84.6% della distanza fra il "
        "pavimento e le quote, ma la sua informazione e' la <em>stessa</em> che le "
        "quote gia' contengono, prezzata meglio. Uno scarto grande e' quindi "
        "un'anomalia da capire — di solito una squadra le cui medie mobili non "
        "hanno ancora registrato qualcosa che il mercato conosce gia': un "
        "infortunio, un cambio in panchina, un mercato di gennaio — "
        "<strong>non un'occasione</strong>. Chi la giocasse starebbe puntando "
        "contro il modello meglio calibrato dei due.</p>"
    )
    if tab.empty:
        return intro + ("<p class='sub'>Confronto non disponibile: vedi lo stato "
                        "del sistema in fondo.</p>")

    righe = []
    for _, r in tab.head(3).iterrows():
        dettaglio = " &middot; ".join(
            f"{et} {r[f'mkt_{c}']:.0%}&rarr;{r[f'gbm_{c}']:.0%}"
            for et, c in (("1", "home"), ("X", "draw"), ("2", "away"))
        )
        righe.append(
            f"<tr><td class='l q'>{_esc(quando(r['kickoff']))}</td>"
            f"<td class='l'><strong>{_esc(r['partita'])}</strong></td>"
            f"<td class='l q'>{dettaglio}</td>"
            f"<td>{r['scarto']:.1%}</td></tr>"
        )
    resto = ""
    if len(tab) > 3:
        resto = (f"<p class='sub'>Scarto mediano sulle {len(tab)} partite della "
                 f"giornata: {tab['scarto'].median():.1%}.</p>")
    return (
        intro
        + "<div class='card'><table><thead><tr><th class='l'>quando</th>"
        "<th class='l'>partita</th><th class='l'>mercato &rarr; M4 senza quote</th>"
        f"<th>scarto</th></tr></thead><tbody>{''.join(righe)}</tbody></table></div>"
        + resto
        + "<p class='sub'>Lo scarto e' la variazione totale fra le due "
        "distribuzioni: meta' della somma degli scarti assoluti sui tre esiti, "
        "cioe' quanta probabilita' andrebbe spostata per passare dall'una "
        "all'altra.</p>"
    )


def _html_track(tr: TrackRecord | None) -> str:
    if tr is None:
        return "<p class='sub'>Registro non ancora creato.</p>"

    voci = [
        f"<dt>previsioni valide</dt><dd>{tr.totali - tr.escluse}</dd>",
        f"<dt>risolte</dt><dd>{tr.risolte}</dd>",
        f"<dt>in attesa di risultato</dt><dd>{tr.in_attesa}</dd>",
    ]
    if tr.escluse:
        voci.append(f"<dt>escluse (scritte dopo il fischio)</dt><dd>{tr.escluse}</dd>")
    if tr.rps is not None:
        d = tr.rps - config.TEST_RPS_REFERENCE
        voci.append(
            f"<dt>RPS cumulativo</dt><dd>{tr.rps:.4f} <span class='q'>"
            f"(backtest {config.TEST_RPS_REFERENCE:.4f}, {d:+.4f})</span></dd>"
        )
        voci.append(f"<dt>accuratezza</dt><dd>{tr.accuratezza:.1%}</dd>")
    if tr.rps_mercato is not None:
        voci.append(f"<dt>RPS delle quote registrate</dt><dd>{tr.rps_mercato:.4f}"
                    f" <span class='q'>(de-vig proporzionale)</span></dd>")
    blocchi = [f"<div class='card'><dl class='kv'>{''.join(voci)}</dl></div>"]

    if tr.fabbisogno:
        f = tr.fabbisogno
        if f.get("metodo") == "non stimabile":
            blocchi.append("<p class='sub'>Non riesco a stimare quante previsioni "
                           "servono: manca il walk-forward. Lancia "
                           "<code>goalmodel evaluate</code>.</p>")
        else:
            dove_siamo = (
                f"Oggi il track record vale {f['n_partite']} partite in "
                f"{f['n_cluster']} giornate risolte"
                + (f", e l'intervallo al 95% sull'RPS medio e' largo "
                   f"&plusmn;{f['semiampiezza']:.4f}."
                   if f["semiampiezza"] is not None
                   else ": troppo poche perche' l'intervallo si possa misurare qui.")
            )
            blocchi.append(
                f"<p class='nota'><strong>Quante ne servono ancora.</strong> "
                f"{dove_siamo} Per distinguere uno scarto di {f['tolleranza']:.3f} "
                f"dal backtest — la soglia sotto la quale una divergenza non "
                f"cambierebbe comunque nessuna decisione — servono circa "
                f"<strong>{f['cluster_necessari']} giornate</strong> "
                f"({f['partite_necessarie']} partite): ne mancano "
                f"<strong>{f['mancanti']}</strong>, cioe' "
                f"{f['mancanti'] / 38:.1f} stagioni di Serie A. La semiampiezza "
                f"scala come 1/&radic;giornate, e si ricampionano le giornate e non "
                f"le partite perche' le dieci partite di un turno sono predette "
                f"dallo stesso addestramento. Stima da {f['metodo']}.</p>"
            )

    grafico = svg_rps(tr)
    if grafico:
        blocchi.append(
            f"<h3>RPS cumulativo delle previsioni registrate</h3>"
            f"<div class='card'>{grafico}"
            f"<p class='sub' style='margin-bottom:0'>Media progressiva, una "
            f"partita alla volta, in ordine di data. La tratteggiata e' lo "
            f"{config.TEST_RPS_REFERENCE:.4f} del backtest. Piu' basso e' meglio. "
            f"I primi punti oscillano moltissimo: e' aritmetica della media, non "
            f"il modello che cambia idea.</p></div>"
        )
    else:
        blocchi.append("<p class='sub'>Nessuna previsione ancora risolta: il "
                       "grafico compare dopo la prima giornata giocata.</p>")

    cal = svg_calibrazione(tr)
    if cal:
        fuori = int(tr.calibrazione["significativo"].sum())
        blocchi.append(
            f"<h3>Calibrazione delle previsioni registrate</h3>"
            f"<div class='card'><div class='due'><div>{cal}</div>"
            f"<div><p class='sub' style='margin-top:0'>Tre esiti impilati: una "
            f"probabilita' del 30% deve verificarsi il 30% delle volte, che sia "
            f"un 1, una X o un 2. Sulla diagonale = calibrato. Le barre sono "
            f"l'intervallo di Wilson del bin; i punti in rosso sono i bin il cui "
            f"scarto non e' spiegabile dal solo campionamento "
            f"({fuori} su {len(tr.calibrazione)}).</p></div></div></div>"
        )
    elif tr.risolte:
        blocchi.append(
            f"<p class='sub'>Servono almeno 50 previsioni risolte perche' una "
            f"curva di calibrazione dica qualcosa: ora sono {tr.risolte}.</p>"
        )
    return "".join(blocchi)


def _html_stato(esito: predict_mod.Esito, diag: Diagnostica, falliti: list[str],
                registro: str, scaricato: pd.Timestamp | None) -> str:
    voci = []
    if scaricato is None:
        voci.append("<dt>snapshot quote</dt><dd class='giu'>assente</dd>")
    else:
        eta = (pd.Timestamp.now(tz="UTC") - scaricato).total_seconds() / 86400
        classe = "giu" if eta > config.FIXTURES_MAX_AGE_DAYS else ""
        voci.append(f"<dt>snapshot quote</dt><dd class='{classe}'>"
                    f"{_esc(quando(scaricato))} ({eta:.1f} giorni fa)</dd>")
    voci.append(f"<dt>partite in giornata</dt><dd>{len(esito.preds)}</dd>")
    voci.append(f"<dt>gia' in registro</dt><dd>{esito.duplicate}</dd>")
    voci.append(f"<dt>senza quote</dt><dd>{len(esito.skipped)}</dd>")
    voci.append(f"<dt>registro</dt><dd class='{'' if registro == SCRITTO else 'giu'}'>"
                f"{_esc(ETICHETTA_REGISTRO[registro])}</dd>")

    scoperte = ""
    if not esito.skipped.empty:
        righe = "".join(
            f"<tr><td class='l q'>{_esc(quando(r.get('kickoff')))}</td>"
            f"<td class='l'>{_esc(r['home_team'])} - {_esc(r['away_team'])}</td>"
            f"<td class='l q'>manca {_esc(r.get('manca', 'quote'))}</td></tr>"
            for _, r in esito.skipped.iterrows()
        )
        scoperte = (
            f"<h3>Partite senza quote, non predette</h3><div class='card'>"
            f"<table>{righe}</table><p class='sub' style='margin-bottom:0'>Normale "
            f"se il resto del turno si gioca in settimana: lo snapshot copre solo "
            f"il turno imminente. Rilanciando piu' avanti si aggiungono, senza "
            f"toccare quelle gia' registrate. Le quote non si imputano: una "
            f"previsione inventata e' peggio di nessuna previsione.</p></div>"
        )

    avvisi = list(diag.avvisi)
    if falliti:
        avvisi.insert(0, "stage di rete falliti, si e' proseguito con i dati gia' "
                         "presenti: " + ", ".join(falliti))
    blocco_avvisi = (
        "<div class='card warn'><strong>Avvisi</strong><ul>"
        + "".join(f"<li>{_esc(a)}</li>" for a in avvisi)
        + "</ul></div>"
    ) if avvisi else "<p class='sub'>Nessun avviso: il ciclo e' filato liscio.</p>"

    return (f"<div class='card'><dl class='kv'>{''.join(voci)}</dl></div>"
            + blocco_avvisi + scoperte)



# ---------------------------------------------------------------------------
# La pagina intera
# ---------------------------------------------------------------------------

def componi(
    *,
    esito: predict_mod.Esito,
    diag: Diagnostica,
    cambiamenti: pd.DataFrame,
    divergenza: pd.DataFrame,
    track: TrackRecord | None,
    falliti: list[str],
    registro: str,
    origine: str,
    scaricato,
    stagione: str,
    giornata,
) -> str:
    """
    Le sezioni gia' calcolate diventano un documento HTML autonomo.

    Riceve DataFrame e non li interroga per decidere cosa mostrare oltre il
    "sono vuoti?": ogni scelta che richiede un conto sta in `sezioni.py`.
    """
    preds = esito.preds
    adesso = pd.Timestamp.now(tz="UTC")

    stato = []
    nuove = max(len(preds) - esito.duplicate, 0)
    etichetta = "previste e registrate" if registro == SCRITTO else "non registrate"
    stato.append(f"<span class='tag{'' if registro == SCRITTO else ' w'}'>"
                 f"{nuove} {etichetta}</span>")
    if esito.duplicate:
        stato.append(f"<span class='tag'>{esito.duplicate} gia' in registro</span>")
    if not esito.skipped.empty:
        stato.append(f"<span class='tag w'>{len(esito.skipped)} senza quote</span>")
    if registro != SCRITTO:
        stato.append(f"<span class='tag w'>{_esc(ETICHETTA_REGISTRO[registro])}</span>")
    if diag.avvisi:
        n = len(diag.avvisi)
        stato.append(f"<span class='tag w'>{n} {'avviso' if n == 1 else 'avvisi'}</span>")

    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Giornata {giornata} - Serie A {stagione}</title>
<style>{CSS}</style></head><body><main>
<h1>Giornata {giornata} <span class='q'>&middot; Serie A {stagione}</span></h1>
<p class="sub">Report del {_esc(quando(adesso))} (ora italiana) &middot;
modello <code>{_esc(esito.model_version)}</code></p>
<p>{''.join(stato)}</p>

<h2><span class="n">1</span>Giornata in arrivo</h2>
{_html_giornata(preds)}
{_html_una_per_partita(preds)}
{_html_con_quota(preds)}
{_html_target(preds)}
{_html_selezioni(preds)}

<h2><span class="n">2</span>Cosa e' cambiato</h2>
{_html_cambiamenti(cambiamenti)}

<h2><span class="n">3</span>Dove il modello diverge dal mercato</h2>
{_html_divergenza(divergenza)}

<h2><span class="n">4</span>Track record</h2>
{_html_track(track)}

<h2><span class="n">5</span>Stato del sistema</h2>
{_html_stato(esito, diag, falliti, registro, scaricato)}

<footer>Generato da <code>{_esc(origine)}</code>.
Il dato e' <code>track_record/predictions_log.csv</code>, append-only e
versionato: questo report ne e' solo una vista e si rigenera con
<code>goalmodel report</code>.</footer>
</main></body></html>"""
