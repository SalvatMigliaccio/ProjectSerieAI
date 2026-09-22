"""
Blocco B: quanto pesa chi non gioca.

L'IDEA. Il mercato prezza il calendario mesi prima, e il blocco A lo ha
dimostrato: riposo, congestione e coppe non aggiungono niente alle quote. Un
infortunio di giovedi' e' l'unico posto dove il mercato puo' essere
strutturalmente LENTO, perche' l'informazione nasce due giorni prima della
partita. E' per questo che il layer giocatori era il candidato migliore fin
dall'inizio.

LE DUE FEATURE, E PERCHE' DUE
  quota_minuti_assenti   quota dei minuti di squadra indisponibili
  quota_ga_assente       quota dei gol+assist prodotti, indisponibile

La prima conta chiunque: un centrale, un terzino, il portiere. La seconda pesa
per quanto quel giocatore produce, e vede l'attaccante ma NON vede il portiere
titolare, che di gol+assist ne fa zero. Sono complementari, e tenere solo la
seconda — come diceva la formulazione iniziale del piano — renderebbe
invisibile meta' delle assenze che contano.

GOL+ASSIST E NON xG+xA, PERCHE' IL DATO NON C'E'
Il piano diceva "xG+xA per 90". Le statistiche giocatore-partita di FBref
servite da soccerdata per la Serie A non hanno nessuna colonna attesa
(verificato su 23632 righe): ci sono `performance_gls` e `performance_ast` e
basta. Gol+assist e' lo stesso concetto misurato molto peggio — un attaccante
che non ha ancora segnato pesa ZERO — ed e' il limite piu' serio di questo
blocco. La via d'uscita, se servira', e' l'xG per giocatore da Understat
(`ingest --stage shots`), non un'altra tabella di FBref.

IL PESO SI SEMPLIFICA, E VA SAPUTO
"Minuti pesati per produzione per 90" si riduce alla produzione totale:
    minuti x (GA / (minuti/90)) = 90 x GA
Quindi `quota_ga_assente` e' la quota dei gol+assist della rosa che manca.
Non e' un errore, e' aritmetica — ma chi legge "minuti pesati" si aspetta
altro, e vale la pena scriverlo.

LEAKAGE: I MINUTI SONO QUELLI ALLA DATA, NON DI FINE STAGIONE
E' il punto in cui questo blocco puo' rompersi in silenzio. L'aggregato
stagionale di FBref costa 13 richieste invece di 4580 ed e' comodissimo, ma
contiene i minuti giocati DOPO la partita da predire: un giocatore che si
infortuna a ottobre risulterebbe poco importante perche' ha pochi minuti
TOTALI, e il modello imparerebbe dal futuro. Qui si usa il per partita, e i
pesi di ogni riga guardano solo le partite con data STRETTAMENTE precedente.

IL TURNOVER NON PUO' ESSERE QUELLO DI QUESTA PARTITA
"Quanti titolari sono cambiati rispetto alla partita precedente" richiede la
formazione di OGGI, che esce un'ora prima del fischio: fuori dall'orizzonte
T-24h, che e' una decisione bloccata del progetto. Qui il turnover e' quello
gia' osservato — fra le due partite precedenti — ed e' una misura della
propensita' a ruotare, non della rotazione di stasera.

Uso:
    python -m src.features.players
    python -m src.features.players --no-save
"""

from __future__ import annotations

import argparse
import logging
import re
import unicodedata

import numpy as np
import pandas as pd

from .. import config

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("players")

KEYS = ["league", "season", "home_team", "away_team"]

# Motivi di assenza che NON contano come indisponibilita'. Un giocatore "in
# dubbio" a T-24h e' incerto, non assente: contarlo fra gli assenti sovrastima
# sistematicamente la quota, e il mercato quell'incertezza la prezza a meta'.
DUBBI = {"injured doubtful"}

FEATURES_PLAYERS = [
    "home_quota_minuti_assenti", "away_quota_minuti_assenti",
    "diff_quota_minuti_assenti",
    "home_quota_ga_assente", "away_quota_ga_assente",
    "diff_quota_ga_assente",
    "home_n_assenti", "away_n_assenti", "diff_n_assenti",
]


