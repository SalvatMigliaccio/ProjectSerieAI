"""
Il report settimanale in HTML: un file statico, autonomo, apribile col doppio
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
    python -m src.report
    python -m src.report --open        # genera e apre nel browser
    python -m src.report --no-diverge  # salta la sezione 3 (che addestra M4)
"""

from __future__ import annotations

import argparse
import logging
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from . import predict as predict_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("report")

# Percorso fisso: e' il file che si tiene aperto nel browser e si ricarica.
# Sta accanto al registro perche' e' la sua lettura, ma NON e' versionato —
# si rigenera, e un HTML che cambia ogni settimana in git e' solo rumore.
REPORT = config.TRACK_RECORD / "report.html"

# L'archivio, uno per giornata. Riaprire il report di tre turni fa e'
# esattamente cio' che serve per capire come sono andate le previsioni.
ARCHIVIO = config.PROCESSED / "reports"

GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

KEYS = ["league", "season", "home_team", "away_team"]

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


@dataclass
class Diagnostica:
    """
    Cosa non ha funzionato mentre si costruiva il report.

    Vive in un oggetto invece che nei soli log perche' la sezione 5 deve
    poterlo stampare: un avviso che esiste solo nel terminale di sabato
    mattina, quando si rilegge il report il mercoledi', non esiste.
    """

    avvisi: list[str] = field(default_factory=list)

    def avvisa(self, testo: str) -> None:
        log.warning("%s", testo)
        self.avvisi.append(testo)


# ---------------------------------------------------------------------------
# Formattazione
# ---------------------------------------------------------------------------

def quando(ko) -> str:
    """Data e ora in ora italiana: e' quella che l'utente ha in testa."""
    if ko is None or pd.isna(ko):
        return "  ?"
    loc = pd.Timestamp(ko)
    loc = loc.tz_localize("UTC") if loc.tz is None else loc
    loc = loc.tz_convert("Europe/Rome")
    return f"{GIORNI[loc.weekday()]} {loc:%d/%m} {loc:%H:%M}"


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Selezioni (condivise con il riepilogo a terminale di weekly.py)
# ---------------------------------------------------------------------------

TRIVIALI = {"over 0.5", "under 0.5", "over 4.5", "under 4.5", "under 1.5"}


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
            # Le quasi-certezze si escludono: 'over 0.5' sta al 92% ma nessun
            # book lo paga abbastanza perche' la giocata abbia senso, e in
            # cima alla classifica coprirebbe tutto il resto.
            if mercato in TRIVIALI or p < minimo:
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


# ---------------------------------------------------------------------------
# Sezione 2 — cosa e' cambiato
# ---------------------------------------------------------------------------

def _lungo(preds: pd.DataFrame) -> pd.DataFrame:
    """Da una riga per partita a una riga per squadra, con il suo lambda."""
    righe = []
    for _, r in preds.iterrows():
        for lato, avv, lam in (("casa", "away_team", "lambda_home"),
                               ("trasferta", "home_team", "lambda_away")):
            squadra = r["home_team"] if lato == "casa" else r["away_team"]
            righe.append({
                "squadra": squadra,
                "dove": lato,
                "avversario": r[avv],
                "lambda": float(r[lam]),
                "match_date": pd.to_datetime(r["date"]),
                "chiave": tuple(str(r[k]) for k in KEYS),
            })
    return pd.DataFrame(righe)


