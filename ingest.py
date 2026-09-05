"""
Ingestion multi-fonte per il dataset Serie A.

Scarica i dati grezzi da tutte le fonti e li salva in Parquet dentro data/raw/.
Ogni stage e' indipendente e ri-eseguibile: soccerdata mantiene una cache locale
persistente (~/soccerdata/data/), quindi rilanciare uno stage gia' completato
non ri-scarica nulla.

Uso:
    python ingest.py --stage matches      # veloce, ~10 secondi
    python ingest.py --stage understat    # veloce, ~2 minuti
    python ingest.py --stage elo          # veloce, ~1 minuto
    python ingest.py --stage schedule     # medio, ~5 minuti
    python ingest.py --stage lineups      # LENTO, ore
    python ingest.py --stage missing      # LENTO, richiede browser
    python ingest.py --stage all

ATTENZIONE ai tempi: FBref applica rate limiting e soccerdata lo rispetta
introducendo un ritardo tra le richieste. Gli stage 'lineups' e 'player_stats'
fanno una richiesta per partita: con ~380 partite/stagione x 10 stagioni sono
~3800 richieste. Lancialo di notte, una stagione alla volta. La cache rende
l'operazione incrementale, quindi puoi interromperlo e riprenderlo.

WhoScored (stage 'missing') usa Selenium e richiede un browser installato.
Se non ti serve subito, saltalo: il modello T-24h puo' partire senza.
"""

import argparse
import logging
from pathlib import Path

import pandas as pd
import soccerdata as sd

from src import config

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
    raw = pd.read_csv(src, encoding="latin-1")
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
    from src.normalize import apply_name_map, load_name_map
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
    for stat_type in ["shooting", "possession", "passing", "defense"]:
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


def ingest_player_stats() -> pd.DataFrame:
    """
    Statistiche per giocatore e per partita: minuti, gol, assist, xG, xA,
    tiri, tocchi. Serve per costruire il profilo storico di ogni calciatore
    e quindi pesare le assenze in modo intelligente (un attaccante da 0.6 xG
    a partita che manca pesa piu' di un terzino di rotazione).

    MOLTO LENTO: una richiesta per partita, ~22+ righe ciascuna.
    Consiglio: lancialo una stagione alla volta modificando SEASONS.
    """
    fb = sd.FBref(leagues=LEAGUE, seasons=SEASONS)
    df = fb.read_player_match_stats(stat_type="summary")
    save(df, "fbref_player_match")
    return df


def ingest_missing() -> pd.DataFrame:
    """
    Infortunati e squalificati per singola partita, da WhoScored.

    Questo e' il pezzo chiave per il modello T-24h: sono informazioni note
    PRIMA della formazione ufficiale, quindi utilizzabili con anticipo reale.

    RICHIEDE UN BROWSER: WhoScored ha protezioni anti-bot e soccerdata usa
    Selenium. Serve Chrome/Chromium installato. Se fallisce, saltalo per ora.
    """
    ws = sd.WhoScored(leagues=LEAGUE, seasons=SEASONS)
    df = ws.read_missing_players()
    save(df, "whoscored_missing")
    return df


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

    for team in teams:
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

STAGES = {
    "matches": ingest_matches,
    "fixtures": ingest_fixtures,
    "understat": ingest_understat,
    "shots": ingest_understat_shots,
    "schedule": ingest_schedule,
    "team_stats": ingest_team_stats,
    "lineups": ingest_lineups,
    "player_stats": ingest_player_stats,
    "missing": ingest_missing,
    "elo": ingest_elo,
}

# Ordine consigliato: prima i veloci, cosi' hai subito qualcosa con cui
# lavorare mentre i lenti girano in background.
ORDER = ["matches", "understat", "fixtures", "schedule", "elo",
         "team_stats", "lineups", "player_stats", "missing"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=list(STAGES) + ["all"],
        default="matches",
        help="Quale stage eseguire",
    )
    parser.add_argument(
        "--fixtures-file",
        help="solo per --stage fixtures: legge da un file locale invece che "
             "dalla rete. Utile quando il sito e' giu' o per riprodurre uno "
             "snapshot gia' scaricato.",
    )
    args = parser.parse_args()

    stages = ORDER if args.stage == "all" else [args.stage]
    for name in stages:
        log.info("=== stage: %s ===", name)
        try:
            if name == "fixtures":
                ingest_fixtures(args.fixtures_file)
            else:
                STAGES[name]()
        except Exception as exc:
            log.error("stage '%s' fallito: %s", name, exc)
            if args.stage != "all":
                raise


if __name__ == "__main__":
    main()