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

# Il registro delle previsioni sta FUORI da data/, che e' in .gitignore.
# E' l'unico dato irriproducibile del progetto: i parquet si riscaricano, le
# previsioni no, perche' vanno scritte prima del calcio d'inizio e quel
# momento non torna. Qui dentro e' versionato, quindi ha una storia completa
# e una copia in ogni clone del repository.
TRACK_RECORD = ROOT / "track_record"

TEAM_NAME_MAP = MANUAL / "team_name_map.json"
TEAM_NAME_MAP_SUGGESTED = MANUAL / "team_name_map_suggested.json"
COACH_CHANGES = MANUAL / "coach_changes.csv"
DERBIES = MANUAL / "derbies.csv"

for _d in (RAW, INTERIM, PROCESSED, MANUAL, TRACK_RECORD):
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

# Fuso orario in cui ciascuna fonte esprime gli orari di calcio d'inizio.
#
# NON E' UN DETTAGLIO. fbref pubblica l'orario nel fuso LOCALE DELLO STADIO,
# football-data.co.uk nel fuso del Regno Unito. Verificato su 2681 partite con
# orario su entrambe le fonti: la differenza e' esattamente 1 ora in OGNI mese
# dell'anno, inverno compreso — quindi non e' UTC contro UK (varierebbe con
# l'ora legale), e' Italia contro UK. Convertendo entrambe con questi fusi, i
# due orari coincidono al minuto sul 99.5% delle partite.
#
# Trattare l'orario di fbref come UTC sposta il calcio d'inizio di due ore in
# avanti (in estate) e fa passare per valida una previsione fatta a partita
# gia' iniziata. E' successo davvero, su due righe del registro.
LEAGUE_TIMEZONE = {
    "ITA-Serie A": "Europe/Rome",
    "ENG-Premier League": "Europe/London",
    "ESP-La Liga": "Europe/Madrid",
    "GER-Bundesliga": "Europe/Berlin",
    "FRA-Ligue 1": "Europe/Paris",
}

# ---------------------------------------------------------------------------
# Quote delle partite in arrivo
# ---------------------------------------------------------------------------

# football-data.co.uk pubblica in un unico file le partite del turno imminente
# di tutti i campionati che copre, con le quote di apertura.
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

# Codice 'Div' di football-data -> nome lega di soccerdata. Il file dei
# fixture non ha una colonna lega leggibile: ha questi codici.
FOOTBALL_DATA_DIV = {
    "I1": "ITA-Serie A",
    "E0": "ENG-Premier League",
    "SP1": "ESP-La Liga",
    "D1": "GER-Bundesliga",
    "F1": "FRA-Ligue 1",
}

# ---------------------------------------------------------------------------
# Coppe europee: il pezzo che manca al blocco A
# ---------------------------------------------------------------------------

# `fbref_schedule` contiene la sola Serie A, quindi una partita di Champions
# del martedi' non compare da nessuna parte e la congestione da coppa e'
# invisibile. Queste tre competizioni la rendono visibile.
#
# I nomi a destra sono quelli ESATTI della pagina fbref.com/en/comps/ — non
# "Champions League" ne' "UCL": soccerdata aggancia per stringa e uno scarto
# di una parola non da' errore, da' zero righe. Le chiavi a sinistra sono
# nuove, quindi aggiungerle a LEAGUE_DICT non sovrascrive nessuna lega
# esistente (soccerdata fa `dict.update` al primo livello).
#
# La Conference League esiste solo dal 2021/22: sulle stagioni precedenti
# restituisce vuoto, e va bene cosi'.
FBREF_CUPS = {
    "UEFA-Champions League": "UEFA Champions League",
    "UEFA-Europa League": "UEFA Europa League",
    "UEFA-Conference League": "UEFA Conference League",
}

# ---------------------------------------------------------------------------
# WhoScored: nomi di regione localizzati
# ---------------------------------------------------------------------------

# WhoScored serve le pagine nella lingua dedotta dalla GEOLOCALIZZAZIONE di chi
# chiama. Da un IP italiano la regione si chiama "Italia", non "Italy", e la
# mappa interna di soccerdata — che si aspetta "Italy - Serie A" — non aggancia
# piu' niente. L'errore non dice questo: dice
#   KeyError: "None of [Index(['ITA-Serie A'])] are in the [index]"
# che sembra una lega non supportata, mentre la lega c'e' e si chiama in
# un'altra lingua.
#
# Qui si sovrascrive SOLO il campo WhoScored del dizionario leghe, a runtime e
# dentro il progetto: scrivere in ~/soccerdata/config/league_dict.json
# rimpiazzerebbe l'intera voce della lega (soccerdata fa `dict.update` al primo
# livello) e romperebbe anche FBref, Understat e MatchHistory.
#
# Se un giorno le pagine tornassero in inglese, questa mappa va svuotata: il
# controllo in `ingest.applica_locale_whoscored` avvisa quando una voce non
# corrisponde a nessuna regione vista nel catalogo.
WHOSCORED_LEAGUE_OVERRIDE = {
    "ITA-Serie A": "Italia - Serie A",
    "ENG-Premier League": "Inghilterra - Premier League",
    "ESP-La Liga": "Spagna - LaLiga",
    "GER-Bundesliga": "Germania - Bundesliga",
    "FRA-Ligue 1": "Francia - Ligue 1",
}

