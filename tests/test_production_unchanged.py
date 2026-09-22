"""
Non regressione della produzione: M1 deve restare identico, bit a bit.

PERCHE' ESISTE. Da qui in poi gli esperimenti toccano moduli condivisi —
`features/market.py` per le baseline alternative, i set di feature, nuovi
modelli. Nessuno di questi cambiamenti deve spostare di un solo bit cio' che
`predict_round` scrive nel track record ogni venerdi'. Un cambiamento del
genere non darebbe errore: le probabilita' sarebbero plausibili, il registro
si riempirebbe, e il track record confronterebbe previsioni di un modello
diverso da quello valutato nel backtest. E' lo stesso principio del test sul
fuso orario: la rete che se ne accorge per te.

COSA FISSA. Un campione di partite gia' giocate, scelto una volta sola e
salvato in `tests/golden/m1_riferimento.parquet`:
  - le QUOTE di ingresso, per distinguere "e' cambiato il codice" da "sono
    cambiati i dati" — football-data corregge a volte lo storico, e un test
    che confondesse le due cose farebbe cercare il bug nel posto sbagliato;
  - le fonti scelte da `pick_odds` e i lambda impliciti di `market.py`;
  - le probabilita' di M1 e tutti i mercati derivati di `all_markets`, che
    sono quelli che il report mostra.

Il campione e' stratificato per stagione e include di proposito le righe che
passano dai RIPIEGHI (BW, Avg, BbAv, P): sono i percorsi di codice che un
refactoring di `pick_odds` rompe per primi e che un campione casuale di
partite normali non vedrebbe mai.

BIT A BIT, NON "A MENO DI 1e-9". Una tolleranza nasconderebbe proprio le
modifiche che contano: un de-vigging "equivalente" che cambia l'ultima cifra
e' un de-vigging diverso, e il track record deve saperlo.

Va lanciato PRIMA DI OGNI COMMIT:

    python -m tests.test_production_unchanged

Si rigenera SOLO dopo un cambiamento di produzione voluto e dichiarato:

    python -m tests.test_production_unchanged --rigenera
"""

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goalmodel import config
from goalmodel.features import market
from goalmodel.models.baseline import PRED_COLS, MarketOnly, all_markets

GOLDEN = Path(__file__).resolve().parent / "golden" / "m1_riferimento.parquet"
KEYS = config.JOIN_KEYS
PER_STAGIONE = 8
PER_FONTE = 4
FONTI = ["mkt_1x2_source", "mkt_ou_source"]
LAMBDA = ["mkt_lambda_home", "mkt_lambda_away"]


def colonne_quote(df: pd.DataFrame) -> list[str]:
    """Le quote di APERTURA che `market.py` legge per costruire l'ingresso di M1."""
    cols = []
    for book in market.BOOKS_1X2:
        cols += list(market.cols_1x2(book))
    for book in market.BOOKS_OU:
        cols += list(market.cols_ou(book))
    return [c for c in cols if c in df.columns]


def calcola(master: pd.DataFrame) -> pd.DataFrame:
    """
    Il percorso di produzione, dalle quote grezze ai mercati derivati.

    `market.build` gira sul frame INTERO, come fa la produzione, e solo dopo
    si seleziona: se un giorno la costruzione smettesse di essere riga per
    riga, calcolare solo sul campione nasconderebbe proprio quel cambiamento.
    """
    feats = market.build(master, save=False)
    m1 = MarketOnly().predict(feats)
    out = pd.concat([feats[KEYS + FONTI + LAMBDA], m1[PRED_COLS]], axis=1)
    out["season"] = out["season"].astype(str)
    return out


def scegli_campione(master: pd.DataFrame, calcolato: pd.DataFrame) -> pd.DataFrame:
    """Stratificato per stagione, piu' tutte le fonti di ripiego."""
    base = calcolato.copy()
    base["date"] = pd.to_datetime(master["date"]).to_numpy()
    concluse = sorted(s for s in base["season"].unique() if s != config.CURRENT_SEASON)
    ok = base[LAMBDA].notna().all(axis=1)

    scelte = []
    for s in concluse:
        g = base[(base["season"] == s) & ok].sort_values(["date", "home_team"])
        pos = np.unique(np.linspace(0, len(g) - 1, PER_STAGIONE).round().astype(int))
        scelte.append(g.iloc[pos])

    for fonte in FONTI:
        altre = base[ok & base[fonte].notna() & (base[fonte] != "B365")]
        for _, g in altre.sort_values("date").groupby(fonte):
            scelte.append(g.head(PER_FONTE))

    camp = pd.concat(scelte).drop_duplicates(subset=KEYS)
    return camp[KEYS].reset_index(drop=True)


