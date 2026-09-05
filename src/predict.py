"""
Inferenza settimanale: probabilita' per le partite in arrivo, registrate
prima che si giochino.

QUALE MODELLO, E PERCHE' NON IL PIU' COMPLICATO
Il modello predefinito e' **M1, market-only via lambda**. Non e' pigrizia: e'
l'unica scelta coerente con quello che il test set ha misurato. Nessun modello
statistico batte il mercato, M5 ancorato vi degenera esattamente, e usare in
produzione qualcosa di misurato peggiore del riferimento significherebbe
buttare via il lavoro di valutazione. Il giorno in cui un M7 con le feature
sui giocatori battera' il mercato con intervallo netto, qui cambia un
argomento: `--model` accetta qualsiasi modello che rispetti l'interfaccia
`fit`/`predict` di models/baseline.py.

DA DOVE VENGONO LE PARTITE FUTURE
Da `fbref_schedule.parquet`, che contiene il calendario completo, comprese le
partite non ancora giocate — 4941 righe contro le 4580 di `matches`. I nomi
squadra passano per `team_name_map.json` PRIMA di qualsiasi join: fbref usa
`Hellas Verona` e `SPAL` dove il resto del progetto usa `Verona` e `Spal`, e
senza quelle due voci la giornata si aggancia all'89%.

IL PUNTO CRITICO: LE FEATURE DI UNA PARTITA NON GIOCATA
Per una partita futura non esistono FTHG, home_np_xg, home_ppda: sono
statistiche che nascono dalla partita stessa. La soluzione non e' imputarle,
e' accodare la riga futura in fondo allo storico e rilanciare i moduli di
feature senza toccarne la logica:

  - `features/form.py` scrive lo stato PRIMA di ogni partita e solo dopo lo
    aggiorna. Una riga con statistiche tutte nulle riceve quindi lo stato
    dopo l'ultima partita giocata di quelle due squadre — esattamente cio' che
    serve — e non corrompe lo stato, perche' il ciclo salta i valori nulli.
    Zero modifiche alla logica.
  - `features/market.py` gira sulle quote di apertura della partita in arrivo.

Un assert verifica che nessuna colonna post-partita sia valorizzata nelle
righe future. Se scatta, il filtro ha un bug e la previsione sarebbe leakage.

SE LE QUOTE NON CI SONO, LA PARTITA SI SALTA
Non si imputa un valore di mercato: senza quote M1 non ha ingressi, e una
previsione inventata sarebbe peggio di nessuna previsione. La partita esce
dal report con un messaggio esplicito e non finisce nel log.

Uso:
    python -m src.predict --matchday 3
    python -m src.predict --next
    python -m src.predict --team Napoli
"""

from __future__ import annotations

import argparse
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config
from .features import form as form_mod
from .features import market as market_mod
from .models.baseline import Model, MarketOnly, score_matrix
from .normalize import apply_name_map, load_name_map, load_raw, normalize_season

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("predict")

KEYS = ["league", "season", "home_team", "away_team"]
PREDICTIONS_LOG = config.PROCESSED / "predictions_log.csv"

# Le previsioni ricostruite con --as-of NON vanno nel registro vero. Sono
# state prodotte conoscendo gia' il calendario completo e, soprattutto, da un
# modello scelto guardando quelle stagioni: mescolarle al track record lo
# falsificherebbe, e il track record e' l'unica misura davvero fuori campione
# che il progetto abbia. Finiscono in un file separato.
BACKFILL_LOG = config.PROCESSED / "predictions_backfill.csv"

UPCOMING_ODDS = config.MANUAL / "upcoming_odds.csv"

LOG_COLUMNS = [
    "timestamp_prediction", "model_version",
    "league", "season", "matchday", "match_date", "home_team", "away_team",
    "lambda_home", "lambda_away",
    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
    "odds_home", "odds_draw", "odds_away", "odds_source",
]