def cambiamenti(preds: pd.DataFrame, model_version: str,
                diag: Diagnostica) -> pd.DataFrame:
    """
    Per ogni squadra della giornata: i gol attesi di adesso contro quelli
    dell'ultima previsione che la riguardava.

    PERCHE' SERVE. Un lambda isolato non e' leggibile: 1.12 e' tanto o poco?
    Dipende dall'avversario, dal campo, dal momento. Il confronto con la
    previsione precedente della stessa squadra rende il numero interpretabile
    senza doverlo calibrare a mente — "il Napoli e' passato da 1.45 in casa a
    1.12 in trasferta" dice qualcosa che "1.12" da solo non dice.

    NON E' UNA MISURA DI ERRORE. Fra due previsioni cambia sia la squadra (la
    forma, gli infortuni che il mercato ha prezzato) sia l'avversario e il
    campo, e i due effetti non si separano. E' un aiuto alla lettura, e va
    letto insieme all'avversario e al fattore campo che la tabella riporta.

    Il confronto e' a parita' di `model_version`: mescolare previsioni di
    modelli diversi misurerebbe la differenza fra i modelli, non il movimento
    della squadra.
    """
    if preds.empty:
        return pd.DataFrame()

    try:
        storico = pd.read_csv(predict_mod.PREDICTIONS_LOG)
    except FileNotFoundError:
        diag.avvisa("registro assente: nessun confronto con le previsioni precedenti")
        return pd.DataFrame()
    if storico.empty:
        return pd.DataFrame()

    storico = storico[storico["model_version"] == model_version].copy()
    if storico.empty:
        diag.avvisa(f"nessuna previsione precedente del modello '{model_version}': "
                    f"la sezione 'cosa e' cambiato' resta vuota")
        return pd.DataFrame()

    storico["timestamp_prediction"] = pd.to_datetime(storico["timestamp_prediction"], utc=True)
    storico["match_date"] = pd.to_datetime(storico["match_date"])
    passato = _lungo(storico.rename(columns={"match_date": "date"}))
    # `_lungo` emette due righe per partita, nell'ordine (casa, trasferta):
    # ripetere ogni timestamp due volte lo riallinea senza rifare il giro
    # delle chiavi. Serve solo a rompere la parita' fra due previsioni della
    # stessa data, dove vince la piu' recente.
    passato["timestamp"] = np.repeat(storico["timestamp_prediction"].to_numpy(), 2)

    attuale = _lungo(preds)
    chiavi_ora = set(attuale["chiave"])

    righe = []
    for _, r in attuale.iterrows():
        prec = passato[
            (passato["squadra"] == r["squadra"])
            & (~passato["chiave"].isin(chiavi_ora))
            & (passato["match_date"] < r["match_date"])
        ]
        if prec.empty:
            righe.append({**r.to_dict(), "prima": np.nan})
            continue
        # La piu' recente per data di partita: e' l'ultima volta che di quella
        # squadra si e' detto qualcosa.
        p = prec.sort_values(["match_date", "timestamp"]).iloc[-1]
        righe.append({
            **r.to_dict(),
            "prima": p["lambda"],
            "prima_dove": p["dove"],
            "prima_avversario": p["avversario"],
            "prima_data": p["match_date"],
        })

    tab = pd.DataFrame(righe)
    tab["delta"] = tab["lambda"] - tab["prima"]
    return tab.sort_values("delta", key=lambda s: s.abs(), ascending=False)


# ---------------------------------------------------------------------------
# Sezione 3 — divergenza dal mercato
# ---------------------------------------------------------------------------

PROB = ["p_home", "p_draw", "p_away"]


