"""
Configurazione centrale. Ogni modulo importa da qui: nessun path hardcoded
sparso per il codice.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
MANUAL = ROOT / "manual"

TEAM_NAME_MAP = MANUAL / "team_name_map.json"
TEAM_NAME_MAP_SUGGESTED = MANUAL / "team_name_map_suggested.json"
COACH_CHANGES = MANUAL / "coach_changes.csv"
DERBIES = MANUAL / "derbies.csv"

for _d in (RAW, INTERIM, PROCESSED, MANUAL):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Perimetro dati
# ---------------------------------------------------------------------------

# Big 5 europei: stesso set di feature completo su tutti, ~1.800 partite/stagione.
# Per lavorare solo sull'Italia in fase di sviluppo, riduci a ['ITA-Serie A']:
# tutto il resto del codice funziona identico.
LEAGUES = [
    "ITA-Serie A",
    "ENG-Premier League",
    "ESP-La Liga",
    "GER-Bundesliga",
    "FRA-Ligue 1",
]

# 2014/15 e' il limite inferiore di Understat, che e' la fonte di xG e PPDA.
# Formato soccerdata: '1415' = stagione 2014/15.
SEASONS = [
    "1415", "1516", "1617", "1718", "1819", "1920",
    "2021", "2122", "2223", "2324", "2425", "2526",
    "2627",
]

# La stagione in corso. soccerdata la riscarica automaticamente a ogni run
# (bypassa la cache), quindi non va toccata a mano dopo ogni giornata.
CURRENT_SEASON = "2627"

SQUADRA_TARGET = "Napoli"

# ---------------------------------------------------------------------------
# Parametri di modellazione
# ---------------------------------------------------------------------------

# Half-life in partite per le medie esponenziali della forma recente.
FORM_HALFLIFE = 6

# Finestra piu' lunga per le statistiche separate casa/trasferta,
# dove i campioni si dimezzano.
FORM_HALFLIFE_VENUE = 10

# Quota di regressione verso la media di lega applicata al confine di stagione.
# Rappresenta l'incertezza da mercato e cambi tecnici. Le finestre mobili NON
# si azzerano mai: si attenuano. Vedi sezione 14bis del documento master.
SEASON_REGRESSION = 0.30

# Numero massimo di gol per lato nella matrice dei risultati esatti.
MAX_GOALS = 10
