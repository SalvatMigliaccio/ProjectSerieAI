"""
Ingestion multi-fonte per il dataset Serie A.

Scarica i dati grezzi da tutte le fonti e li salva in Parquet dentro data/raw/.
Ogni stage e' indipendente e ri-eseguibile: soccerdata mantiene una cache
locale persistente (~/soccerdata/data/), quindi rilanciare uno stage gia'
completato non ri-scarica nulla.

    goalmodel ingest --stage matches      # veloce,  ~10 secondi
    goalmodel ingest --stage understat    # veloce,  ~2 minuti
    goalmodel ingest --stage elo          # veloce,  ~1 minuto
    goalmodel ingest --stage schedule     # medio,   ~5 minuti
    goalmodel ingest --stage lineups      # LENTO,   ore
    goalmodel ingest --stage missing      # LENTO,   richiede un browser
    goalmodel ingest --stage all
    goalmodel ingest --stage all --no-parallel

PARALLELISMO: FRA GLI HOST, MAI DENTRO UNO
Con `--stage all` gli stage che contattano server DIVERSI girano insieme;
quelli sullo stesso server restano in fila. Non e' una cautela generica, e'
aritmetica: soccerdata dorme `rate_limit` secondi dopo ogni richiesta, nel
thread chiamante, e quel valore vale 7 secondi per FBref e 5 per WhoScored.
Due istanze FBref in due thread dormono in parallelo e raddoppiano le
richieste al secondo viste dal server — cioe' aggirano il limite invece di
rispettarlo. `--no-parallel` torna all'esecuzione in sequenza.

Quanto si guadagna, onestamente: sui tre stage di avvio (matches, understat,
schedule) la somma diventa il massimo, ~7 minuti scendono a ~5. Sugli stage
pesanti NON si guadagna niente, perche' sono tutti su FBref e restano in fila
fra loro. Le ore stanno nel limite di frequenza, e quello non si tocca.

ATTENZIONE AI TEMPI: gli stage 'lineups' e 'player_stats' fanno una richiesta
per partita. Su 4580 partite sono 4580 x 7 secondi, cioe' quasi nove ore di
sola attesa. Lanciali di notte. La cache rende l'operazione incrementale,
quindi si puo' interrompere e riprendere.

WhoScored (stage 'missing') usa Selenium e richiede un browser installato.
Se non serve subito, si salta: il modello T-24h parte senza.
"""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import soccerdata as sd

from . import config

try:  # la barra e' un di piu': senza tqdm tutto funziona identico
    from tqdm import tqdm as _TQDM
except ImportError:  # pragma: no cover
    _TQDM = None

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
# Leghe, stagioni e percorsi vengono da src/config.py: unica fonte di verita'.
# Per cambiare perimetro modifica quel file, non questo.

LEAGUE = config.LEAGUES
SEASONS = config.SEASONS
RAW = config.RAW

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("ingest")


def save(df: pd.DataFrame, name: str) -> None:
    """Salva in Parquet resettando l'indice (soccerdata usa MultiIndex)."""
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{name}.parquet"
    df.reset_index().to_parquet(out, index=False)
    log.info("%-22s %6d righe, %2d colonne -> %s",
             name, len(df), df.shape[1], out)


# ---------------------------------------------------------------------------
# Stage 1 - Risultati e quote (football-data.co.uk)
# ---------------------------------------------------------------------------

def ingest_matches() -> pd.DataFrame:
    """
    Base del dataset: risultato finale e primo tempo, statistiche di partita
    (tiri, tiri in porta, corner, falli, cartellini), arbitro, e le quote
    1X2 di una decina di bookmaker piu' le quote di chiusura medie e massime.

    NOTA SUL LEAKAGE: le colonne HS/AS/HST/AST/HC/AC/HF/AF/HY/AY/HR/AR sono
    statistiche POST-partita. Servono per costruire medie mobili storiche,
    MAI come feature della riga stessa.

    Le quote (B365H/D/A, PSH/D/A, ...) sono legittime come feature perche'
    note prima del fischio d'inizio, ma vedi la discussione: se vuoi misurare
    se il modello aggiunge informazione al mercato, tienile fuori e usale
    solo come benchmark.
    """
    mh = sd.MatchHistory(leagues=LEAGUE, seasons=SEASONS)
    df = mh.read_games()
    save(df, "matches")
    return df


# ---------------------------------------------------------------------------
# Stage 1b - Quote delle partite in arrivo (football-data.co.uk/fixtures.csv)
# ---------------------------------------------------------------------------