def divergenza(preds: pd.DataFrame, diag: Diagnostica) -> pd.DataFrame:
    """
    Quanto il modello statistico si scosta dal mercato, partita per partita.

    COS'E' E COSA NON E'. E' M4 senza feature di mercato — il modello che dai
    soli dati di gioco copre l'84.6% della distanza fra il pavimento e le
    quote — messo accanto a M1, che le quote *e'*. Lo scarto fra i due NON e'
    un segnale di scommessa: il test set ha stabilito che nessun modello
    statistico batte il mercato, e che le stesse feature che alimentano M4
    sono informazione che le quote gia' contengono, prezzata meglio. Uno
    scarto grande e' quindi un'ANOMALIA DA CAPIRE — di solito una squadra le
    cui medie mobili non hanno ancora registrato qualcosa che il mercato
    conosce (un infortunio, un cambio in panchina, un mercato di gennaio) —
    non un'occasione da giocare.

    La distanza e' la variazione totale, meta' della somma degli scarti
    assoluti sui tre esiti: un numero solo, in punti di probabilita', che dice
    quanta massa andrebbe spostata per passare da una previsione all'altra.

    IL MODELLO PUO' DEGENERARE IN SILENZIO. Se le feature di forma arrivano
    tutte nulle, LightGBM si ferma a un albero e restituisce la stessa
    previsione per ogni partita: nessun errore, nessuna eccezione, e uno
    scarto dal mercato enorme e privo di significato. Qui si controlla e, se
    succede, la sezione non si stampa e l'avviso finisce nella diagnostica.
    """
    if preds.empty:
        return pd.DataFrame()

    from .evaluate import load_dataset
    from .models.gbm import PoissonGBM

    try:
        df = load_dataset()
    except FileNotFoundError as exc:
        diag.avvisa(f"dataset non disponibile, sezione divergenza saltata: {exc}")
        return pd.DataFrame()

    taglio = pd.to_datetime(preds["date"]).min()
    train = df[
        (df["date"] < taglio)
        & df["FTR"].notna()
        & (~df["season"].isin(config.BURN_IN_SEASONS))
    ]
    if len(train) < 1000:
        diag.avvisa(f"solo {len(train)} partite di storico: M4 non si addestra, "
                    f"sezione divergenza saltata")
        return pd.DataFrame()

    modello = PoissonGBM(use_market=False, **config.GBM_PARAMS_NO_MARKET)
    try:
        modello.fit(train)
        mancanti = [c for c in modello.features_ if c not in preds.columns]
        if mancanti:
            diag.avvisa(f"{len(mancanti)} feature di M4 assenti dalle partite in "
                        f"arrivo (es. {mancanti[:3]}): sezione divergenza saltata")
            return pd.DataFrame()
        alt = modello.predict(preds)
    except Exception as exc:  # il report deve uscire comunque
        diag.avvisa(f"M4 non ha prodotto previsioni ({exc}): sezione divergenza saltata")
        return pd.DataFrame()

    # Un albero per lato significa che l'arresto anticipato non ha trovato
    # niente da imparare: quasi sempre feature tutte nulle. Il confronto
    # sarebbe fra il mercato e una costante.
    if modello.best_iters_ and max(modello.best_iters_) <= 1:
        diag.avvisa(
            "M4 si e' fermato a un albero per lato: sta prevedendo la stessa cosa "
            "per tutte le partite, segno che le feature di forma arrivano nulle. "
            "Ricostruiscile con 'python -m src.features.form'. Sezione saltata."
        )
        return pd.DataFrame()

    p_mkt = preds[PROB].to_numpy(float)
    p_gbm = alt[PROB].to_numpy(float)
    tab = pd.DataFrame({
        "kickoff": preds["kickoff"].to_numpy() if "kickoff" in preds else pd.NaT,
        "partita": (preds["home_team"] + " - " + preds["away_team"]).to_numpy(),
        "mkt_home": p_mkt[:, 0], "mkt_draw": p_mkt[:, 1], "mkt_away": p_mkt[:, 2],
        "gbm_home": p_gbm[:, 0], "gbm_draw": p_gbm[:, 1], "gbm_away": p_gbm[:, 2],
    })
    tab["scarto"] = 0.5 * np.abs(p_gbm - p_mkt).sum(axis=1)
    log.info("divergenza: M4 addestrato su %d partite, %s alberi per lato",
             len(train), modello.best_iters_)
    return tab.sort_values("scarto", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Sezione 4 — track record
# ---------------------------------------------------------------------------

@dataclass
class TrackRecord:
    """Il track record gia' masticato, pronto da impaginare."""

    totali: int = 0
    escluse: int = 0
    risolte: int = 0
    in_attesa: int = 0
    rps: float | None = None
    rps_mercato: float | None = None
    accuratezza: float | None = None
    per_partita: pd.DataFrame = field(default_factory=pd.DataFrame)
    calibrazione: pd.DataFrame = field(default_factory=pd.DataFrame)
    fabbisogno: dict | None = None


def carica_track_record(diag: Diagnostica) -> TrackRecord | None:
    """Registro + risultati veri, con l'RPS di ogni singola partita."""
    from . import backtest_log as bl
    from .evaluate import _onehot, accuracy, calibration_table, outcome_index, rps

    # Il percorso si legge da `predict_mod` al momento della chiamata, non dal
    # default di `load_log`, che viene fissato all'import: e' l'unico modo di
    # far girare il report su un registro finto in un test.
    try:
        preds = bl.load_log(path=predict_mod.PREDICTIONS_LOG)
    except FileNotFoundError:
        return None

    joined = bl.attach_results(preds)
    valide = joined[~joined["post_kickoff"]]
    risolte = valide[valide["FTR"].notna()].copy()

    tr = TrackRecord(
        totali=len(joined),
        escluse=int(joined["post_kickoff"].sum()),
        risolte=len(risolte),
        in_attesa=int(valide["FTR"].isna().sum()),
    )
    if tr.escluse:
        diag.avvisa(f"{tr.escluse} righe del registro sono state scritte dopo il "
                    f"calcio d'inizio: restano nel file ma non entrano nelle metriche")
    if risolte.empty:
        # Il fabbisogno si stima ANCHE con il registro vuoto: e' proprio
        # all'inizio che serve sapere quanto sara' lunga l'attesa, e un blocco
        # che compare solo quando i numeri ci sono gia' non serve a nessuno.
        tr.fabbisogno = fabbisogno(risolte, diag)
        return tr

    # L'ordine cronologico serve alla media progressiva; le metriche medie non
    # ne risentono, ma si ordina una volta sola per non tenere in giro due
    # versioni dello stesso frame.
    risolte = risolte.sort_values(["match_date", "home_team"])
    p = risolte[bl.PROB_COLS].to_numpy(float)
    y = outcome_index(risolte["FTR"])
    risolte["rps"] = rps(p, y)
    risolte["rps_cumulativo"] = risolte["rps"].expanding().mean()

    tr.rps = float(risolte["rps"].mean())
    tr.accuratezza = float(accuracy(p, y).mean())
    tr.per_partita = risolte
    ref = bl.market_reference(risolte)
    tr.rps_mercato = None if ref is None else ref["RPS"]

    if len(risolte) >= 50:
        tr.calibrazione = calibration_table(p.reshape(-1), _onehot(y).reshape(-1))
    tr.fabbisogno = fabbisogno(risolte, diag)
    return tr


def fabbisogno(risolte: pd.DataFrame, diag: Diagnostica,
               tolleranza: float = config.TRACK_TOLERANCE_RPS) -> dict:
    """
    Quante previsioni servono ancora perche' il confronto con il backtest
    voglia dire qualcosa.

    LA DOMANDA. Il track record va letto contro RPS 0.1881, il valore del
    mercato sul test set. Uno scarto persistente da li' non sarebbe un modello
    che sbaglia — il modello *e'* la linea di apertura — sarebbe la pipeline di
    produzione che si comporta diversamente dal backtest. Ma con venti partite
    qualunque scarto e' rumore, e dichiararlo senza dire quanto rumore c'e'
    sarebbe peggio che non dirlo.

    COME SI STIMA. Si prende l'ampiezza dell'intervallo appaiato sull'RPS
    medio, con bootstrap a cluster sulla giornata — le dieci partite di una
    giornata sono predette dallo stesso addestramento, trattarle come dieci
    osservazioni indipendenti restringe l'intervallo del 38%, misurato. Poi si
    usa il fatto che la semiampiezza scala come 1/sqrt(cluster):

        cluster_necessari = cluster_attuali * (semiampiezza / tolleranza)^2

    Sotto i quattro cluster il bootstrap non ha di che ricampionare — e a
    registro vuoto non ha proprio niente — quindi si ripiega sulla deviazione
    standard per giornata misurata sul walk-forward: la stessa quantita',
    stimata su 114 giornate vere invece che su due. In quel caso la risposta
    non e' "quante ne mancano rispetto all'intervallo di adesso" ma "quante ne
    servono in tutto", che a registro vuoto e' la stessa cosa.
    """
    from .evaluate import cluster_bootstrap

    if risolte.empty:
        n_cluster, per_cluster = 0, 10.0  # una giornata di Serie A
    else:
        cluster = (risolte["season"].astype(str) + "-"
                   + risolte["matchday"].astype(str)).to_numpy()
        n_cluster = len(pd.unique(cluster))
        per_cluster = len(risolte) / n_cluster

    if n_cluster >= 4:
        # La differenza appaiata contro una costante e' la serie stessa meno
        # quella costante: l'intervallo sulla media non cambia di ampiezza.
        res = cluster_bootstrap(
            risolte["rps"].to_numpy(float) - config.TEST_RPS_REFERENCE, cluster,
        )
        semi = (res["ic_alto"] - res["ic_basso"]) / 2
        necessari = n_cluster * (semi / tolleranza) ** 2
        metodo = "bootstrap a cluster sulle previsioni registrate"
    else:
        sd = _sd_cluster_walk_forward(diag)
        if sd is None:
            return {"n_cluster": n_cluster, "n_partite": len(risolte),
                    "metodo": "non stimabile", "tolleranza": tolleranza}
        from scipy import stats
        z = stats.norm.ppf(0.975) + stats.norm.ppf(0.80)
        semi = (stats.norm.ppf(0.975) * sd / np.sqrt(n_cluster)
                if n_cluster else None)
        necessari = (z * sd / tolleranza) ** 2
        metodo = ("deviazione standard per giornata misurata sul walk-forward: "
                  "le giornate registrate sono troppo poche per il bootstrap")

    return {
        "n_cluster": n_cluster,
        "n_partite": len(risolte),
        "per_cluster": per_cluster,
        "semiampiezza": None if semi is None else float(semi),
        "tolleranza": tolleranza,
        "cluster_necessari": int(np.ceil(necessari)),
        "partite_necessarie": int(np.ceil(necessari * per_cluster)),
        "mancanti": max(int(np.ceil(necessari)) - n_cluster, 0),
        "metodo": metodo,
    }


def _sd_cluster_walk_forward(diag: Diagnostica) -> float | None:
    """
    Deviazione standard dell'RPS medio di giornata, dal walk-forward.

    E' il ripiego di `fabbisogno` finche' il registro non ha abbastanza
    giornate: la stessa quantita', misurata su 114 giornate vere del test set
    invece che sulle due o tre gia' accumulate in produzione.
    """
    path = config.PROCESSED / "walk_forward_predictions.parquet"
    if not path.exists():
        diag.avvisa(f"{path.name} assente: non posso stimare quante previsioni "
                    f"servono. Lancia 'python -m src.evaluate'.")
        return None
    from .evaluate import PROB_COLS, outcome_index, rps

    wf = pd.read_parquet(path)
    rif = "M1b market-only (diretto)"
    wf = wf[wf["model"] == rif] if rif in set(wf["model"]) else wf[wf["model"] == wf["model"].iloc[0]]
    wf = wf.dropna(subset=PROB_COLS)
    if wf.empty:
        return None
    r = rps(wf[PROB_COLS].to_numpy(float), outcome_index(wf["FTR"]))
    per_cluster = pd.Series(r).groupby(
        (wf["season"].astype(str) + "-" + wf["matchday"].astype(str)).to_numpy()
    ).mean()
    return float(per_cluster.std(ddof=1))


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
            f"<dt>dove</dt><dd>{squadra} in "
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
                           "<code>python -m src.evaluate</code>.</p>")
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
        f"<div class='card warn'><strong>Avvisi</strong><ul>"
        + "".join(f"<li>{_esc(a)}</li>" for a in avvisi)
        + "</ul></div>"
    ) if avvisi else "<p class='sub'>Nessun avviso: il ciclo e' filato liscio.</p>"

    return (f"<div class='card'><dl class='kv'>{''.join(voci)}</dl></div>"
            + blocco_avvisi + scoperte)