# Tutto cio' che esiste solo DOPO il fischio finale. Su una riga futura deve
# essere nullo: e' il controllo che separa una previsione da un imbroglio.
POST_MATCH_COLS = [
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
    "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR",
    "HxG", "AxG",
    "home_xg", "away_xg", "home_np_xg", "away_np_xg",
    "home_np_xg_difference", "away_np_xg_difference",
    "home_ppda", "away_ppda", "home_deep_completions", "away_deep_completions",
    "home_points", "away_points", "home_expected_points", "away_expected_points",
    "home_goals", "away_goals",
]


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------

def kickoff(df: pd.DataFrame) -> pd.Series:
    """
    Orario del calcio d'inizio, in UTC.

    IL FUSO ORARIO E' LA PARTE CHE CONTA. fbref pubblica l'orario nel fuso
    locale dello stadio, non in UTC: per la Serie A e' ora italiana. Trattarlo
    come UTC lo sposta di due ore in avanti d'estate, e una previsione fatta
    dopo il fischio d'inizio passa per valida. E' successo, su due righe del
    registro. La conversione usa `config.LEAGUE_TIMEZONE`, che gestisce da
    solo il cambio dell'ora legale.

    fbref pubblica l'ora solo per le giornate imminenti: su 380 partite di
    stagione ne ha una cinquantina. Quando manca si prende la mezzanotte
    locale, che in UTC cade la sera PRIMA: e' il limite prudente, anticipa il
    fischio invece di posticiparlo, quindi una previsione considerata valida
    lo e' davvero.

    Lo stesso valore serve sia a filtrare le partite future sia a verificare
    che il timestamp della previsione le preceda: usarne due diversi
    permetterebbe a una partita di passare il filtro e fallire l'assert.
    """
    date = pd.to_datetime(df["date"]).dt.normalize()
    time = df["time"] if "time" in df.columns else pd.Series(pd.NA, index=df.index)
    delta = pd.to_timedelta(time.astype("string") + ":00", errors="coerce").fillna(
        pd.Timedelta(0)
    )
    locale = date + delta

    out = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    leghe = df["league"] if "league" in df.columns else pd.Series("", index=df.index)
    for lega, idx in leghe.groupby(leghe).groups.items():
        tz = config.LEAGUE_TIMEZONE.get(lega)
        if tz is None:
            log.warning("fuso orario ignoto per '%s': interpreto gli orari come UTC", lega)
            tz = "UTC"
        # ambiguous/nonexistent: le due ore l'anno del cambio ora legale non
        # devono far esplodere la conversione.
        out.loc[idx] = (
            locale.loc[idx]
            .dt.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
            .dt.tz_convert("UTC")
        )
    return out


def load_fixtures(as_of: pd.Timestamp) -> pd.DataFrame:
    """Calendario normalizzato, con giornata e orario, solo partite future."""
    mapping = load_name_map()
    sched = normalize_season(apply_name_map(load_raw("fbref_schedule"), mapping))
    sched = sched[sched["league"].isin(config.LEAGUES)].copy()
    sched = sched.dropna(subset=["week"])

    sched["matchday"] = sched["week"].astype(int)
    sched["kickoff"] = kickoff(sched)
    sched["date"] = pd.to_datetime(sched["date"])

    future = sched[sched["kickoff"] > as_of].copy()
    log.info(
        "calendario: %d partite totali, %d ancora da giocare dopo %s",
        len(sched), len(future), as_of.strftime("%Y-%m-%d %H:%M UTC"),
    )
    return future[KEYS + ["matchday", "date", "kickoff"]].sort_values(
        ["kickoff", "home_team"]
    ).reset_index(drop=True)


def load_played(as_of: pd.Timestamp) -> pd.DataFrame:
    """Storico giocato, tagliato alla data di previsione."""
    df = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    df["date"] = pd.to_datetime(df["date"])
    cutoff = as_of.tz_convert(None) if as_of.tz is not None else as_of
    played = df[(df["date"] < cutoff) & df["FTR"].notna()].copy()
    log.info("storico: %d partite giocate fino a %s", len(played), cutoff.date())
    return played