def normalizza_nome(s: pd.Series) -> pd.Series:
    """
    Nome confrontabile fra fonti: senza accenti, senza punteggiatura,
    minuscolo.

    WhoScored e FBref scrivono gli stessi giocatori in modo diverso
    (`Rafael Leão` contro `Rafael Leao`, `Vázquez` contro `Vazquez`). Senza
    normalizzare, l'aggancio scende sotto il 70% e la quota di assenti
    risulta bassa per un errore di join invece che per una squadra al
    completo — che e' la conclusione sbagliata presa con un numero giusto.
    """
    def _n(x) -> str:
        if not isinstance(x, str):
            return ""
        x = unicodedata.normalize("NFKD", x)
        x = "".join(c for c in x if not unicodedata.combining(c))
        return " ".join(re.sub(r"[^a-zA-Z ]", " ", x).lower().split())
    return s.map(_n)


def _colonna(df: pd.DataFrame, *candidati: str) -> str:
    """
    La prima colonna che esiste fra i candidati.

    I nomi di FBref cambiano fra versioni di soccerdata e fra `stat_type`:
    indovinarne uno solo produce un KeyError a meta' di uno script lungo.
    """
    for c in candidati:
        if c in df.columns:
            return c
    raise KeyError(
        f"nessuna di queste colonne e' presente: {candidati}. "
        f"Disponibili: {sorted(df.columns)[:40]}"
    )


# ---------------------------------------------------------------------------
# Le due fonti
# ---------------------------------------------------------------------------

def carica_assenze() -> pd.DataFrame:
    """
    Chi manca, per partita, agganciato alla quadrupla del progetto.

    WhoScored identifica la partita con un `game_id` suo: la traduzione passa
    dai calendari salvati in `data/raw/whoscored/`, non dal parsing della
    stringa `game` — che contiene trattini anche nei nomi squadra e si
    romperebbe su "Inter Milan-AC Milan" senza dare errore.

    ORIZZONTE. Questi dati vengono dalla pagina di anteprima della partita,
    pubblicata prima del calcio d'inizio: e' informazione T-24h. E' l'assunto
    su cui poggia tutto il blocco, e se un giorno si scoprisse che WhoScored
    aggiorna quelle liste a posteriori, il blocco B andrebbe buttato — non
    corretto.
    """
    from ..normalize import apply_name_map, load_name_map

    path = config.RAW / "whoscored_missing.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path.name} assente: lancia 'python ingest.py --stage missing'"
        )
    miss = pd.read_parquet(path)

    cal = [pd.read_parquet(p)[["season", "game_id", "home_team", "away_team"]]
           for p in sorted((config.RAW / "whoscored").glob("schedule_*.parquet"))]
    if not cal:
        raise FileNotFoundError(
            "nessun calendario WhoScored in data/raw/whoscored/: senza, il "
            "game_id non si traduce nella quadrupla"
        )
    cal = pd.concat(cal, ignore_index=True).drop_duplicates("game_id")

    mappa = load_name_map()
    # `apply_name_map` pretende home_team e away_team: il calendario le ha, il
    # frame degli assenti no — li' la squadra sta in `team`, e si mappa a mano.
    cal = apply_name_map(cal, mappa)
    miss["team"] = miss["team"].replace(mappa)

    miss["game_id"] = miss["game_id"].astype(int)
    cal["game_id"] = cal["game_id"].astype(int)
    out = miss.merge(cal.drop(columns="season"), on="game_id", how="left")

    orfane = out["home_team"].isna().sum()
    if orfane:
        log.warning("%d righe di assenza senza partita nel calendario", orfane)
    out = out.dropna(subset=["home_team"])

    out["league"] = "ITA-Serie A"
    out["season"] = out["season"].astype(str)
    out["indisponibile"] = ~out["reason"].isin(DUBBI)
    out["chiave_giocatore"] = normalizza_nome(out["player"])
    log.info("assenze: %d righe, %d partite, %d indisponibili certi",
             len(out), out["game_id"].nunique(), int(out["indisponibile"].sum()))
    return out


