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
ORDER = ["matches", "understat", "schedule", "elo",
         "team_stats", "lineups", "player_stats", "missing"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=list(STAGES) + ["all"],
        default="matches",
        help="Quale stage eseguire",
    )
    args = parser.parse_args()

    stages = ORDER if args.stage == "all" else [args.stage]
    for name in stages:
        log.info("=== stage: %s ===", name)
        try:
            STAGES[name]()
        except Exception as exc:
            log.error("stage '%s' fallito: %s", name, exc)
            if args.stage != "all":
                raise


if __name__ == "__main__":
    main()