def assembla(master: pd.DataFrame, calcolato: pd.DataFrame,
             chiavi: pd.DataFrame) -> pd.DataFrame:
    """Chiavi -> quote di ingresso + uscite di produzione, in un frame solo."""
    m = master.copy()
    m["season"] = m["season"].astype(str)
    quote = colonne_quote(m)
    ingressi = m[KEYS + quote].drop_duplicates(subset=KEYS)

    out = chiavi.merge(ingressi, on=KEYS, how="left", validate="one_to_one")
    out = out.merge(calcolato, on=KEYS, how="left", validate="one_to_one")
    for c in quote:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")

    mk = all_markets(out["mkt_lambda_home"].to_numpy(float),
                     out["mkt_lambda_away"].to_numpy(float))
    mk.columns = [f"am_{c}" for c in mk.columns]
    mk.index = out.index
    out = pd.concat([out, mk], axis=1)
    for c in LAMBDA + PRED_COLS + list(mk.columns):
        out[c] = out[c].astype("float64")
    return out


def impronta(df: pd.DataFrame, colonne: list[str]) -> str:
    """Hash dei valori esatti: un numero da confrontare a colpo d'occhio."""
    h = hashlib.sha256()
    for c in colonne:
        h.update(np.ascontiguousarray(df[c].to_numpy("float64")).tobytes())
    return h.hexdigest()[:16]


def _uguali(a: pd.Series, b: pd.Series) -> bool:
    if a.dtype.kind in "fc" or b.dtype.kind in "fc":
        return np.array_equal(a.to_numpy("float64"), b.to_numpy("float64"),
                              equal_nan=True)
    return a.astype(object).fillna("<NA>").tolist() == b.astype(object).fillna("<NA>").tolist()


def rigenera(master: pd.DataFrame) -> None:
    calcolato = calcola(master)
    chiavi = scegli_campione(master, calcolato)
    rif = assembla(master, calcolato, chiavi)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    rif.to_parquet(GOLDEN, index=False)
    uscite = LAMBDA + PRED_COLS + [c for c in rif.columns if c.startswith("am_")]
    print(f"riferimento RIGENERATO: {len(rif)} partite, {rif.shape[1]} colonne")
    print(f"  stagioni: {sorted(rif['season'].unique())}")
    for f in FONTI:
        print(f"  {f}: {rif[f].value_counts().to_dict()}")
    print(f"  impronta delle uscite: {impronta(rif, uscite)}")
    print("\nATTENZIONE: da questo momento il test confronta con QUESTI numeri.")
    print("Rigenerare e' giusto solo dopo un cambiamento di produzione voluto.")


def verifica(master: pd.DataFrame) -> int:
    if not GOLDEN.exists():
        print(f"manca {GOLDEN}: lancia prima con --rigenera")
        return 2
    rif = pd.read_parquet(GOLDEN)
    chiavi = rif[KEYS]
    corrente = assembla(master, calcola(master), chiavi)

    presenti = chiavi.merge(master.assign(season=master["season"].astype(str))[KEYS],
                            on=KEYS, how="inner")
    if len(presenti) < len(chiavi):
        print(f"1. partite di riferimento presenti nel dataset: "
              f"{len(presenti)}/{len(chiavi)}   FALLITO")
        print("   Sono sparite partite storiche da matches_master: e' un "
              "problema di DATI, non di codice.")
        return 1
    print(f"1. partite di riferimento presenti: {len(chiavi)}/{len(chiavi)}      ok")

    quote = [c for c in colonne_quote(master) if c in rif.columns]
    cambiate = [c for c in quote if not _uguali(rif[c], corrente[c])]
    if cambiate:
        print("2. quote di ingresso identiche                 FALLITO")
        print(f"   colonne cambiate: {cambiate}")
        print("   Sono cambiati i DATI, non il codice: football-data ha "
              "corretto lo storico.\n   Verifica e poi rigenera con --rigenera.")
        return 1
    print(f"2. quote di ingresso identiche ({len(quote)} colonne)   ok")

    # UN LAMBDA SPARITO NON E' UN LAMBDA DIVERSO, e i confronti sotto non lo
    # vedrebbero: `_uguali` mette NaN e NaN d'accordo, quindi un de-vigging
    # che smette di produrre un valore dove il riferimento ce l'aveva passa
    # come "identico". E' una regressione che non cambia i numeri, li fa
    # scomparire. Il controllo era scritto e lasciato scollegato (audit B14).
    mancanti = corrente["mkt_lambda_home"].isna() & rif["mkt_lambda_home"].notna()
    if mancanti.any():
        persi = chiavi[mancanti.to_numpy()]
        print("2b. lambda di mercato non piu' calcolati       FALLITO")
        print(f"   {int(mancanti.sum())} partite hanno perso mkt_lambda_home, "
              f"es. {persi.head(3).to_dict('records')}")
        print("   Le quote di ingresso sono identiche (passo 2), quindi e' il "
              "de-vigging che ha smesso di produrre un valore.")
        return 1
    print("2b. nessun lambda di mercato sparito           ok")

    esito = 0
    gruppi = [
        ("3. fonti scelte da pick_odds", FONTI),
        ("4. lambda impliciti di market.py", LAMBDA),
        ("5. probabilita' di M1", PRED_COLS),
        ("6. mercati derivati (all_markets)",
         [c for c in rif.columns if c.startswith("am_")]),
    ]
    for etichetta, colonne in gruppi:
        diverse = [c for c in colonne if not _uguali(rif[c], corrente[c])]
        if diverse:
            esito = 1
            print(f"{etichetta:<42} FALLITO")
            for c in diverse[:6]:
                if rif[c].dtype.kind == "f":
                    d = (rif[c] - corrente[c]).abs().max()
                    print(f"   {c}: scarto massimo {d:.3e}")
                else:
                    print(f"   {c}: valori diversi")
        else:
            print(f"{etichetta:<42} ok  (bit a bit)")

    uscite = LAMBDA + PRED_COLS + [c for c in rif.columns if c.startswith("am_")]
    print(f"\nimpronta riferimento {impronta(rif, uscite)}   "
          f"corrente {impronta(corrente, uscite)}")
    if esito:
        print("\nLA PRODUZIONE E' CAMBIATA. Non committare: trova quale modifica "
              "ha spostato M1.\nSe il cambiamento e' voluto, dichiaralo e poi "
              "rigenera con --rigenera.")
    else:
        print("\nproduzione invariata: M1 identico bit a bit sul campione")
    return esito


