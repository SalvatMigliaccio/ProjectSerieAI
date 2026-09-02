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
    # "ENG-Premier League",
    # "ESP-La Liga",
    # "GER-Bundesliga",
    # "FRA-Ligue 1",
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

# ---------------------------------------------------------------------------
# Protocollo di valutazione
# ---------------------------------------------------------------------------

# Stagioni di test. Sono le ultime tre concluse: si valuta sempre qui, e non si
# guarda nient'altro finche' non si e' finito di scegliere. La 2627 e' in corso
# (20 partite) e non fa da test: e' il set su cui si predice davvero.
TEST_SEASONS = ["2324", "2425", "2526"]

# Stagioni escluse dall'addestramento: la prima non ha storico ne' media di
# lega di riferimento (vedi `is_burn_in` in features/form.py).
BURN_IN_SEASONS = ["1415"]

# Finestra mobile del GLM Poisson, in partite di lega. Due stagioni: abbastanza
# da stimare 20 attacchi e 20 difese, abbastanza poco da seguire i cambi di
# rosa. E' il primo iperparametro da mettere in discussione.
GLM_WINDOW_MATCHES = 760

# Ridge minima sui coefficienti del GLM. Non serve a regolarizzare davvero, ma
# a rendere identificabile un modello dove intercetta, attacchi e difese sono
# collineari per costruzione.
GLM_RIDGE = 1e-3

# Correzione Dixon-Coles sui punteggi bassi, per i modelli che NON la stimano
# (M1, M2, M4). Zero significa due Poisson indipendenti. M3 non usa questo
# valore: stima il proprio rho insieme ad attacchi e difese.
# ATTENZIONE AL SEGNO: e' rho negativo ad alzare 0-0 e 1-1.
DC_RHO = 0.0

# Numero di bin per curve di calibrazione ed ECE.
CALIBRATION_BINS = 10

# Stagioni di validazione: qui si tarano gli iperparametri, mai sul test.
# Sono le due che precedono il test, quindi il taratura vede solo il passato.
VALIDATION_SEASONS = ["2122", "2223"]

# Ricampionamenti del bootstrap a cluster sulle giornate.
BOOTSTRAP_SAMPLES = 10_000

# Griglia di half-life in GIORNI per il decadimento temporale di Dixon-Coles.
# Si sceglie sulla validazione. La griglia arriva fino a due anni perche' la
# prima versione si fermava a 180 e il minimo cadeva proprio li': scegliere il
# valore al bordo della griglia significa non aver ancora trovato il minimo.
DC_HALFLIFE_GRID = [30, 60, 90, 120, 180, 240, 365, 540, 730]

# Valore scelto sulla validazione (RPS 0.20051). Il minimo e' interno alla
# griglia e la curva e' piatta fra 180 e 540 giorni: la scelta esatta conta
# poco, quello che conta e' non stare sotto i 120.
DC_HALFLIFE = 240

# Numero di alberi del GBM, scelto sulla validazione per ciascuna variante.
GBM_TREES_NO_MARKET = 300
GBM_TREES_MARKET = 300