# ---------------------------------------------------------------------------
# Quote per le partite in arrivo
# ---------------------------------------------------------------------------

def _merge_odds_source(
    out: pd.DataFrame, src: pd.DataFrame, etichetta: str
) -> pd.DataFrame:
    """
    Riempie le quote ancora mancanti da una fonte, senza toccare le altre.

    L'ordine delle fonti e' una precedenza, non un'unione: una partita gia'
    coperta non viene sovrascritta da una fonte piu' bassa in classifica.
    """
    cols = [c for c in src.columns if c.startswith("B365")]
    if "B365H" not in cols:
        log.info("%s: nessuna colonna B365, fonte ignorata", etichetta)
        return out

    # Prefisso esplicito invece di affidarsi ai suffissi di merge: pandas
    # aggiunge il suffisso SOLO quando la colonna esiste gia' da entrambe le
    # parti, quindi alla prima fonte non lo aggiungerebbe e la fonte
    # principale non verrebbe mai letta. E' un errore che non da' eccezioni:
    # produce zero quote e sembra un problema di dati.
    tmp = {c: f"__src_{c}" for c in cols}
    src = src.drop_duplicates(subset=KEYS)[KEYS + cols].rename(columns=tmp)
    out = out.merge(src, on=KEYS, how="left", validate="one_to_one")

    prendi = out["odds_from"].isna() & out["__src_B365H"].notna()
    for c, t in tmp.items():
        if c not in out.columns:
            out[c] = np.nan
        out.loc[prendi, c] = out.loc[prendi, t]
    out = out.drop(columns=list(tmp.values()))
    out.loc[prendi, "odds_from"] = etichetta
    log.info("%s: quote per %d partite", etichetta, int(prendi.sum()))
    return out