def carica_giocatori() -> pd.DataFrame:
    """
    Minuti e produzione di ogni giocatore in ogni partita gia' giocata.

    IL PESO E' GOL+ASSIST, NON xG+xA, E NON PER SCELTA. Le statistiche
    giocatore-partita di FBref servite da soccerdata per la Serie A NON hanno
    colonne attese: ci sono `performance_gls` e `performance_ast`, non esiste
    niente che somigli a un xG (verificato su 23632 righe di 2021/22 e
    2022/23). Il piano diceva xG+xA per 90, e il dato non c'e'.

    Cosa cambia in peggio: gol+assist e' molto piu' rumoroso dell'xG. Un
    attaccante che in dieci partite non ha ancora segnato riceve peso ZERO, e
    la sua assenza risulta irrilevante quando non lo e'. Il peso premia chi ha
    gia' concretizzato, non chi produce occasioni.

    Perche' si tiene comunque: `quota_minuti_assenti` non dipende da questo e
    copre chiunque, portiere compreso. Se il blocco dovesse dipendere solo
    dalla colonna dei gol, la strada e' prendere l'xG per giocatore da
    Understat (`ingest --stage shots`, che ha xG per singolo tiro e il
    passatore) invece di cercarlo ancora su FBref.

    LA DATA NON C'E' COME COLONNA. Sta dentro `game` ("2021-08-21
    Empoli-Lazio") e dentro `game_id`. Si prende dal calendario FBref via
    `game_id`: parsare la stringa funzionerebbe finche' un nome squadra non
    contiene un trattino, e allora smetterebbe in silenzio.
    """
    from ..normalize import load_name_map, load_raw

    path = config.RAW / "fbref_player_match.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path.name} assente: lancia 'python ingest.py --stage player_stats'"
        )
    df = pd.read_parquet(path)
    # `apply_name_map` pretende home_team/away_team, che qui non ci sono: la
    # squadra sta in `team` e si mappa a mano.
    df["team"] = df["team"].replace(load_name_map())

    cal = load_raw("fbref_schedule")[["game_id", "date"]].drop_duplicates("game_id")
    df = df.merge(cal, on="game_id", how="left")
    senza_data = df["date"].isna().sum()
    if senza_data:
        log.warning("%d righe senza data dal calendario: escluse", senza_data)
        df = df.dropna(subset=["date"])

    col_min = _colonna(df, "min", "playing_time_min", "minutes")
    col_gol = _colonna(df, "performance_gls", "gls")
    col_ass = _colonna(df, "performance_ast", "ast")

    out = pd.DataFrame({
        "season": df["season"].astype(str),
        "team": df["team"],
        "player": df["player"],
        "date": pd.to_datetime(df["date"]),
        "minuti": pd.to_numeric(df[col_min], errors="coerce").fillna(0.0),
        "ga": (pd.to_numeric(df[col_gol], errors="coerce").fillna(0.0)
               + pd.to_numeric(df[col_ass], errors="coerce").fillna(0.0)),
    })
    out["chiave_giocatore"] = normalizza_nome(out["player"])
    log.info("giocatori: %d righe, stagioni %s, colonne usate (%s, %s, %s)",
             len(out), sorted(out["season"].unique()), col_min, col_gol, col_ass)
    return out


# ---------------------------------------------------------------------------
# Lo stato della rosa PRIMA di ogni partita
# ---------------------------------------------------------------------------

def pesi_alla_data(giocatori: pd.DataFrame) -> pd.DataFrame:
    """
    Minuti e xG+xA accumulati da ogni giocatore PRIMA di ciascuna data.

    Il cumulato si sposta di una partita (`shift`): alla riga della partita
    del 15 ottobre corrisponde lo stato al 14. E' la stessa disciplina di
    `features/form.py` — si scrive lo stato prima, si aggiorna dopo — ed e'
    quella che rende la feature calcolabile a T-24h.

    Si lavora su una matrice date x giocatori per ogni coppia (stagione,
    squadra): ~120 coppie da 38 date e 30 giocatori, quindi il costo e'
    trascurabile e i confini non si sbagliano.
    """
    pezzi = []
    for (stagione, squadra), g in giocatori.groupby(["season", "team"], sort=False):
        pivot_min = g.pivot_table(index="date", columns="chiave_giocatore",
                                  values="minuti", aggfunc="sum").sort_index()
        pivot_xga = g.pivot_table(index="date", columns="chiave_giocatore",
                                  values="ga", aggfunc="sum").reindex(
                                      index=pivot_min.index,
                                      columns=pivot_min.columns).fillna(0.0)
        cum_min = pivot_min.fillna(0.0).cumsum().shift(1).fillna(0.0)
        cum_xga = pivot_xga.cumsum().shift(1).fillna(0.0)

        lungo = (cum_min.stack().rename("minuti_ad")
                 .to_frame()
                 .join(cum_xga.stack().rename("ga_ad"))
                 .reset_index())
        lungo["season"], lungo["team"] = stagione, squadra
        pezzi.append(lungo)

    out = pd.concat(pezzi, ignore_index=True)
    out = out[out["minuti_ad"] > 0]
    log.info("stato rosa: %d righe (giocatore x data) con minuti alla data",
             len(out))
    return out