def sensibilita(master: pd.DataFrame) -> int:
    """
    La rete scatta davvero? Si sposta UN valore di UN bit e il test deve fallire.

    Una rete di non regressione che non e' mai stata vista fallire non e' una
    rete: e' un test che passa. Qui si altera una copia del riferimento — mai
    il file vero, mai il codice di produzione — della minima quantita'
    rappresentabile (`np.nextafter`) su una probabilita' di M1, e si verifica
    che il confronto la veda. Poi lo stesso con una quota di ingresso, che
    deve far scattare il ramo "sono cambiati i dati" e non l'altro.
    """
    import contextlib
    import io
    import tempfile

    global GOLDEN
    vero = GOLDEN
    rif = pd.read_parquet(vero)
    esiti = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for etichetta, colonna, atteso in (
                ("probabilita' di M1", "p_home", "5. probabilita' di M1"),
                ("quota di ingresso", "B365H", "2. quote di ingresso identiche"),
            ):
                alterato = rif.copy()
                v = alterato.loc[0, colonna]
                alterato.loc[0, colonna] = np.nextafter(v, np.inf)
                GOLDEN = Path(tmp) / f"alterato_{colonna}.parquet"
                alterato.to_parquet(GOLDEN, index=False)

                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    codice = verifica(master)
                visto = f"{atteso}" in buffer.getvalue() and "FALLITO" in buffer.getvalue()
                ok = codice == 1 and visto
                esiti.append(ok)
                print(f"  un bit su {etichetta:<20} ({colonna}): "
                      f"{'il test FALLISCE come deve' if ok else 'NON RILEVATO'}")
    finally:
        GOLDEN = vero
    if all(esiti):
        print("\nla rete scatta al singolo bit, sul ramo giusto")
        return 0
    print("\nLA RETE NON SCATTA: il test di non regressione non protegge niente")
    return 1


@pytest.mark.richiede_dati
def test_produzione_invariata() -> None:
    """
    Il punto d'ingresso per pytest della rete di sicurezza della produzione.

    ERA INVISIBILE AL RUNNER. Questo file non aveva nessuna funzione `test_`,
    quindi `pytest` lo raccoglieva a zero e la suite risultava verde senza mai
    confrontare M1 con il riferimento. E' lo stesso difetto che il file
    esiste per impedire — qualcosa che sembra misurare e non misura — capitato
    al misuratore.
    """
    import logging
    logging.getLogger("market").setLevel(logging.WARNING)
    master = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    assert verifica(master) == 0, (
        "M1, le quote di ingresso o i lambda di mercato sono cambiati rispetto "
        "al riferimento. Se il cambiamento e' voluto e dichiarato, rigenera con "
        "`python -m tests.test_production_unchanged --rigenera`."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Non regressione di M1")
    ap.add_argument("--rigenera", action="store_true",
                    help="riscrive il riferimento. SOLO dopo un cambiamento "
                         "di produzione voluto")
    ap.add_argument("--sensibilita", action="store_true",
                    help="verifica che il test fallisca a un bit di differenza")
    args = ap.parse_args()

    import logging
    logging.getLogger("market").setLevel(logging.WARNING)
    master = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    if args.rigenera:
        rigenera(master)
        return
    if args.sensibilita:
        sys.exit(sensibilita(master))
    sys.exit(verifica(master))


if __name__ == "__main__":
    main()