# Oltre questa eta' lo snapshot delle quote e' sospetto: il file copre il turno
# imminente e viene rigenerato ogni settimana, quindi tre giorni sono gia'
# tanti. Non blocca, avvisa.
FIXTURES_MAX_AGE_DAYS = 3

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

# Quota equa minima per la sezione "selezioni con quota" del report. Sotto
# questo livello la vincita e' talmente piccola che la giocata non interessa
# nessuno, per quanto probabile sia.
#
# ATTENZIONE A COSA NON FA. Alzare la soglia NON migliora il valore atteso:
# il margine del book (5.19% misurato) e' identico su tutti i mercati derivati
# dalle stesse quote, quindi filtrare per quota sposta varianza e vincita
# potenziale, non il vantaggio — che resta negativo ovunque.
QUOTA_MINIMA_SELEZIONE = 1.50

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

# RPS del benchmark market-only sul test set (2324-2526, 1140 partite).
# E' il riferimento contro cui si legge il track record di produzione: uno
# scarto persistente da questo valore non e' un modello che sbaglia, e' la
# pipeline che si comporta diversamente dal backtest.
TEST_RPS_REFERENCE = 0.1881

# Scarto dal riferimento sotto il quale non vale la pena accorgersi di niente.
# Serve a rispondere a "quante previsioni servono perche' il track record dica
# qualcosa": senza una tolleranza dichiarata, la domanda non ha risposta.
#
# 0.01 non e' arbitrario: e' un quarto della distanza fra il mercato (0.1881) e
# il pavimento delle frequenze di base (0.2291), ed e' piu' grande di QUALSIASI
# differenza misurata fra i modelli statistici provati (la maggiore, M2 contro
# il mercato, vale 0.0088). Una divergenza piu' piccola di cosi' fra produzione
# e backtest non cambierebbe nessuna decisione; una piu' grande vuol dire che
# la pipeline di produzione non sta facendo quello che faceva il backtest.
TRACK_TOLERANCE_RPS = 0.01

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

# Iperparametri del GBM, scelti per ricerca casuale sulla validazione, uno per
# variante. Il numero di alberi NON e' qui: lo decide l'arresto anticipato sulla
# coda del training, con tetto a 3000 (che non viene mai raggiunto: le scelte
# stanno intorno ai 450).
# Si riottengono con: python -m src.models.gbm --tune
#
# NOTA SUI BORDI DELLO SPAZIO DI RICERCA. In entrambe le varianti la scelta
# tocca `num_leaves` al massimo (8) e `colsample_bytree` al minimo (0.6). Non
# si e' esteso lo spazio perche' la superficie e' piatta: fra la prima e la
# quinta configurazione ci sono 0.0003 di RPS, contro un'ampiezza tipica
# dell'intervallo appaiato di ~0.006. La scelta esatta dentro le prime cinque
# e' rumore. Se un giorno si estende, gli unici due parametri da muovere sono
# quelli, e solo quelli.
GBM_PARAMS_NO_MARKET = {
    "learning_rate": 0.03,
    "num_leaves": 8,
    "min_child_samples": 75,
    "colsample_bytree": 0.6,
    "subsample": 0.6,
    "reg_lambda": 20.0,
}
GBM_PARAMS_MARKET = {
    "learning_rate": 0.02,
    "num_leaves": 8,
    "min_child_samples": 75,
    "colsample_bytree": 0.6,
    "subsample": 0.7,
    "reg_lambda": 5.0,
}

# M5, il GBM ancorato al mercato via init_score. Regolarizzazione molto piu'
# forte: stima un residuo, e un residuo si sovradatta piu' facilmente di un
# livello. Lo spazio di ricerca e' stato esteso una volta (learning_rate giu',
# reg_lambda su) perche' le prime cinque scelte lo toccavano sistematicamente;
# estenderlo ha guadagnato 0.00006 di RPS e ha reso la superficie piatta
# (0.000025 fra le prime cinque), quindi non si estende oltre.
GBM_PARAMS_ANCHORED = {
    "learning_rate": 0.0025,
    "num_leaves": 4,
    "min_child_samples": 50,
    "colsample_bytree": 0.6,
    "subsample": 0.6,
    "reg_lambda": 50.0,
}

# Peso della miscela logaritmica di M6, stimato sulla sola validazione.
# 0 = mercato puro, 1 = GBM puro. La curva e' una U con minimo interno a 0.15;
# il guadagno rispetto al mercato puro e' pero' di soli 0.00011 di RPS.
BLEND_WEIGHT = 0.15
