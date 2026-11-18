"""
Il CONTENUTO del report: le cinque sezioni, calcolate e restituite come
DataFrame. Nessuna stringa HTML esce da qui.

PERCHE' E' SEPARATO DALL'IMPAGINAZIONE (audit A4)
`report.py` faceva quattro mestieri in milletrecento righe, e uno di questi
era addestrare un modello. Conseguenza concreta, non teorica: un errore di
feature dentro `divergenza()` si manifestava come "sezione mancante nel
report" invece che come errore di modellazione, e si andava a cercare nel
posto sbagliato. E' esattamente il difetto B1.

La regola e' quella: **qui dentro si calcola, in `pagina.py` si impagina**.
Una funzione che restituisce una stringa con dentro un tag sta nel file
sbagliato.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config, data
from ..prediction import predict as predict_mod

log = logging.getLogger("report")

KEYS = config.JOIN_KEYS


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
# Selezioni (condivise con il riepilogo a terminale di predict_round.py)
# ---------------------------------------------------------------------------

TRIVIALI = {"over 0.5", "under 0.5", "over 4.5", "under 4.5", "under 1.5"}

# I soli mercati per cui il registro conserva la quota vera del bookmaker.
# Gli altri si potrebbero solo stimare applicando un margine medio, che
# sarebbe un numero inventato con l'aria di essere misurato.
QUOTE_BOOK = {"1": "odds_home", "X": "odds_draw", "2": "odds_away"}


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
    from ..models.baseline import all_markets, fair_odds

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
                # Il nome grezzo serve a ritrovare la quota del book: le
                # etichette leggibili ("1 (Napoli)") non si rimappano.
                "mercato_raw": mercato,
                "probabilita": float(p),
                "quota_equa": float(fair_odds(p)),
                # Il book quota solo l'1X2: per doppia chance, over/under e
                # gol-gol non abbiamo il suo prezzo, e stimarlo applicando un
                # margine medio sarebbe inventarlo. Meglio lasciarlo vuoto.
                "quota_book": QUOTE_BOOK.get(mercato) and r.get(QUOTE_BOOK[mercato]),
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

    SU COSA E' ADDESTRATO. Solo sulle colonne che esistono anche per le
    partite in arrivo: un blocco che si costruisce per lo storico ma non per
    una partita da giocare — oggi le assenze — resta fuori da entrambi i lati.
    Il perche' e' nel commento sopra la costruzione del modello.

    IL MODELLO PUO' DEGENERARE IN SILENZIO. Se le feature di forma arrivano
    tutte nulle, LightGBM si ferma a un albero e restituisce la stessa
    previsione per ogni partita: nessun errore, nessuna eccezione, e uno
    scarto dal mercato enorme e privo di significato. Qui si controlla e, se
    succede, la sezione non si stampa e l'avviso finisce nella diagnostica.
    """
    if preds.empty:
        return pd.DataFrame()

    from ..evaluation.evaluate import load_dataset
    from ..models import gbm

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

    # M4 SI ADDESTRA SOLO SU CIO' CHE ESISTERA' AL MOMENTO DI PREDIRE.
    # Le due parti nascono da percorsi diversi e non possono coincidere:
    # `load_dataset` unisce i parquet di TUTTI i blocchi, mentre
    # `predict.build_features` ricostruisce in memoria solo quelli calcolabili
    # per una partita non ancora giocata. Le assenze del turno in arrivo non
    # sono ancora state scaricate, quindi il blocco giocatori c'e' di qua e
    # non di la'.
    #
    # Addestrare sull'insieme grande e predire su quello piccolo non e'
    # un'alternativa: LightGBM manderebbe ogni riga futura sul ramo dei
    # mancanti proprio sulla feature piu' importante di quel blocco
    # (`home_quota_minuti_assenti`, prima su 61), e la previsione sarebbe
    # distorta senza sollevare niente. Prima si preferiva saltare la sezione,
    # che e' il difetto B1: una funzionalita' persa per un elenco di colonne
    # che nessuno aveva deciso.
    assenti = tuple(c for c in gbm.form_features(train) if c not in preds.columns)
    if assenti:
        log.info("divergenza: %d colonne assenti dalle partite in arrivo, fuori "
                 "dal modello (es. %s)", len(assenti), list(assenti[:3]))

    modello = gbm.PoissonGBM(
        use_market=False,
        escludi=tuple(gbm.FUORI_DAL_MODELLO) + assenti,
        **config.GBM_PARAMS_NO_MARKET,
    )
    try:
        modello.fit(train)
        alt = modello.predict(preds)
    except Exception as exc:
        # LARGO DI PROPOSITO, MA NON MUTO (audit B4). Il report deve uscire
        # comunque: una sezione diagnostica mancante e' meglio di nessun
        # report, e questa sezione addestra un modello, cioe' il punto dove e'
        # piu' facile che qualcosa vada storto. Il prezzo e' che un errore di
        # programmazione qui somiglia a un problema di dati, quindi il
        # traceback finisce nel log — dove si puo' leggere — e non solo la
        # riga riassuntiva nella diagnostica del report.
        log.exception("divergenza: M4 ha sollevato, sezione saltata")
        diag.avvisa(f"M4 non ha prodotto previsioni ({type(exc).__name__}: {exc}): "
                    f"sezione divergenza saltata. Il traceback e' nel log.")
        return pd.DataFrame()

    # Un albero per lato significa che l'arresto anticipato non ha trovato
    # niente da imparare: quasi sempre feature tutte nulle. Il confronto
    # sarebbe fra il mercato e una costante.
    if modello.best_iters_ and max(modello.best_iters_) <= 1:
        diag.avvisa(
            "M4 si e' fermato a un albero per lato: sta prevedendo la stessa cosa "
            "per tutte le partite, segno che le feature di forma arrivano nulle. "
            "Ricostruiscile con 'goalmodel features-form'. Sezione saltata."
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
    from ..evaluation.evaluate import _onehot, accuracy, calibration_table, outcome_index, rps
    from ..prediction import backtest_log as bl

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
    from ..evaluation.evaluate import cluster_bootstrap

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
    wf = data.load_processed_opzionale("walk_forward_predictions")
    if wf is None:
        diag.avvisa("walk_forward_predictions.parquet assente: non posso "
                    "stimare quante previsioni servono. Lancia "
                    "'goalmodel evaluate'.")
        return None
    from ..evaluation.evaluate import PROB_COLS, outcome_index, rps

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