def load_fixtures_odds() -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Lo snapshot scaricato da football-data, con l'ora in cui e' stato preso."""
    path = config.RAW / "fixtures_odds.parquet"
    if not path.exists():
        log.warning("%s assente: lancia 'python ingest.py --stage fixtures'", path.name)
        return pd.DataFrame(), None

    df = pd.read_parquet(path)
    scaricato = pd.to_datetime(df["downloaded_at"]).max() if "downloaded_at" in df else None
    if scaricato is not None:
        eta = (pd.Timestamp.now(tz="UTC") - scaricato).total_seconds() / 86400
        livello = log.warning if eta > config.FIXTURES_MAX_AGE_DAYS else log.info
        livello("snapshot quote del %s (%.1f giorni fa)",
                scaricato.strftime("%Y-%m-%d %H:%M UTC"), eta)
        if eta > config.FIXTURES_MAX_AGE_DAYS:
            log.warning("piu' vecchio di %d giorni: il file copre solo il turno "
                        "imminente e viene sovrascritto. Rilancia "
                        "'python ingest.py --stage fixtures'.",
                        config.FIXTURES_MAX_AGE_DAYS)
    return df, scaricato


def attach_odds(fixtures: pd.DataFrame, played_all: pd.DataFrame) -> pd.DataFrame:
    """
    Attacca le quote grezze alle partite in arrivo, in ordine di precedenza:

      1. `data/raw/fixtures_odds.parquet`, lo snapshot scaricato da
         football-data.co.uk. E' la fonte normale.
      2. `manual/upcoming_odds.csv`, ripiego facoltativo per le partite che il
         file scaricato non copre — capita, perche' contiene solo il turno
         imminente — o quando il sito e' irraggiungibile.
      3. `matches_master`, che le ha solo per le partite gia' giocate. Serve
         unicamente con `--as-of`, per ricostruire una previsione passata.

    Cio' che resta senza quote non viene predetto.
    """
    out = fixtures.copy()
    out["odds_from"] = pd.NA

    scaricate, _ = load_fixtures_odds()
    if not scaricate.empty:
        out = _merge_odds_source(out, scaricate, "fixtures")

    if UPCOMING_ODDS.exists():
        manuale = normalize_season(apply_name_map(pd.read_csv(UPCOMING_ODDS), load_name_map()))
        out = _merge_odds_source(out, manuale, "manual")
    else:
        log.info("%s assente: nessun ripiego manuale", UPCOMING_ODDS.name)

    out = _merge_odds_source(out, played_all, "matches_master")
    return out


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------

def assert_no_post_match(future: pd.DataFrame) -> None:
    """
    Nessuna colonna post-partita puo' essere valorizzata su una riga futura.

    Se scatta, non e' un dettaglio: significa che nel frame delle previsioni e'
    entrata una partita gia' giocata, e le sue feature di forma conterrebbero
    il risultato che si sta cercando di prevedere.
    """
    presenti = [c for c in POST_MATCH_COLS if c in future.columns]
    sporche = {c: int(future[c].notna().sum()) for c in presenti if future[c].notna().any()}
    assert not sporche, (
        f"colonne post-partita valorizzate su righe future: {sporche}. "
        "Il filtro sulle partite da predire ha un bug."
    )


def build_features(played: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    """
    Feature per le partite in arrivo, accodandole allo storico.

    Non si tocca la logica dei moduli di feature: si allunga il loro ingresso.
    Le colonne che lo storico ha e il calendario no diventano nulle nelle
    righe accodate, che e' precisamente la situazione che form.py gia' gestisce.
    """
    future = fixtures.drop(columns=["kickoff", "odds_from"], errors="ignore").copy()

    # La stessa partita non puo' stare in entrambi i lati: si duplicherebbe la
    # chiave e la squadra vedrebbe due volte la stessa giornata nelle medie
    # mobili. Succede se il filtro sulle date lascia passare una partita gia'
    # giocata, ed e' meglio fermarsi qui che a valle con un errore di merge.
    doppie = future.merge(played[KEYS].drop_duplicates(), on=KEYS, how="inner")
    assert doppie.empty, (
        f"{len(doppie)} partite da predire sono gia' nello storico: "
        f"{doppie[['home_team', 'away_team']].head(5).to_dict('records')}. "
        "Il filtro sulla data del calcio d'inizio non ha funzionato."
    )

    # Le righe future si marcano PRIMA di concatenare. Individuarle a
    # posteriori dalla posizione non funziona: subito dopo si ordina per data,
    # e le ultime righe per indice diventano le piu' recenti, non le future.
    played = played.assign(_is_future=False)
    future = future.assign(_is_future=True)
    combined = pd.concat([played, future], ignore_index=True, sort=False)
    combined = combined.sort_values("date").reset_index(drop=True)

    assert_no_post_match(combined[combined["_is_future"]])
    combined = combined.drop(columns="_is_future")

    form = form_mod.build(combined, save=False)
    market = market_mod.build(combined, save=False)

    feats = combined[KEYS + ["date"]].copy()
    feats = feats.merge(form.drop(columns=["date"], errors="ignore"),
                        on=KEYS, how="left", validate="one_to_one")
    feats = feats.merge(market.drop(columns=["date"], errors="ignore"),
                        on=KEYS, how="left", validate="one_to_one")
    return feats


# ---------------------------------------------------------------------------
# Previsione
# ---------------------------------------------------------------------------

def _report_fixtures_coverage(fixtures: pd.DataFrame, matchday: int | None) -> None:
    """
    Lo snapshot scaricato copre la giornata richiesta?

    Il file di football-data e' una finestra sul turno imminente, non il
    calendario della stagione: chiedere la giornata 12 a settembre non produce
    un errore, produce zero quote. Senza questo messaggio sembrerebbe un
    problema di quote mancanti, e si andrebbe a cercare nel posto sbagliato.
    """
    da_fixtures = int((fixtures["odds_from"] == "fixtures").sum())
    scoperte = int(fixtures["odds_from"].isna().sum())
    etichetta = f"giornata {matchday}" if matchday is not None else "selezione"

    if da_fixtures == 0 and scoperte:
        log.warning(
            "lo snapshot scaricato NON copre la %s: 0 partite su %d. "
            "fixtures.csv contiene solo il turno imminente, non tutta la "
            "stagione. Se la giornata e' lontana, e' normale: torna piu' "
            "vicino alla data, oppure inserisci le quote a mano in %s.",
            etichetta, len(fixtures), UPCOMING_ODDS.name,
        )
    elif scoperte:
        log.warning("%d partite su %d della %s non sono nello snapshot",
                    scoperte, len(fixtures), etichetta)


def top_scorelines(lam_home: float, lam_away: float, n: int = 5, rho: float = config.DC_RHO):
    """I punteggi esatti piu' probabili, dalla stessa matrice di tutto il resto."""
    mat = score_matrix(np.array([lam_home]), np.array([lam_away]), rho=rho)[0]
    flat = [(i, j, mat[i, j]) for i in range(mat.shape[0]) for j in range(mat.shape[1])]
    flat.sort(key=lambda t: t[2], reverse=True)
    return flat[:n]