# ---------------------------------------------------------------------------
# Costruzione
# ---------------------------------------------------------------------------

def build(
    esito: predict_mod.Esito,
    *,
    falliti: list[str] | tuple[str, ...] = (),
    registro: str = SCRITTO,
    diverge: bool = True,
    origine: str = "python -m src.weekly",
) -> Path:
    """
    Scrive `track_record/report.html` e la copia d'archivio della giornata.

    Il report esce SEMPRE, anche quando non c'e' niente da prevedere: e'
    proprio il caso in cui si vuole sapere perche', e la sezione 5 lo dice.

    `registro` dice cosa e' successo al registro mentre si costruiva questo
    report, e non e' cosmesi: un report che dichiara "10 previste" quando
    nessuna riga e' stata scritta e' peggio di nessun report, perche' a
    distanza di settimane sembra un track record e non lo e'.
    """
    diag = Diagnostica()
    preds = esito.preds

    _, scaricato = predict_mod.load_fixtures_odds()
    if scaricato is not None:
        eta = (pd.Timestamp.now(tz="UTC") - scaricato).total_seconds() / 86400
        if eta > config.FIXTURES_MAX_AGE_DAYS:
            diag.avvisa(f"lo snapshot quote ha {eta:.1f} giorni: copre solo il turno "
                        f"imminente e viene sovrascritto. Rilancia "
                        f"'python ingest.py --stage fixtures'.")
    else:
        diag.avvisa("snapshot quote assente: le previsioni, se ci sono, vengono dal "
                    "ripiego manuale o dallo storico")

    if not esito.skipped.empty:
        diag.avvisa(f"{len(esito.skipped)} partite della giornata non hanno quote "
                    f"complete e non sono state predette")

    tab_cambiamenti = cambiamenti(preds, esito.model_version, diag)
    tab_divergenza = (divergenza(preds, diag) if diverge and not preds.empty
                      else pd.DataFrame())
    if not diverge:
        diag.avvisa("sezione divergenza saltata su richiesta (--no-diverge)")
    tr = carica_track_record(diag)

    stagione = (str(preds["season"].iloc[0]) if not preds.empty
                else config.CURRENT_SEASON)
    giornata = esito.matchday if esito.matchday is not None else "?"
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

    html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Giornata {giornata} - Serie A {stagione}</title>