def season_from_date(d: pd.Timestamp) -> str:
    """
    Stagione soccerdata a quattro cifre dalla data della partita.

    Il file dei fixture non dice a quale stagione appartenga: contiene solo il
    turno imminente. Si deduce dal mese, con lo stacco a luglio — nessun
    campionato dei Big 5 gioca partite di lega in quel mese.
    Non si usa `config.CURRENT_SEASON` perche' andrebbe aggiornato a mano ogni
    agosto, ed e' esattamente il tipo di dettaglio che si dimentica.
    """
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _leggi_csv(src: str | Path) -> pd.DataFrame:
    """
    Legge il CSV da rete con header da browser, o da file locale.

    Gli header servono perche' alcuni server rifiutano le richieste che non
    sembrano un browser. Non e' il caso di football-data — provato: risponde
    503 anche con header completi mentre altri host rispondono 200, quindi il
    blocco e' verso l'ambiente, non verso l'User-Agent — ma costa una riga e
    toglie una variabile quando il download fallisce.
    """
    src = str(src)
    if not src.startswith(("http://", "https://")):
        return pd.read_csv(src, encoding="latin-1")

    import urllib.error
    import urllib.request

    req = urllib.request.Request(src, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/131.0.0.0 Safari/537.36"),
        "Accept": "text/csv,text/plain,*/*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Referer": "https://www.football-data.co.uk/matches.php",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            dati = resp.read()
    except urllib.error.HTTPError as exc:
        raise ConnectionError(
            f"{src} ha risposto {exc.code}. Il sito e' spesso irraggiungibile "
            f"dai programmi pur funzionando dal browser: scaricalo a mano e usa "
            f"'goalmodel ingest --stage fixtures --fixtures-file <percorso>'."
        ) from exc

    import io
    return pd.read_csv(io.BytesIO(dati), encoding="latin-1")


def _parse_fixture_dates(df: pd.DataFrame) -> pd.Series:
    """
    Data e ora di football-data in un unico timestamp.

    Il formato e' britannico (giorno/mese) e l'anno compare sia a due sia a
    quattro cifre a seconda dell'annata: `dayfirst=True` copre entrambi. Senza
    di esso il 05/09 diventerebbe il 9 maggio, e l'errore passerebbe
    silenziosamente perche' e' comunque una data valida.
    """
    date = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    if "Time" in df.columns:
        delta = pd.to_timedelta(df["Time"].astype("string") + ":00", errors="coerce")
        date = date + delta.fillna(pd.Timedelta(0))
    return date


def ingest_fixtures(source: str | Path | None = None) -> pd.DataFrame:
    """
    Quote di apertura delle partite del turno imminente.

    ATTENZIONE, IL FILE E' UNA FINESTRA, NON UN ARCHIVIO: contiene solo le
    partite dei prossimi giorni e viene sovrascritto. Se non lo si scarica in
    tempo, quelle quote non si recuperano piu' da qui.

    Le colonne vengono rinominate sullo schema di `matches.parquet` — league,
    home_team, away_team, date — perche' tutto il progetto si aggancia su
    quella quadrupla. Le colonne delle quote restano com'erano: sono le stesse
    identiche di football-data, ed e' cio' che garantisce che in produzione si
    usino le stesse quote dell'addestramento.
    """
    src = source or config.FIXTURES_URL
    downloaded_at = pd.Timestamp.now(tz="UTC")
    log.info("scarico %s", src)
    raw = _leggi_csv(src)
    # Un BOM in testa al file rinomina silenziosamente la prima colonna in
    # '﻿Div' e fa fallire tutto con un KeyError incomprensibile.
    raw.columns = [str(c).lstrip("﻿").lstrip("ï»¿").strip() for c in raw.columns]

    mancanti = [c for c in ("Div", "Date", "HomeTeam", "AwayTeam") if c not in raw.columns]
    if mancanti:
        raise ValueError(
            f"{src} non ha le colonne attese {mancanti}. Trovate: "
            f"{list(raw.columns)[:12]}. Il formato di football-data e' cambiato?"
        )

    log.info("fixtures.csv: %d righe, %d colonne, campionati %s",
             len(raw), raw.shape[1], sorted(raw["Div"].dropna().unique()))

    wanted = {k: v for k, v in config.FOOTBALL_DATA_DIV.items() if v in LEAGUE}
    df = raw[raw["Div"].isin(wanted)].copy()
    if df.empty:
        log.warning("nessuna partita per %s: il turno non e' ancora pubblicato "
                    "oppure i codici Div sono cambiati", LEAGUE)

    df["league"] = df["Div"].map(wanted)
    df["date"] = _parse_fixture_dates(df)
    df = df.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team"})
    df = df.dropna(subset=["date", "home_team", "away_team"])
    df["season"] = df["date"].map(season_from_date)
    df["downloaded_at"] = downloaded_at

    # Nomi squadra sulla convenzione del progetto, PRIMA di qualsiasi verifica.
    from .normalize import apply_name_map, load_name_map
    df = apply_name_map(df, load_name_map())

    _check_fixture_teams(df)

    keep = ["league", "season", "date", "home_team", "away_team", "downloaded_at"]
    odds = [c for c in df.columns if c not in keep and c not in ("Div", "Date", "Time")]
    out = df[keep + odds].sort_values("date").reset_index(drop=True)

    RAW.mkdir(parents=True, exist_ok=True)
    dst = RAW / "fixtures_odds.parquet"
    out.to_parquet(dst, index=False)
    log.info("fixtures_odds        %6d righe, %2d colonne -> %s", len(out), out.shape[1], dst)
    if len(out):
        log.info("turno coperto: dal %s al %s (snapshot %s)",
                 out["date"].min().date(), out["date"].max().date(),
                 downloaded_at.strftime("%Y-%m-%d %H:%M UTC"))
        b365 = [c for c in ("B365H", "B365D", "B365A") if c in out.columns]
        if len(b365) == 3:
            pieno = out[b365].notna().all(axis=1).sum()
            log.info("quote B365 1X2 complete su %d/%d partite", pieno, len(out))
        else:
            log.warning("colonne B365 1X2 assenti dal file: %s", b365)
    return out


def _check_fixture_teams(df: pd.DataFrame) -> None:
    """
    Ogni squadra del file deve essere gia' nota a matches_master.

    Un nome non riconosciuto non e' un dettaglio cosmetico: la partita non si
    aggancerebbe alla quadrupla di join e sparirebbe senza rumore dalla
    previsione. Meglio un avviso esplicito con il nome esatto da aggiungere a
    team_name_map.json.
    """
    master = config.INTERIM / "matches_master.parquet"
    if not master.exists():
        log.warning("%s assente: salto la verifica dei nomi squadra", master.name)
        return

    noti = pd.read_parquet(master, columns=["home_team", "away_team"])
    noti = set(noti["home_team"]) | set(noti["away_team"])
    presenti = set(df["home_team"]) | set(df["away_team"])
    sconosciute = sorted(presenti - noti)

    if sconosciute:
        log.warning("=" * 70)
        log.warning("%d squadre del file fixture NON riconosciute: %s",
                    len(sconosciute), sconosciute)
        log.warning("Le loro partite non si aggancerebbero e sparirebbero dalla")
        log.warning("previsione senza errore. Aggiungi la corrispondenza in")
        log.warning("%s e rilancia questo stage.", config.TEAM_NAME_MAP)
        log.warning("=" * 70)
    else:
        log.info("nomi squadra: tutte %d riconosciute", len(presenti))


# ---------------------------------------------------------------------------
# Stage 2 - xG per squadra/partita (Understat)
# ---------------------------------------------------------------------------

def ingest_understat() -> pd.DataFrame:
    """
    xG e xGA per squadra e per partita. Questa e' la fonte piu' importante
    del progetto dopo i risultati: l'xG e' molto meno rumoroso dei gol e
    predice i risultati futuri meglio dei gol passati.

    Understat espone JSON, quindi e' veloce (nessun rate limit aggressivo).
    """
    us = sd.Understat(leagues=LEAGUE, seasons=SEASONS)
    schedule = us.read_schedule()
    save(schedule, "understat_schedule")

    team_stats = us.read_team_match_stats()
    save(team_stats, "understat_team_match")
    return team_stats


def ingest_understat_shots() -> pd.DataFrame:
    """
    Eventi tiro con coordinate e xG per singolo tiro. Opzionale in fase 1,
    ma abilita metriche piu' fini (xG non-rigore, qualita' media del tiro,
    xG concesso da situazione di gioco vs palla inattiva).
    Piu' lento: una richiesta per partita.
    """
    us = sd.Understat(leagues=LEAGUE, seasons=SEASONS)
    shots = us.read_shot_events()
    save(shots, "understat_shots")
    return shots


# ---------------------------------------------------------------------------
# Stage 3 - Calendario e statistiche avanzate (FBref)
# ---------------------------------------------------------------------------

def ingest_schedule() -> pd.DataFrame:
    """
    Calendario FBref: fornisce il match_id necessario a tutti gli stage
    successivi (formazioni, statistiche giocatore, eventi).
    """
    fb = sd.FBref(leagues=LEAGUE, seasons=SEASONS)
    df = fb.read_schedule()
    save(df, "fbref_schedule")
    return df


def registra_coppe() -> list[str]:
    """
    Aggiunge le coppe UEFA al dizionario leghe di soccerdata, a runtime.

    Sono chiavi NUOVE: non sovrascrivono nessuna lega esistente. Si fa qui e
    non in `~/soccerdata/config/league_dict.json` per la stessa ragione del
    locale WhoScored — quel file e' fuori dal progetto, non e' versionato, e
    chi clona il repository si ritroverebbe uno stage che fallisce senza
    capire perche'.
    """
    from soccerdata._config import LEAGUE_DICT

    # LEAGUE_DICT e' globale in soccerdata e con gli stage in parallelo
    # `cups` e `missing` possono mutarlo insieme.
    with _LOCK_LEGHE:
        for chiave, nome in config.FBREF_CUPS.items():
            LEAGUE_DICT.setdefault(chiave, {
                "FBref": nome, "season_start": "Aug", "season_end": "May",
            })
    return list(config.FBREF_CUPS)


def ingest_cups() -> pd.DataFrame:
    """
    Calendario di Champions, Europa e Conference League.

    Serve al blocco A: senza, la congestione da coppa infrasettimanale e'
    invisibile, perche' il calendario di campionato non cambia quando una
    squadra gioca il martedi'. E' ingestion di solo calendario — una richiesta
    per competizione e stagione, niente browser.

    SI ISOLA PER (COMPETIZIONE, STAGIONE), NON PER COMPETIZIONE. La versione
    precedente chiedeva tutte le stagioni di una coppa in una chiamata sola e
    catturava l'errore per competizione. Sembra abbastanza, e non lo e': la
    Conference League non esiste prima del 2021/22, soccerdata solleva
    `KeyError: '1415'` invece di restituire vuoto, e l'eccezione si portava via
    l'INTERA competizione — comprese le stagioni che c'erano.

    Non e' un'ipotesi: e' successo. Il file conteneva 4140 partite di sola
    Champions (1766) ed Europa (2374), zero di Conference, e il numero 4140
    era finito nella documentazione come se le coppe fossero tre. Le stagioni
    2021/22 in poi della Conference non sono mai state scaricate, e sono
    proprio quelle che si sovrappongono al test set.

    Il numero di richieste non cambia: soccerdata ne fa comunque una per
    stagione. Cambia solo dove si mette il `try`.
    """
    coppe = registra_coppe()
    pezzi = []
    for coppa in barra(coppe, "coppe"):
        righe_coppa = 0
        stagioni_ok = 0
        for stagione in SEASONS:
            try:
                fb = sd.FBref(leagues=[coppa], seasons=[stagione])
                df = fb.read_schedule().reset_index()
            except Exception as exc:
                # Una stagione in cui la competizione non esisteva ancora e'
                # NORMALE (la Conference prima del 2021/22): si annota a
                # livello debug e si va avanti.
                log.debug("%s %s non disponibile: %s", coppa, stagione,
                          type(exc).__name__)
                continue
            if df.empty:
                continue
            pezzi.append(df.assign(competition=coppa))
            righe_coppa += len(df)
            stagioni_ok += 1
        if righe_coppa:
            log.info("%-26s %5d partite su %d stagioni",
                     coppa, righe_coppa, stagioni_ok)
        else:
            log.warning("%s: nessuna stagione disponibile", coppa)

    if not pezzi:
        raise RuntimeError("nessuna coppa scaricata")
    out = pd.concat(pezzi, ignore_index=True)
    RAW.mkdir(parents=True, exist_ok=True)
    dst = RAW / "fbref_cups_schedule.parquet"
    out.to_parquet(dst, index=False)
    log.info("%-22s %6d righe, %2d colonne -> %s",
             "fbref_cups_schedule", len(out), out.shape[1], dst)
    return out


def ingest_team_stats() -> None:
    """
    Statistiche avanzate di squadra per partita, dai dati Opta/StatsPerform
    con cui FBref e' in partnership. Molto piu' ricche dei tiri/corner base.

    stat_type disponibili: 'schedule', 'shooting', 'keeper', 'passing',
    'passing_types', 'goal_shot_creation', 'defense', 'possession', 'misc'.

    opponent_stats=True restituisce le stesse metriche dal punto di vista
    dell'avversario, utile per costruire la coppia (prodotto, subito).
    """
    fb = sd.FBref(leagues=LEAGUE, seasons=SEASONS)
    tipi = ["shooting", "possession", "passing", "defense"]
    for stat_type in barra(tipi, "team stats"):
        try:
            df = fb.read_team_match_stats(stat_type=stat_type)
            save(df, f"fbref_team_{stat_type}")
        except Exception as exc:  # copertura incompleta su stagioni vecchie
            log.warning("team stats '%s' non disponibile: %s", stat_type, exc)


# ---------------------------------------------------------------------------
# Stage 4 - Layer giocatori
# ---------------------------------------------------------------------------

def ingest_lineups() -> pd.DataFrame:
    """
    Formazioni: chi era in campo, titolare o subentrato, con i minuti.

    E' la base per il layer giocatori:
      - quota minuti stagionali degli assenti (feature B)
      - forza dell'XI schierato aggregando rating individuali (feature C)
      - indice di turnover: quanti titolari cambiati rispetto alla partita
        precedente (segnale forte nelle settimane di coppa)

    LENTO: una richiesta HTTP per partita.
    """
    fb = sd.FBref(leagues=LEAGUE, seasons=SEASONS)
    df = fb.read_lineup()
    save(df, "fbref_lineups")
    return df


def ingest_player_stats(seasons: list[str] | None = None) -> pd.DataFrame:
    """
    Statistiche per giocatore e per partita: minuti, gol, assist, xG, xA.

    E' la base del peso delle assenze. Serve il PER PARTITA e non l'aggregato
    stagionale: i minuti di fine stagione contengono quelli giocati DOPO la
    partita da predire, e usarli sarebbe leakage. Con il per partita si
    calcolano i minuti alla data, che e' l'unica cosa lecita.

    MOLTO LENTO: una richiesta per partita. Riprendibile — si salva a ogni
    stagione e al rilancio si riparte da dove si era arrivati.

    Le colonne arrivano con un MultiIndex e una colonna `age` che mescola
    interi e stringhe tipo `25-182`: si appiattisce e si converte, altrimenti
    il parquet non si scrive e si perde tutto il lavoro sull'ultima riga.
    """
    seasons = seasons or SEASONS
    dst = RAW / "fbref_player_match.parquet"
    pezzi = [pd.read_parquet(dst)] if dst.exists() else []
    fatte = set(pezzi[0]["season"].astype(str)) if pezzi else set()
    if fatte:
        log.info("stagioni gia' scaricate: %s", sorted(fatte))

    for stagione in barra(seasons, "stagioni"):
        if str(stagione) in fatte:
            continue
        try:
            fb = sd.FBref(leagues=LEAGUE, seasons=[stagione])
            df = fb.read_player_match_stats(stat_type="summary").reset_index()
            df.columns = [
                "_".join(str(p) for p in c
                         if p and not str(p).startswith("Unnamed")).strip("_")
                if isinstance(c, tuple) else str(c)
                for c in df.columns
            ]
            df.columns = [c.lower().replace(" ", "_").replace("+", "_plus_")
                          for c in df.columns]
            for c in df.columns:
                if df[c].dtype == object:
                    df[c] = df[c].astype("string")
            pezzi.append(df)
            pd.concat(pezzi, ignore_index=True).to_parquet(dst, index=False)
            log.info("%s: %d righe giocatore-partita", stagione, len(df))
        except Exception as exc:
            log.warning("%s: %s — proseguo con la prossima stagione",
                        stagione, type(exc).__name__)

    out = pd.concat(pezzi, ignore_index=True) if pezzi else pd.DataFrame()
    log.info("%-22s %6d righe -> %s", "fbref_player_match", len(out), dst)
    return out


def applica_locale_whoscored() -> None:
    """
    Rimette a posto i nomi di regione che WhoScored serve tradotti.

    WhoScored localizza le pagine sulla geolocalizzazione di chi chiama: da un
    IP italiano la regione e' "Italia", non "Italy", e la mappa di soccerdata
    non aggancia piu' niente. L'errore che ne esce — `None of [Index(['ITA-Serie
    A'])] are in the [index]` — sembra una lega non supportata, e manda a
    cercare nel posto sbagliato.

    Si tocca SOLO il campo WhoScored: il file di configurazione globale
    (`~/soccerdata/config/league_dict.json`) rimpiazzerebbe l'intera voce,
    perche' soccerdata fa `dict.update` al primo livello, e porterebbe via
    anche le mappe di FBref, Understat e MatchHistory.
    """
    from soccerdata._config import LEAGUE_DICT

    from . import whoscored_patch

    with _LOCK_LEGHE:
        for chiave, nome in config.WHOSCORED_LEAGUE_OVERRIDE.items():
            if chiave in LEAGUE_DICT:
                LEAGUE_DICT[chiave]["WhoScored"] = nome
                log.info("WhoScored: '%s' -> '%s'", chiave, nome)
        whoscored_patch.applica()


def ingest_missing(seasons: list[str] | None = None) -> pd.DataFrame:
    """
    Infortunati e squalificati per singola partita, da WhoScored.

    E' il pezzo chiave del blocco B: sono informazioni note PRIMA della
    formazione ufficiale, quindi dentro l'orizzonte T-24h. E' anche l'unico
    posto dove il mercato puo' essere strutturalmente lento — il calendario si
    sa da mesi, un infortunio di giovedi' no.

    RICHIEDE UN BROWSER (Selenium + Chrome) ED E' LENTO: ~11 secondi per
    partita, misurati. Sei stagioni sono circa sette ore.

    UNA STAGIONE PER VOLTA, CON PAUSA. Chiedere tutte le stagioni in una sola
    sessione fa scattare il captcha di WhoScored, e in headless il risolutore
    non puo' nemmeno comparire: l'errore che ne esce e' un `IndexError` dentro
    soccerdata, che non somiglia per niente a "sei stato bloccato". Con
    un'istanza per stagione e una pausa in mezzo il problema non si presenta.

    SALVA A OGNI STAGIONE. Sette ore di scraping non si rifanno per un errore
    all'ultima riga, e il file gia' scritto viene riletto e completato: si puo'
    interrompere e riprendere quando si vuole.
    """
    import time

    applica_locale_whoscored()
    seasons = seasons or SEASONS
    dst = RAW / "whoscored_missing.parquet"
    viste_path = RAW / "whoscored_missing_viste.parquet"

    pezzi = [pd.read_parquet(dst)] if dst.exists() else []
    viste = [pd.read_parquet(viste_path)] if viste_path.exists() else []
    fatte = set(viste[0]["game_id"].astype(int)) if viste else set()
    if fatte:
        log.info("gia' scaricate %d partite: le salto", len(fatte))

    for stagione in barra(seasons, "stagioni"):
        try:
            ws = sd.WhoScored(leagues=LEAGUE, seasons=[stagione], headless=True)
            sched = ws.read_schedule().reset_index()
        except Exception as exc:
            log.warning("%s: calendario non disponibile (%s), stagione saltata",
                        stagione, type(exc).__name__)
            continue

        ids = [i for i in sched["game_id"].dropna().astype(int) if i not in fatte]
        if not ids:
            continue
        t0 = time.time()
        try:
            miss = ws.read_missing_players(match_id=ids).reset_index()
            pezzi.append(miss)
            pd.concat(pezzi, ignore_index=True).to_parquet(dst, index=False)
            # Le partite senza assenti non producono righe: senza registrare
            # QUALI sono state interrogate non si distingue "nessun assente"
            # da "non scaricata", e ogni statistica a valle sarebbe calcolata
            # sulle sole partite con assenze.
            viste.append(pd.DataFrame({"season": stagione, "game_id": ids}))
            pd.concat(viste, ignore_index=True).to_parquet(viste_path, index=False)
            log.info("%s: %d partite -> %d assenti (%.0f min)",
                     stagione, len(ids), len(miss), (time.time() - t0) / 60)
        except Exception as exc:
            log.warning("%s: %s — proseguo con la prossima stagione",
                        stagione, type(exc).__name__)
        time.sleep(20)

    out = pd.concat(pezzi, ignore_index=True) if pezzi else pd.DataFrame()
    log.info("%-22s %6d righe -> %s", "whoscored_missing", len(out), dst)
    return out


# ---------------------------------------------------------------------------
# Stage 5 - Rating Elo (ClubElo)
# ---------------------------------------------------------------------------

def ingest_elo(schedule_path: Path = RAW / "fbref_schedule.parquet") -> pd.DataFrame:
    """
    Storico Elo giornaliero per ogni squadra apparsa in Serie A nel periodo.

    Perche' scaricarlo invece di calcolarlo? Perche' l'Elo di ClubElo tiene
    conto anche delle coppe europee: cattura il fatto che il Napoli affronta
    avversari di livello diverso rispetto a chi gioca solo il campionato.
    Poi calcolerai comunque anche un Elo tuo, tarato sulla sola Serie A, e
    userai entrambi.

    read_team_history() restituisce la serie storica completa per squadra:
    una richiesta per squadra invece di una per data. Molto piu' efficiente.
    """
    if not schedule_path.exists():
        raise FileNotFoundError(
            f"{schedule_path} mancante: lancia prima --stage schedule"
        )

    sched = pd.read_parquet(schedule_path)
    teams = sorted(set(sched["home_team"]) | set(sched["away_team"]))
    log.info("Squadre trovate: %d", len(teams))

    elo = sd.ClubElo()
    frames = []
    consecutive_failures = 0

    for team in barra(teams, "squadre"):
        try:
            hist = elo.read_team_history(team).reset_index()
            hist["team"] = team
            frames.append(hist)
            consecutive_failures = 0
        except Exception as exc:
            # I nomi squadra tra ClubElo e FBref non sempre coincidono: un
            # fallimento isolato e' normale e si mappa a mano. Fallimenti
            # consecutivi su squadre note significano invece che il servizio
            # e' giu' (tipicamente 502), e insistere e' inutile.
            consecutive_failures += 1
            log.warning("Elo non trovato per '%s': %s", team, exc)
            if consecutive_failures >= 3:
                log.error(
                    "3 fallimenti consecutivi su ClubElo: il servizio sembra "
                    "non disponibile. Lo stage 'elo' e' opzionale, riprova piu' "
                    "tardi e prosegui con gli altri."
                )
                break

    if not frames:
        log.error("nessun dato Elo scaricato: stage saltato")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    save(df, "clubelo_history")
    return df


# ---------------------------------------------------------------------------
# Esecuzione degli stage: cosa si puo' parallelizzare, e cosa no
# ---------------------------------------------------------------------------
#
# IL COLLO DI BOTTIGLIA NON E' QUESTA MACCHINA. Misurato su soccerdata 1.9.1:
# dopo OGNI richiesta la libreria esegue `time.sleep(self.rate_limit)` nel
# thread chiamante, e il valore dipende dalla fonte:
#
#     FBref          7 s     una richiesta per partita negli stage pesanti
#     WhoScored      5 s     piu' il caricamento della pagina in Selenium
#     Understat      0 s
#     ClubElo        0 s
#     football-data  0 s
#
# Quindi `player_stats` su 4580 partite costa 4580 x 7 s ~= 8.9 ORE, e sono
# quasi tutte `sleep`. Con venti core e quindici giga liberi, CPU e RAM non
# c'entrano niente.
#
# PERCHE' NON SI PARALLELIZZA DENTRO UN HOST. Quel `sleep` e' PER ISTANZA e
# PER THREAD: due istanze FBref in due thread dormono in parallelo e
# raddoppiano la frequenza delle richieste viste dal server. Non e' un
# aumento di velocita', e' aggirare il limite — e FBref blocca per molto meno.
# Lo stesso vale per WhoScored, dove per giunta ogni worker vuole un browser
# suo (~400 MB) e la pagina serve un captcha quando si insiste (e' gia'
# scritto in `ingest_missing`: una stagione per volta, con pausa).
#
# COSA SI PARALLELIZZA DAVVERO: gli host fra loro. football-data, Understat,
# FBref, ClubElo e WhoScored sono server diversi; farli lavorare insieme non
# aumenta il carico di nessuno di loro. Ogni host resta servito da un flusso
# di richieste seriale, esattamente come prima.
#
# QUANTO SI GUADAGNA, ONESTAMENTE. Sui tre stage di avvio (matches, understat,
# schedule) si passa dalla somma al massimo: ~7 minuti diventano ~5. Sugli
# stage pesanti non si guadagna NIENTE, perche' sono tutti su FBref e restano
# in fila fra loro. Chi cerca le ore deve guardare la cache, non i thread.

# Stage -> host contattato. Due stage sullo stesso host non girano mai insieme.
HOST_DI_STAGE: dict[str, str] = {
    "matches": "football-data",
    "fixtures": "football-data",
    "understat": "understat",
    "shots": "understat",
    "schedule": "fbref",
    "cups": "fbref",
    "team_stats": "fbref",
    "lineups": "fbref",
    "player_stats": "fbref",
    "missing": "whoscored",
    "elo": "clubelo",
}

# Richieste concorrenti ammesse per host. Sono tutte a 1 di proposito: il
# parallelismo sta FRA gli host, non dentro. La mappa esiste comunque esplicita
# perche' e' il posto dove si discute un eventuale cambiamento, e perche' un
# numero scritto si nota, mentre un limite implicito no.
LIMITE_PER_HOST: dict[str, int] = {
    "football-data": 1,
    "understat": 1,
    "fbref": 1,
    "whoscored": 1,
    "clubelo": 1,
}

# `registra_coppe` e `applica_locale_whoscored` mutano LEAGUE_DICT, che e'
# globale in soccerdata. Con gli stage in parallelo possono capitare insieme.
_LOCK_LEGHE = threading.Lock()


def barra(iterabile, descrizione: str, totale: int | None = None):
    """
    Barra di avanzamento, se tqdm c'e' e siamo su un terminale.

    Non e' una dipendenza dura: senza tqdm, o in un log su file, o in CI,
    l'iterabile torna quello che era. Una barra scritta in un file di log
    produce migliaia di righe di controllo e rende il log illeggibile.
    """
    if not _TQDM or not sys.stderr.isatty():
        return iterabile
    return _TQDM(iterabile, desc=descrizione, total=totale,
                 unit="", leave=False, dynamic_ncols=True)


def _esegui_uno(nome: str, fixtures_file: str | None,
                semafori: dict[str, threading.Semaphore]) -> tuple[str, Exception | None]:
    """Uno stage, tenendo occupato il suo host per tutta la durata."""
    sem = semafori[HOST_DI_STAGE.get(nome, nome)]
    with sem:
        log.info("=== stage: %s (host %s) ===", nome, HOST_DI_STAGE.get(nome, "?"))
        inizio = time.monotonic()
        try:
            if nome == "fixtures":
                ingest_fixtures(fixtures_file)
            else:
                STAGES[nome]()
            log.info("=== stage '%s' fatto in %.1f min ===",
                     nome, (time.monotonic() - inizio) / 60)
            return nome, None
        except Exception as exc:
            log.error("stage '%s' fallito dopo %.1f min: %s",
                      nome, (time.monotonic() - inizio) / 60, exc)
            return nome, exc


def esegui(stages: list[str], parallelo: bool = True,
           fixtures_file: str | None = None) -> dict[str, Exception | None]:
    """
    Esegue gli stage richiesti, restituendo l'esito di ciascuno.

    In sequenza (`parallelo=False`) l'ordine e' quello ricevuto, che e' quello
    di `ORDER`: prima i veloci, cosi' si ha subito qualcosa con cui lavorare.

    In parallelo si usano THREAD e non processi: questi stage passano il tempo
    ad aspettare la rete e a dormire dentro `time.sleep`, e in entrambi i casi
    il GIL e' rilasciato. Dei processi costerebbero memoria e serializzazione
    dei DataFrame senza guadagnare niente.

    IL NUMERO DI WORKER E' len(stages), NON il numero di host. Sembra
    sprecato — tanto i semafori ne lasciano passare uno per host — ed e'
    invece l'unica scelta corretta: un worker BLOCCATO su un semaforo occupa
    comunque il suo posto nel pool. Con tre stage su due host e due soli
    worker, il secondo stage di FBref si prende un worker e ci resta fermo,
    e lo stage di WhoScored — il cui host e' libero — aspetta in coda che
    si liberi un posto.

    Non e' teorico: con `--stage cups player_stats missing` avrebbe fatto
    partire le quindici ore di WhoScored DOPO le nove di FBref invece che
    insieme, cioe' ventiquattro ore invece di quindici. I thread fermi su un
    semaforo non costano niente; un host lasciato inattivo costa ore.
    """
    semafori = {h: threading.Semaphore(n) for h, n in LIMITE_PER_HOST.items()}
    for nome in stages:  # uno stage su un host ignoto resta comunque serializzato
        semafori.setdefault(HOST_DI_STAGE.get(nome, nome), threading.Semaphore(1))

    if not parallelo or len(stages) == 1:
        esiti = {}
        for nome in barra(stages, "stage"):
            _, exc = _esegui_uno(nome, fixtures_file, semafori)
            esiti[nome] = exc
        return esiti

    host = {HOST_DI_STAGE.get(n, n) for n in stages}
    log.info("parallelo: %d stage su %d host distinti (%s)",
             len(stages), len(host), ", ".join(sorted(host)))

    esiti: dict[str, Exception | None] = {}
    with ThreadPoolExecutor(max_workers=len(stages), thread_name_prefix="stage") as pool:
        futuri = [pool.submit(_esegui_uno, n, fixtures_file, semafori) for n in stages]
        for fut in barra(as_completed(futuri), "stage", totale=len(futuri)):
            nome, exc = fut.result()
            esiti[nome] = exc
    return esiti


# ---------------------------------------------------------------------------

STAGES = {
    "matches": ingest_matches,
    "fixtures": ingest_fixtures,
    "understat": ingest_understat,
    "shots": ingest_understat_shots,
    "schedule": ingest_schedule,
    "cups": ingest_cups,
    "team_stats": ingest_team_stats,
    "lineups": ingest_lineups,
    "player_stats": ingest_player_stats,
    "missing": ingest_missing,
    "elo": ingest_elo,
}

# Ordine consigliato: prima i veloci, cosi' hai subito qualcosa con cui
# lavorare mentre i lenti girano in background.
ORDER = ["matches", "understat", "fixtures", "schedule", "cups", "elo",
         "team_stats", "lineups", "player_stats", "missing"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        nargs="+",
        choices=list(STAGES) + ["all"],
        default=["matches"],
        metavar="STAGE",
        help="Uno o piu' stage, oppure 'all'. Con piu' stage su host diversi "
             "il parallelismo ha qualcosa da fare: "
             "`--stage matches understat schedule` contatta tre server "
             "distinti e costa quanto il piu' lento invece della somma.",
    )
    parser.add_argument(
        "--fixtures-file",
        help="solo per --stage fixtures: legge da un file locale invece che "
             "dalla rete. Utile quando il sito e' giu' o per riprodurre uno "
             "snapshot gia' scaricato.",
    )
    parser.add_argument(
        "--parallel", dest="parallelo", action=argparse.BooleanOptionalAction,
        default=True,
        help="esegue insieme gli stage che contattano host DIVERSI "
             "(--no-parallel per la vecchia esecuzione in sequenza). Dentro "
             "un host resta sempre seriale: il limite di frequenza di FBref e "
             "WhoScored e' li' per essere rispettato, non aggirato.")
    args = parser.parse_args()

    if "all" in args.stage:
        stages = ORDER
    else:
        # Si toglie il doppione mantenendo l'ordine di ORDER, che mette i
        # veloci per primi: cosi' `--stage schedule matches` non inverte la
        # priorita' solo perche' e' stato scritto in quell'ordine.
        chiesti = set(args.stage)
        stages = [s for s in ORDER if s in chiesti]

    esiti = esegui(stages, parallelo=args.parallelo,
                   fixtures_file=args.fixtures_file)

    falliti = {n: e for n, e in esiti.items() if e is not None}
    if falliti:
        log.error("stage falliti: %s", ", ".join(sorted(falliti)))
        # Con UN solo stage richiesto l'errore e' IL risultato e va propagato.
        # Con piu' stage no: una fonte giu' butterebbe via il lavoro gia'
        # fatto dalle altre, che e' il modo peggiore di fallire.
        if len(stages) == 1:
            raise next(iter(falliti.values()))


if __name__ == "__main__":
    main()