def predict_fixtures(
    fixtures: pd.DataFrame,
    feats: pd.DataFrame,
    played_all: pd.DataFrame,
    model: Model,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Restituisce (previsioni, scartate-per-mancanza-di-quote)."""
    # La data la porta gia' il calendario: tenerla anche dalle feature
    # genererebbe date_x/date_y e romperebbe tutto il codice a valle.
    rows = fixtures.merge(
        feats.drop(columns=["date"], errors="ignore"),
        on=KEYS, how="left", validate="one_to_one",
    )

    has_odds = rows["mkt_lambda_home"].notna() & rows["mkt_lambda_away"].notna()
    skipped = rows[~has_odds].copy()
    # Perche' e' stata saltata: mancano le quote 1X2, o mancano quelle
    # over/under? Sono due rimedi diversi e il messaggio generico li
    # confondeva. Servono entrambe: il totale gol viene dall'over/under, la
    # supremazia dall'1X2, e i due lambda nascono dal loro incrocio.
    if not skipped.empty:
        no_1x2 = skipped["mkt_p_home"].isna()
        no_ou = skipped["mkt_p_over25"].isna()
        skipped["manca"] = np.where(
            no_1x2 & no_ou, "1X2 e over/under",
            np.where(no_1x2, "1X2", "over/under 2.5"),
        )
    usable = rows[has_odds].copy().reset_index(drop=True)
    if usable.empty:
        return usable, skipped

    pred = model.predict(usable)
    out = pd.concat([usable, pred], axis=1)

    # Le quote effettivamente usate, recuperate dal book che market.py ha
    # scelto. Registrarle permette, a posteriori, di distinguere un errore del
    # modello da un prezzo cambiato fra previsione e calcio d'inizio.
    for etichetta, suffisso in (("odds_home", "H"), ("odds_draw", "D"), ("odds_away", "A")):
        out[etichetta] = [
            float(r[f"{r['mkt_1x2_source']}{suffisso}"])
            if pd.notna(r.get("mkt_1x2_source")) and f"{r['mkt_1x2_source']}{suffisso}" in r
            else np.nan
            for _, r in out.iterrows()
        ]
    # Book e provenienza insieme: "B365-fixtures" non e' la stessa cosa di
    # "B365-manual", perche' il primo e' lo snapshot ufficiale delle 17:00 del
    # venerdi' e il secondo e' una quota copiata a mano in un momento ignoto.
    # A distanza di mesi la differenza serve a interpretare il track record.
    out["odds_source"] = out["mkt_1x2_source"].astype("string") + "-" + out["odds_from"].astype("string")
    return out, skipped


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

def already_logged(model_version: str, path=PREDICTIONS_LOG) -> set[tuple[str, ...]]:
    """Le partite gia' registrate per questo modello, come insieme di chiavi."""
    if not path.exists():
        return set()
    df = pd.read_csv(path, usecols=KEYS + ["model_version"])
    df = df[df["model_version"] == model_version]
    return set(map(tuple, df[KEYS].astype(str).to_numpy()))


def append_log(preds: pd.DataFrame, model_version: str, now: pd.Timestamp,
               path=PREDICTIONS_LOG) -> tuple[int, int]:
    """
    Aggiunge in coda al registro. Restituisce (scritte, saltate perche' gia'
    presenti).

    APPEND-ONLY E IDEMPOTENTE INSIEME
    Il file non viene mai riscritto: si aggiunge in fondo e basta. Ma una
    partita gia' registrata per lo stesso modello non viene aggiunta di nuovo,
    cosi' rilanciare il ciclo tre volte nella stessa settimana non moltiplica
    le righe.

    PERCHE' NON SI AGGIORNA LA RIGA SE LE QUOTE SONO CAMBIATE
    Perche' falsificherebbe il track record a posteriori: la previsione
    registrata deve restare quella fatta con l'informazione di quel momento,
    anche quando col senno di poi era peggiore. Se serve una seconda opinione
    su quote nuove, si cambia `model_version` — la chiave di deduplicazione e'
    (partita, modello), quindi la riga nuova entra e la vecchia resta.
    """
    if preds.empty:
        return 0, 0

    gia = already_logged(model_version, path)
    if gia:
        chiavi = list(map(tuple, preds[KEYS].astype(str).to_numpy()))
        nuove = [k not in gia for k in chiavi]
        saltate = len(preds) - sum(nuove)
        preds = preds[nuove]
        if saltate:
            log.info("%d partite gia' registrate per '%s': non riscritte",
                     saltate, model_version)
        if preds.empty:
            return 0, saltate
    else:
        saltate = 0

    rec = pd.DataFrame({
        "timestamp_prediction": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_version": model_version,
        "league": preds["league"],
        "season": preds["season"],
        "matchday": preds["matchday"],
        "match_date": pd.to_datetime(preds["date"]).dt.strftime("%Y-%m-%d"),
        "home_team": preds["home_team"],
        "away_team": preds["away_team"],
        "lambda_home": preds["lambda_home"].round(4),
        "lambda_away": preds["lambda_away"].round(4),
        "p_home": preds["p_home"].round(5),
        "p_draw": preds["p_draw"].round(5),
        "p_away": preds["p_away"].round(5),
        "p_over25": preds["p_over25"].round(5),
        "p_btts": preds["p_btts"].round(5),
        "odds_home": preds["odds_home"],
        "odds_draw": preds["odds_draw"],
        "odds_away": preds["odds_away"],
        "odds_source": preds["odds_source"],
    })[LOG_COLUMNS]

    path.parent.mkdir(parents=True, exist_ok=True)

    # Copia di sicurezza prima di ogni scrittura. Il registro e' l'unico dato
    # del progetto che non si puo' rigenerare: tutto il resto si riscarica, le
    # previsioni no, perche' vanno scritte prima del calcio d'inizio e quel
    # momento non torna. Una generazione di backup costa un copyfile.
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    # mode="a" e header solo alla creazione: il file non viene mai riaperto in
    # scrittura, quindi nessuna riga gia' scritta puo' essere persa o alterata.
    prima = sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0
    rec.to_csv(path, mode="a", header=not path.exists(), index=False)
    dopo = sum(1 for _ in path.open(encoding="utf-8"))
    assert dopo >= prima, f"il registro si e' accorciato: {prima} -> {dopo} righe"
    log.info("registrate %d previsioni in %s", len(rec), path.name)
    return len(rec), saltate


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(preds: pd.DataFrame, skipped: pd.DataFrame, team: str | None) -> None:
    if not preds.empty:
        tab = pd.DataFrame({
            "data": pd.to_datetime(preds["date"]).dt.strftime("%a %d/%m"),
            "partita": preds["home_team"] + " - " + preds["away_team"],
            "1": preds["p_home"].round(3),
            "X": preds["p_draw"].round(3),
            "2": preds["p_away"].round(3),
            "over 2.5": preds["p_over25"].round(3),
            "gol-gol": preds["p_btts"].round(3),
            "lam casa": preds["lambda_home"].round(2),
            "lam fuori": preds["lambda_away"].round(2),
            "quote": preds.apply(
                lambda r: f"{r['odds_home']:.2f}/{r['odds_draw']:.2f}/{r['odds_away']:.2f}"
                if pd.notna(r["odds_home"]) else "-", axis=1),
        })
        print(tab.to_string(index=False))

    if not skipped.empty:
        print(f"\n--- {len(skipped)} partite NON predette: quote di apertura incomplete")
        for _, r in skipped.iterrows():
            print(f"    {pd.to_datetime(r['date']).strftime('%d/%m')}  "
                  f"{r['home_team']} - {r['away_team']:<12} manca: {r.get('manca', 'quote')}")
        print(f"\n    Servono ENTRAMBI i mercati in {UPCOMING_ODDS.name}:")
        print("      B365H, B365D, B365A        -> supremazia (chi vince)")
        print("      B365>2.5, B365<2.5         -> totale gol atteso")
        print("    I due lambda nascono dal loro incrocio: con il solo 1X2 si sa")
        print("    chi e' favorito ma non quanti gol si giocano, e senza quello")
        print("    non esistono ne' i gol attesi ne' i punteggi esatti.")
        print("    Non vengono imputate: una previsione inventata e' peggio di nessuna.")

    focus = team or config.SQUADRA_TARGET
    sel = preds[(preds["home_team"] == focus) | (preds["away_team"] == focus)] if not preds.empty else preds
    if sel.empty:
        return

    print(f"\n=== {focus.upper()} ===")
    for _, r in sel.iterrows():
        casa = r["home_team"] == focus
        print(f"\n{r['home_team']} - {r['away_team']}   "
              f"{pd.to_datetime(r['date']).strftime('%A %d/%m/%Y')}   "
              f"({focus} jn {'casa' if casa else 'trasferta'})".replace(" jn ", " in "))
        print(f"  1 {r['p_home']:.1%}   X {r['p_draw']:.1%}   2 {r['p_away']:.1%}"
              f"   |  over 2.5 {r['p_over25']:.1%}   gol-gol {r['p_btts']:.1%}")
        print(f"  gol attesi: {r['home_team']} {r['lambda_home']:.2f} - "
              f"{r['lambda_away']:.2f} {r['away_team']}")

        top = top_scorelines(r["lambda_home"], r["lambda_away"], n=5)
        print(f"  risultato piu' probabile: {top[0][0]}-{top[0][1]} ({top[0][2]:.1%})")
        print("  primi cinque punteggi:")
        for i, j, p in top:
            print(f"    {i}-{j}   {p:6.2%}")


# ---------------------------------------------------------------------------

@dataclass
class Esito:
    """
    Cosa e' successo in una passata di previsione.

    Serve a src/weekly.py, che deve poter riferire quante partite sono state
    predette, quante saltate perche' gia' registrate e quante scoperte, senza
    rifare i conti su un frame che non ha visto costruire.
    """
    matchday: int | None = None
    preds: pd.DataFrame = field(default_factory=pd.DataFrame)
    skipped: pd.DataFrame = field(default_factory=pd.DataFrame)
    scritte: int = 0
    duplicate: int = 0
    model_version: str = ""
    now: pd.Timestamp | None = None

    @property
    def vuoto(self) -> bool:
        return self.preds.empty and self.skipped.empty


def run(
    matchday: int | None = None,
    team: str | None = None,
    use_next: bool = False,
    as_of: pd.Timestamp | None = None,
    model: Model | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> Esito:
    now = pd.Timestamp(datetime.now(timezone.utc)) if as_of is None else as_of
    model = model or MarketOnly()
    esito = Esito(model_version=model.name, now=now)

    fixtures = load_fixtures(now)
    if fixtures.empty:
        log.warning("nessuna partita futura nel calendario: serve un'ingestion aggiornata")
        return esito

    if team:
        fixtures = fixtures[(fixtures["home_team"] == team) | (fixtures["away_team"] == team)]
        if fixtures.empty:
            log.warning("nessuna partita futura per '%s': nome giusto?", team)
            return esito
    if use_next or (team and matchday is None):
        # Con --team da solo si intende la prossima partita di quella squadra,
        # non tutte le trentasei che restano da giocare.
        matchday = int(fixtures.loc[fixtures["kickoff"].idxmin(), "matchday"])
        log.info("prima giornata utile: %d", matchday)
    if matchday is not None:
        fixtures = fixtures[fixtures["matchday"] == matchday]
    esito.matchday = matchday
    if fixtures.empty:
        log.warning("nessuna partita corrisponde ai filtri")
        return esito

    played = load_played(now)
    played_all = pd.read_parquet(config.INTERIM / "matches_master.parquet")
    fixtures = attach_odds(fixtures.reset_index(drop=True), played_all)
    _report_fixtures_coverage(fixtures, matchday)

    feats = build_features(played, fixtures)
    preds, skipped = predict_fixtures(fixtures, feats, played_all, model)

    # Il timestamp deve precedere il calcio d'inizio di OGNI partita registrata.
    # Se non fosse cosi' la previsione non sarebbe una previsione, e il track
    # record costruito su questo log non varrebbe niente.
    if not preds.empty:
        ko = preds["kickoff"]
        assert (ko > now).all(), (
            f"previsione a {now} non precede il calcio d'inizio di "
            f"{int((ko <= now).sum())} partite"
        )

    # `quiet` serve a src/weekly.py, che il report lo impagina da solo: senza,
    # la stessa giornata verrebbe stampata due volte in due formati diversi.
    if not quiet:
        print(f"\n=== GIORNATA {matchday if matchday is not None else '(tutte)'} - "
              f"previsione del {now.strftime('%Y-%m-%d %H:%M UTC')} - "
              f"modello: {model.name} ===\n")
        print_report(preds, skipped, team)

    esito.preds, esito.skipped = preds, skipped
    if dry_run:
        # Anche in prova si conta quante sarebbero saltate come duplicate:
        # e' l'informazione che dice se c'e' davvero qualcosa da fare.
        gia = already_logged(model.name)
        if not preds.empty and gia:
            chiavi = map(tuple, preds[KEYS].astype(str).to_numpy())
            esito.duplicate = sum(k in gia for k in chiavi)
        log.info("dry-run: niente scritto nel registro")
    else:
        destinazione = PREDICTIONS_LOG if as_of is None else BACKFILL_LOG
        if as_of is not None:
            log.warning("previsione ricostruita con --as-of: va in %s, non nel "
                        "track record", destinazione.name)
        esito.scritte, esito.duplicate = append_log(preds, model.name, now, path=destinazione)
    return esito


def main() -> None:
    ap = argparse.ArgumentParser(description="Previsione settimanale")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--matchday", type=int, help="giornata da predire")
    g.add_argument("--next", action="store_true", help="prima giornata con partite future")
    ap.add_argument("--team", help="filtra su una squadra")
    ap.add_argument("--as-of", help="finge che 'adesso' sia questa data UTC (YYYY-MM-DD), "
                                    "per ricostruire previsioni passate o provare la pipeline")
    ap.add_argument("--dry-run", action="store_true", help="non scrivere nel registro")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)

    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else None
    if not (args.matchday or args.next or args.team):
        args.next = True
    run(matchday=args.matchday, team=args.team, use_next=args.next,
        as_of=as_of, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