<style>{CSS}</style></head><body><main>
<h1>Giornata {giornata} <span class='q'>&middot; Serie A {stagione}</span></h1>
<p class="sub">Report del {_esc(quando(adesso))} (ora italiana) &middot;
modello <code>{_esc(esito.model_version)}</code></p>
<p>{''.join(stato)}</p>

<h2><span class="n">1</span>Giornata in arrivo</h2>
{_html_giornata(preds)}
{_html_target(preds)}
{_html_selezioni(preds)}

<h2><span class="n">2</span>Cosa e' cambiato</h2>
{_html_cambiamenti(tab_cambiamenti)}

<h2><span class="n">3</span>Dove il modello diverge dal mercato</h2>
{_html_divergenza(tab_divergenza)}

<h2><span class="n">4</span>Track record</h2>
{_html_track(tr)}

<h2><span class="n">5</span>Stato del sistema</h2>
{_html_stato(esito, diag, list(falliti), registro, scaricato)}

<footer>Generato da <code>{_esc(origine)}</code>.
Il dato e' <code>track_record/predictions_log.csv</code>, append-only e
versionato: questo report ne e' solo una vista e si rigenera con
<code>python -m src.report</code>.</footer>
</main></body></html>"""

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
    # scrivono da 'python -m src.weekly', una volta sola, prima del fischio.
    esito = predict_mod.run(use_next=True, dry_run=True, quiet=True)
    path = build(esito, registro=RIGENERATO, diverge=not args.no_diverge,
                 origine="python -m src.report")
    print(f"\n{path}")
    if args.apri:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
