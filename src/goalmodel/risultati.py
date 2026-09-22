"""
I risultati delle partite, da piu' fonti in ordine di fiducia.

PERCHE' SERVE UNA CATENA
football-data e' la fonte dei risultati di tutto il progetto, ed e' quella
giusta: registra il risultato UFFICIALE e porta con se' quote e statistiche.
Ma aggiorna il file della stagione un paio di volte a settimana, non la sera
della partita. Il 21 settembre 2026, lunedi' sera, la giornata 5 era finita da
un giorno e football-data non ne aveva nemmeno un risultato: `close_round`
restava fermo, e Understat — scaricato dallo stesso comando, alla stessa ora —
li aveva tutti e dieci.

    1. football-data   ufficiale, gia' dentro matches_master
    2. Understat       risultato del campo, aggiornato in giornata
    3. FBref           risultato del campo, dal calendario

Una partita prende il risultato dalla prima fonte che ce l'ha, e la colonna
`fonte_risultato` dice quale. A distanza di mesi e' l'unico modo di sapere da
dove viene un numero in un file che non si puo' piu' correggere.

QUANTO CI SI PUO' FIDARE, MISURATO
Su 4600 partite presenti in entrambe, football-data e Understat danno lo
stesso risultato nel 99.98% dei casi. L'unica differenza e' Sassuolo-Pescara
2016/17: 2-1 in campo, 0-3 a tavolino per decisione del giudice sportivo.
Non e' un errore di Understat: e' un'altra definizione di risultato. Ed e'
esattamente il caso che `riconcilia` sorveglia: se una giornata e' stata
chiusa col risultato del campo e football-data poi pubblica un risultato
ufficiale diverso, lo dice.

COSA NON FA
Non tocca matches_master, che resta il dataset del modello costruito da
football-data con le sue 222 colonne. Il ripiego serve a sapere che una
partita e' finita e come, non ad aggiungere righe senza quote e senza
statistiche all'addestramento.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import config

log = logging.getLogger("risultati")

KEYS = ["league", "season", "home_team", "away_team"]
COLONNE = KEYS + ["date", "FTHG", "FTAG", "FTR", "fonte_risultato"]

FOOTBALL_DATA = "football-data"
UNDERSTAT = "understat"
FBREF = "fbref"


def _esito(casa: pd.Series, fuori: pd.Series) -> pd.Series:
    """H, D, A dai gol: la stessa codifica di FTR in football-data."""
    return pd.Series(
        ["H" if c > f else "A" if c < f else "D" for c, f in zip(casa, fuori, strict=True)],
        index=casa.index,
    )


def _da_football_data() -> pd.DataFrame:
    master = pd.read_parquet(config.INTERIM / "matches_master.parquet",
                             columns=KEYS + ["date", "FTHG", "FTAG", "FTR"])
    out = master[master["FTR"].notna()].copy()
    out["fonte_risultato"] = FOOTBALL_DATA
    return out


def _secondaria(nome: str) -> pd.DataFrame | None:
    """
    Una fonte grezza con i nomi tradotti come in matches_master.

    Passa da `normalize`, non da una traduzione propria: due mappe dei nomi
    divergono sempre, e una partita che non si aggancia non da' errore — resta
    semplicemente senza risultato, che e' il sintomo peggiore possibile.
    """
    from . import normalize as nz

    try:
        grezzo = nz.load_raw(nome)
    except FileNotFoundError:
        log.warning("fonte di ripiego %s assente: salto", nome)
        return None
    d = nz.normalize_season(nz.apply_name_map(grezzo, nz.load_name_map()))
    d["season"] = d["season"].astype(str)
    return d[d["league"].isin(config.LEAGUES)]


def _da_understat() -> pd.DataFrame:
    d = _secondaria("understat_schedule")
    if d is None:
        return pd.DataFrame(columns=COLONNE)
    # `is_result` e' vero solo a partita finita: una partita in corso ha gia'
    # dei gol, e senza questo filtro un 1-0 al 30' diventerebbe un risultato.
    d = d[d["is_result"].fillna(False).astype(bool)].copy()
    d["FTHG"] = d["home_goals"].astype(float)
    d["FTAG"] = d["away_goals"].astype(float)
    d["FTR"] = _esito(d["FTHG"], d["FTAG"])
    d["fonte_risultato"] = UNDERSTAT
    return d[COLONNE]


def _da_fbref() -> pd.DataFrame:
    d = _secondaria("fbref_schedule")
    if d is None:
        return pd.DataFrame(columns=COLONNE)
    # Il punteggio e' una stringa con il trattino lungo (EN DASH, "2-1"): si prendono i
    # due numeri, qualunque sia il separatore.
    gol = d["score"].astype("string").str.extract(r"(\d+)\D+(\d+)")
    d = d.assign(FTHG=pd.to_numeric(gol[0]), FTAG=pd.to_numeric(gol[1]))
    d = d[d["FTHG"].notna() & d["FTAG"].notna()].copy()
    d["FTR"] = _esito(d["FTHG"], d["FTAG"])
    d["fonte_risultato"] = FBREF
    return d[COLONNE]


def risultati() -> pd.DataFrame:
    """
    Un risultato per partita, dalla prima fonte che ce l'ha.

    L'ordine della concatenazione E' la precedenza: `drop_duplicates` tiene la
    prima riga, quindi football-data vince sempre dove c'e'.
    """
    pezzi = [_da_football_data(), _da_understat(), _da_fbref()]
    tutti = pd.concat([p for p in pezzi if not p.empty], ignore_index=True)
    tutti["season"] = tutti["season"].astype(str)
    out = tutti.drop_duplicates(subset=KEYS, keep="first").reset_index(drop=True)

    ripiego = out[out["fonte_risultato"] != FOOTBALL_DATA]
    if len(ripiego):
        log.info("risultati da fonti di ripiego: %s",
                 ripiego["fonte_risultato"].value_counts().to_dict())
    return out


def riconcilia() -> list[str]:
    """
    Le giornate chiuse con un risultato di ripiego, confrontate con quello
    ufficiale appena football-data lo pubblica.

    Non riscrive niente: il file di giornata resta non correggibile, per le
    stesse ragioni di sempre. Dice soltanto dove il risultato del campo e
    quello ufficiale divergono — un 0-3 a tavolino, un risultato corretto —
    perche' qualcuno decida cosa farne sapendolo.
    """
    ufficiali = _da_football_data()
    ufficiali["season"] = ufficiali["season"].astype(str)
    avvisi: list[str] = []
    cartella = config.TRACK_RECORD / "rounds"
    for file in sorted(cartella.glob("round_*.csv")):
        giornata = pd.read_csv(file)
        if "fonte_risultato" not in giornata.columns:
            continue  # chiusa prima della catena: tutto da football-data
        dubbie = giornata[giornata["fonte_risultato"].fillna(FOOTBALL_DATA) != FOOTBALL_DATA]
        if dubbie.empty:
            continue
        dubbie = dubbie.assign(season=dubbie["season"].astype(str))
        confronto = dubbie.merge(
            ufficiali[["season", "home_team", "away_team", "FTHG", "FTAG"]],
            on=["season", "home_team", "away_team"], how="inner", suffixes=("", "_uff"))
        for _, r in confronto.iterrows():
            if (r["FTHG"], r["FTAG"]) != (r["FTHG_uff"], r["FTAG_uff"]):
                avvisi.append(
                    f"{file.name}: {r['home_team']}-{r['away_team']} archiviata "
                    f"{int(r['FTHG'])}-{int(r['FTAG'])} da {r['fonte_risultato']}, "
                    f"ufficiale {int(r['FTHG_uff'])}-{int(r['FTAG_uff'])}")
    for a in avvisi:
        log.warning("RISULTATO DIVERSO DA QUELLO UFFICIALE: %s", a)
    return avvisi