# ---------------------------------------------------------------------------

def build(df: pd.DataFrame | None = None, save: bool = True,
          assenze: pd.DataFrame | None = None,
          giocatori: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Una riga per partita, con la quota di rosa indisponibile per lato.

    Le partite fuori dal perimetro coperto dallo scraping restano a NaN, non a
    zero: LightGBM tratta i NaN come "non so", mentre uno zero direbbe
    "squadra al completo" — che e' un dato falso, e il modello lo imparerebbe
    come tale.

    CHI E' FUORI DA AGOSTO PESA ZERO, ED E' VOLUTO. Un giocatore infortunato
    da inizio stagione non ha minuti alla data, quindi non entra nella quota.
    Misurato: l'88.9% degli assenti compare in rosa prima o poi, e il 10.3%
    che non compare mai sono lungodegenti veri — Deulofeu 38 partite di fila,
    Rog 38, Soumaoro 36 — non nomi sbagliati.

    La scelta e' deliberata: questa feature misura il contributo perso di
    RECENTE, non il valore assoluto della rosa mancante. Una squadra senza il
    suo trequartista da sei mesi si e' gia' riorganizzata e il mercato l'ha
    prezzato da un pezzo; e' l'assenza nuova che il mercato puo' non aver
    ancora digerito. Se il blocco non dara' segnale, la prima variante da
    provare e' pesare i lungodegenti con i minuti della stagione PRECEDENTE —
    che e' informazione nota e quindi lecita.
    """
    if df is None:
        df = pd.read_parquet(config.INTERIM / "matches_master.parquet")
        log.info("caricato matches_master: %d righe", len(df))

    df = df.reset_index(drop=True)
    base = df[KEYS + ["date"]].copy()
    # LE DATE VANNO NORMALIZZATE A MEZZANOTTE. `matches_master.date` porta
    # l'ora del calcio d'inizio (12:00 sulle stagioni vecchie, 19:45 sulle
    # recenti), le date di FBref no. Un merge esatto fra le due fallisce sul
    # 100% delle righe senza sollevare niente: si ottengono NaN dappertutto e
    # sembra che gli assenti non si aggancino ai giocatori. E' successo.
    base["date"] = pd.to_datetime(base["date"]).dt.normalize()
    base["season"] = base["season"].astype(str)

    # Le due fonti si possono iniettare: servono ai test, che devono poter
    # costruire il caso limite (date con l'ora, giocatori senza storico)
    # senza dipendere da 12000 righe scaricate.
    if assenze is None:
        assenze = carica_assenze()
    if giocatori is None:
        giocatori = carica_giocatori()
    stato = pesi_alla_data(giocatori)

    # Quanto vale l'intera rosa alla data: il denominatore delle due quote.
    totali = stato.groupby(["season", "team", "date"], as_index=False).agg(
        minuti_rosa=("minuti_ad", "sum"), ga_rosa=("ga_ad", "sum"))

    # Gli assenti, con il peso che avevano PRIMA della partita.
    #
    # `merge_asof` e non un merge esatto sulla data: le due fonti possono
    # datare la stessa partita a un giorno di distanza (un rinvio, una
    # partita di mezzanotte), e un merge esatto in quel caso restituisce NaN
    # in silenzio. Con l'asof all'indietro si prende lo stato dell'ultima
    # partita PRECEDENTE, che e' esattamente la definizione della feature:
    # sbagliare data di un giorno degrada la precisione, non la validita'.
    assenti = assenze[assenze["indisponibile"]].merge(
        base[KEYS + ["date"]], on=KEYS, how="inner")
    assenti = pd.merge_asof(
        assenti.sort_values("date"),
        stato.sort_values("date"),
        on="date", by=["season", "team", "chiave_giocatore"],
        direction="backward",
    )

    agganciati = assenti["minuti_ad"].notna()
    if len(assenti):
        log.info("assenti agganciati alle statistiche: %d/%d (%.1f%%)",
                 int(agganciati.sum()), len(assenti), 100 * agganciati.mean())
        if agganciati.mean() < 0.7:
            log.warning("sotto il 70%%: la quota sara' sottostimata")

    persi = assenti.groupby(["season", "team", "date"], as_index=False).agg(
        minuti_persi=("minuti_ad", "sum"), ga_perso=("ga_ad", "sum"),
        n_assenti=("chiave_giocatore", "count"))

    quote = totali.merge(persi, on=["season", "team", "date"], how="left")
    for c in ("minuti_persi", "ga_perso", "n_assenti"):
        quote[c] = quote[c].fillna(0.0)
    quote["quota_minuti_assenti"] = quote["minuti_persi"] / quote["minuti_rosa"]
    quote["quota_ga_assente"] = np.where(
        quote["ga_rosa"] > 0, quote["ga_perso"] / quote["ga_rosa"], np.nan)

    # Solo le partite davvero interrogate: dove lo scraping non e' arrivato la
    # quota deve restare NaN, non zero.
    coperte = set(zip(assenze["season"], assenze["home_team"], assenze["away_team"]))

    out = base.copy()
    colonne_q = ["quota_minuti_assenti", "quota_ga_assente", "n_assenti"]
    for lato in ("home", "away"):
        q = quote.rename(columns={"team": f"{lato}_team"})[
            ["season", f"{lato}_team", "date"] + colonne_q]
        # Stesso motivo di sopra: asof, non merge esatto sulla data.
        out = pd.merge_asof(
            out.sort_values("date"), q.sort_values("date"),
            on="date", by=["season", f"{lato}_team"], direction="backward",
        ).rename(columns={c: f"{lato}_{c}" for c in colonne_q})

    dentro = [(s, h, a) in coperte
              for s, h, a in zip(out["season"], out["home_team"], out["away_team"])]
    fuori = ~np.array(dentro)
    for c in FEATURES_PLAYERS:
        if c in out.columns:
            out.loc[fuori, c] = np.nan

    for base_col in ("quota_minuti_assenti", "quota_ga_assente", "n_assenti"):
        out[f"diff_{base_col}"] = out[f"home_{base_col}"] - out[f"away_{base_col}"]

    coperto = out["home_quota_minuti_assenti"].notna().mean()
    log.info("copertura: %.1f%% delle partite ha la quota di assenti",
             100 * coperto)

    if save:
        dst = config.PROCESSED / "features_players.parquet"
        out.to_parquet(dst, index=False)
        log.info("scritto %s: %d righe, %d colonne", dst.name, len(out), out.shape[1])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Blocco B: peso delle assenze")
    ap.add_argument("--no-save", action="store_true",
                    help="calcola senza scrivere features_players.parquet")
    args = ap.parse_args()

    pd.set_option("display.width", 200)
    out = build(save=not args.no_save)

    colonne = [c for c in FEATURES_PLAYERS if c in out.columns]
    print("\n=== DISTRIBUZIONE (solo partite coperte) ===")
    print(out[colonne].describe().T.round(3).to_string())

    q = out["home_quota_ga_assente"]
    coperte = out.dropna(subset=["home_quota_ga_assente"])
    if not coperte.empty:
        massimo = coperte[["home_quota_ga_assente", "away_quota_ga_assente"]].max(axis=1)
        print("\n=== SOGLIA DEL PIANO: 15% del contributo atteso ===")
        for soglia in (0.05, 0.10, 0.15, 0.20, 0.30):
            print(f"  almeno una squadra oltre {soglia:.0%}: "
                  f"{(massimo > soglia).mean():>6.1%}")


if __name__ == "__main__":
    main()
