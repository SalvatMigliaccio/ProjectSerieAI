"""
Blocco B, le due migliorie che CLAUDE.md elencava. Misurate SOLO in validazione.

LE DUE COLONNE NUOVE (set GIOCATORI_V2, `features/sets.py`)

  quota_xga_assente     la quota di xG+xA della rosa che manca, da Understat.
                        Sostituisce quota_ga_assente (gol+assist), dove un
                        attaccante che non ha ancora segnato pesa zero.
  quota_minuti_lungo    la quota di minuti assenti, piu' i LUNGODEGENTI: chi e'
                        in lista senza un minuto in stagione pesa per la quota
                        di minuti che aveva nella stagione PRECEDENTE.
                        Sostituisce quota_minuti_assenti, dove pesa zero.

Entrambe rispettano la stessa disciplina del blocco: lo stato di ogni partita
guarda solo le partite con data strettamente precedente (`pesi_alla_data`), e
la stagione precedente e' chiusa — informazione lecita a T-24h.

LE VARIANTI, TUTTE CONTRO LA SPECIFICA DI OGGI (BASE + GIOCATORI)

  xga       GIOCATORI con quota_xga al posto di quota_ga
  lungo     GIOCATORI con quota_minuti_lungo al posto di quota_minuti
  entrambe  tutte e due le sostituzioni

Il confronto e' ANNIDATO (stesso M5, cambia solo la colonna), che e' quello
che decide un blocco: 4.3 volte meno rumoroso del confronto col mercato.

DOVE SI MISURA E COSA DECIDE. Solo validazione (2021/22, 2022/23), media di 5
semi, come la forma per sede: il test set non si tocca e m resta 3. Regola
scritta prima dei numeri:
  - intervallo che contiene lo zero, o differenza >= 0 -> variante scartata,
    a costo zero;
  - intervallo tutto sotto zero -> IPOTESI PRE-REGISTRATA, con la specifica
    congelata com'e', da testare quando ci saranno dati nuovi. Non promuove
    niente: la validazione non e' il test.

LIMITE DICHIARATO. Le assenze partono dal 2021/22: nella prima stagione di
validazione il modello impara il blocco solo dalle giornate gia' giocate di
quella stessa stagione. La validazione vede soprattutto il 2022/23.

Uso:
    python -m goalmodel.experiments.bloccoB_migliorie --descrivi
    python -m goalmodel.experiments.bloccoB_migliorie --lancia
    python -m goalmodel.experiments.bloccoB_migliorie --analizza
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import subprocess
import sys

import numpy as np
import pandas as pd

from .. import config, data, experiments
from ..evaluation.evaluate import KEYS, load_dataset, paired_pairs, walk_forward
from ..features import players
from ..features import sets as sets_mod
from ..models.baseline import PRED_COLS
from .modelli import M5Set, media_log_lambda

log = logging.getLogger("bloccoB_migliorie")

SEMI = (0, 1, 2, 3, 4)
# Minuti di una squadra in una stagione intera: 11 giocatori x 90' x 38 giornate.
MINUTI_STAGIONE = 11 * 90 * 38

_GA = ("home_quota_ga_assente", "away_quota_ga_assente", "diff_quota_ga_assente")
_MIN = ("home_quota_minuti_assenti", "away_quota_minuti_assenti", "diff_quota_minuti_assenti")
_XGA = ("home_quota_xga_assente", "away_quota_xga_assente", "diff_quota_xga_assente")
_LUNGO = ("home_quota_minuti_lungo", "away_quota_minuti_lungo", "diff_quota_minuti_lungo")

# variante -> (set, colonne tolte). Le colonne nuove entrano con GIOCATORI_V2;
# quelle che NON servono alla variante si tolgono, cosi' ogni variante cambia
# una cosa sola rispetto al riferimento.
VARIANTI: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "giocatori": (("BASE", "GIOCATORI"), ()),
    "xga": (("BASE", "GIOCATORI", "GIOCATORI_V2"), _GA + _LUNGO),
    "lungo": (("BASE", "GIOCATORI", "GIOCATORI_V2"), _MIN + _XGA),
    "entrambe": (("BASE", "GIOCATORI", "GIOCATORI_V2"), _GA + _MIN),
}
RIFERIMENTO = "giocatori"

# IL CONTROLLO: la validazione vede questo blocco? Le tre varianti risultano
# tutte a +-0.00001 dal riferimento, con intervalli strettissimi, e la
# spiegazione piu' probabile e' che in validazione il modello quasi non usi le
# colonne dei giocatori (le assenze partono proprio dal 2021/22). Si verifica
# misurando il blocco INTERO contro BASE, come sul test set (-0.00027): se qui
# e' ~0, la validazione e' cieca per il blocco e il "no" alle varianti non
# dice niente; se il blocco funziona, quel "no" e' solido.
CONTROLLO = {"base": (("BASE",), ())}
COLONNE = KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR", "model"] + PRED_COLS


# ---------------------------------------------------------------------------
# Le colonne nuove
# ---------------------------------------------------------------------------

def xga_per_partita() -> pd.DataFrame:
    """
    xG e xA di ogni giocatore in ogni partita di Serie A, da Understat.

    xA = la somma dell'xG dei tiri che ha servito. Si aggancia ai minuti di
    FBref per (stagione, squadra, giocatore normalizzato, data): Understat e
    FBref scrivono i nomi in modo diverso, ed e' `normalizza_nome` a renderli
    confrontabili, come per le assenze.
    """
    from ..normalize import load_name_map

    tiri = data.load_raw("understat_shots", "goalmodel ingest --stage shots")
    tiri = tiri[tiri["league"].astype(str) == "ITA-Serie A"].copy()
    col_xg = players._colonna(tiri, "xg", "xG")
    col_ass = players._colonna(tiri, "assist_player", "player_assisted")
    tiri["team"] = tiri["team"].astype(str).replace(load_name_map())
    tiri["date"] = pd.to_datetime(tiri["date"]).dt.tz_localize(None).dt.normalize()
    tiri["season"] = tiri["season"].astype(str)
    tiri[col_xg] = pd.to_numeric(tiri[col_xg], errors="coerce").fillna(0.0)

    tiri["player"] = tiri["player"].astype(str).str.translate(_TRASLITTERA)
    tiri[col_ass] = tiri[col_ass].astype("string").str.translate(_TRASLITTERA)
    xg = (tiri.assign(chiave_giocatore=players.normalizza_nome(tiri["player"].astype(str)))
          .groupby(["season", "team", "chiave_giocatore", "date"], as_index=False)[col_xg].sum()
          .rename(columns={col_xg: "xg"}))
    serviti = tiri[tiri[col_ass].notna()]
    xa = (serviti.assign(chiave_giocatore=players.normalizza_nome(serviti[col_ass].astype(str)))
          .groupby(["season", "team", "chiave_giocatore", "date"], as_index=False)[col_xg].sum()
          .rename(columns={col_xg: "xa"}))
    out = xg.merge(xa, on=["season", "team", "chiave_giocatore", "date"], how="outer").fillna(0.0)
    out["xga"] = out["xg"] + out["xa"]
    return out


# Lettere che la decomposizione Unicode non riduce all'ASCII: senza, Yildiz
# scritto con la i turca senza punto resta "y ld z" in una fonte e "yildiz"
# nell'altra. Scritte come codici: a vista si confondono con le lettere latine.
_TRASLITTERA = str.maketrans({chr(0x131): "i", chr(0x130): "I", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
                              "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae", "Æ": "AE"})


def _token(chiave: str) -> set[str]:
    return {t for t in chiave.split() if len(t) >= 4}


def riconcilia_nomi(x: pd.DataFrame, g: pd.DataFrame) -> pd.DataFrame:
    """
    Riporta i nomi di Understat sulla chiave di FBref, quando si possono riconoscere.

    Il 10% dell'xG non si agganciava, e non a caso: era quasi tutto di
    attaccanti — "Franck Zambo" contro "Andre Frank Zambo Anguissa", "Nico
    Paz" contro "Nicolas Paz", la "i" turca di Yildiz. Per ogni nome di
    Understat senza gemello esatto nella stessa (stagione, squadra) si cerca
    il giocatore di FBref che condivide un pezzo di nome di almeno 4 lettere,
    e lo si accetta SOLO se il candidato e' uno solo: due candidati sono un
    dubbio, e un dubbio resta non agganciato piuttosto che attribuire l'xG di
    un giocatore a un altro.

    Solo qui, non in `players.normalizza_nome`: quella e' parte della
    specifica congelata di GIOCATORI.
    """
    rosa = g.groupby(["season", "team"])["chiave_giocatore"].apply(set).to_dict()
    mappa: dict[tuple[str, str, str], str] = {}
    for (s, t, k) in x[["season", "team", "chiave_giocatore"]].drop_duplicates().itertuples(index=False):
        nomi = rosa.get((s, t), set())
        if k in nomi:
            continue
        cand = [n for n in nomi if _token(k) & _token(n)]
        if len(cand) == 1:
            mappa[(s, t, k)] = cand[0]
    x = x.copy()
    x["chiave_giocatore"] = [mappa.get((s, t, k), k) for s, t, k in
                             zip(x["season"], x["team"], x["chiave_giocatore"], strict=True)]
    log.info("nomi Understat riconciliati per somiglianza: %d", len(mappa))
    return x.groupby(["season", "team", "chiave_giocatore", "date"], as_index=False)[
        ["xg", "xa", "xga"]].sum()


def giocatori_con_xga() -> pd.DataFrame:
    """
    Le righe di `players.carica_giocatori`, con `ga` sostituito da xG+xA.

    Stesso frame, stessa colonna: `players.build` lo riceve iniettato e calcola
    la quota come fa per gol+assist, senza toccarne una riga. Il +-1 giorno fra
    FBref e Understat si assorbe con un asof sulla data.
    """
    g = players.carica_giocatori()
    g["date"] = pd.to_datetime(g["date"]).dt.normalize()
    x = riconcilia_nomi(xga_per_partita(), g)
    m = pd.merge_asof(
        g.sort_values("date"), x.rename(columns={"date": "data_us"}).sort_values("data_us"),
        left_on="date", right_on="data_us", by=["season", "team", "chiave_giocatore"],
        direction="nearest", tolerance=pd.Timedelta(days=1))
    agganciati = m["data_us"].notna() & (m["minuti"] > 0)
    log.info("righe con minuti agganciate a Understat: %.1f%%",
             100 * agganciati.sum() / max((m["minuti"] > 0).sum(), 1))
    m["ga"] = m["xga"].fillna(0.0)
    return m[g.columns]


def quota_stagione_precedente() -> pd.DataFrame:
    """
    La quota di minuti di ogni giocatore nella stagione PRECEDENTE.

    Minuti / minuti di una stagione intera, con qualunque squadra: chi ha
    cambiato maglia d'estate ha comunque un'importanza misurata. La stagione e'
    chiusa prima della prima partita di quella dopo: lecito a T-24h.
    """
    s = data.load_raw("fbref_player_season", "goalmodel ingest --stage player_stats")
    col = players._colonna(s, "playing_time_min", "min", "minutes")
    s = s.assign(chiave_giocatore=players.normalizza_nome(s["player"].astype(str)),
                 minuti=pd.to_numeric(s[col], errors="coerce").fillna(0.0),
                 season=s["season"].astype(str))
    per = s.groupby(["season", "chiave_giocatore"], as_index=False)["minuti"].sum()

    def dopo(stagione: str) -> str:
        a = int(stagione[:2]) + 1
        return f"{a:02d}{(a + 1) % 100:02d}"

    per["season"] = per["season"].map(dopo)
    per["quota_prec"] = (per["minuti"] / MINUTI_STAGIONE).clip(upper=1 / 11)
    return per[["season", "chiave_giocatore", "quota_prec"]]


def colonne_nuove(df: pd.DataFrame) -> pd.DataFrame:
    """Le sei colonne di GIOCATORI_V2, una riga per partita, in memoria."""
    assenze = players.carica_assenze()

    # 1. xG+xA: la stessa costruzione del blocco, con il peso cambiato.
    # Tutto si aggancia per CHIAVE, mai per posizione: `build` riordina per data,
    # e due chiamate con pesi diversi non promettono lo stesso ordine di righe.
    v_xga = players.build(df, save=False, assenze=assenze, giocatori=giocatori_con_xga())
    out = v_xga[KEYS + [f"{lato}_quota_ga_assente" for lato in ("home", "away", "diff")]].rename(
        columns={f"{lato}_quota_ga_assente": f"{lato}_quota_xga_assente"
                 for lato in ("home", "away", "diff")})

    # 2. Lungodegenti: la quota di oggi piu' chi e' in lista senza minuti.
    base = players.build(df, save=False, assenze=assenze)
    giocatori = players.carica_giocatori()
    stato = players.pesi_alla_data(giocatori)
    partite = df[KEYS + ["date"]].copy()
    partite["date"] = pd.to_datetime(partite["date"]).dt.normalize()
    partite["season"] = partite["season"].astype(str)
    assenti = assenze[assenze["indisponibile"]].merge(partite, on=KEYS, how="inner")
    assenti = pd.merge_asof(
        assenti.sort_values("date"), stato.sort_values("date"),
        on="date", by=["season", "team", "chiave_giocatore"], direction="backward")
    lungo = assenti[assenti["minuti_ad"].isna()].merge(
        quota_stagione_precedente(), on=["season", "chiave_giocatore"], how="left")
    extra = lungo.groupby(KEYS + ["team"], as_index=False)["quota_prec"].sum()
    q = base[KEYS + ["home_quota_minuti_assenti", "away_quota_minuti_assenti"]].copy()
    for lato in ("home", "away"):
        e = (extra[extra["team"] == extra[f"{lato}_team"]][KEYS + ["quota_prec"]]
             .rename(columns={"quota_prec": f"{lato}_extra"}))
        q = q.merge(e, on=KEYS, how="left", validate="one_to_one")
        # La quota resta NaN dove il blocco non ha copertura: NaN + extra = NaN,
        # e si vuole cosi' — "non so" non diventa "squadra al completo".
        q[f"{lato}_quota_minuti_lungo"] = (
            q[f"{lato}_quota_minuti_assenti"] + q[f"{lato}_extra"].fillna(0.0)
        ).clip(upper=1.0)
    q["diff_quota_minuti_lungo"] = q["home_quota_minuti_lungo"] - q["away_quota_minuti_lungo"]
    return out.merge(q[KEYS + list(_LUNGO)], on=KEYS, how="left", validate="one_to_one")


def dataset() -> pd.DataFrame:
    """Il dataset di valutazione con GIOCATORI_V2 agganciato in memoria."""
    df = load_dataset()
    df["season"] = df["season"].astype(str)
    nuove = colonne_nuove(df)
    attese = sorted(sets_mod.SETS["GIOCATORI_V2"].colonne)
    if sorted(c for c in nuove.columns if c not in KEYS) != attese:
        raise AssertionError("le colonne prodotte non coincidono con il set registrato")
    return df.merge(nuove, on=KEYS, how="left", validate="one_to_one")


def descrivi() -> None:
    df = dataset()
    val = df[df["season"].isin(config.VALIDATION_SEASONS)]
    print(f"validazione {config.VALIDATION_SEASONS}: {len(val)} partite")
    for vecchia, nuova in (("home_quota_ga_assente", "home_quota_xga_assente"),
                           ("home_quota_minuti_assenti", "home_quota_minuti_lungo")):
        print(f"  {nuova:<28} copertura {val[nuova].notna().mean():.1%}  "
              f"media {val[nuova].mean():.3f} (era {val[vecchia].mean():.3f})  "
              f"r con la gemella {val[vecchia].corr(val[nuova]):.3f}")


# ---------------------------------------------------------------------------
# Misura
# ---------------------------------------------------------------------------

def file_uscita(variante: str, seme: int):
    return experiments.percorso(f"wf_bloccoB_v2_validazione_{variante}_s{seme}.parquet")


def esegui(seme: int, quali: dict | None = None) -> None:
    experiments.proteggi_produzione()
    quali = quali or VARIANTI
    df = dataset()
    modelli = [M5Set(sets, seed=seme, togli=togli, etichetta=v)
               for v, (sets, togli) in quali.items()]
    preds = walk_forward(df, modelli, test_seasons=config.VALIDATION_SEASONS)
    for v, m in zip(quali, modelli, strict=True):
        preds[preds["model"] == m.name][COLONNE].to_parquet(file_uscita(v, seme), index=False)
    log.info("seme %d scritto", seme)


def lancia(controllo: bool = False) -> None:
    """Un processo per seme, come forma_venue: LightGBM qui usa un thread solo."""
    quali = CONTROLLO if controllo else VARIANTI
    coda = [s for s in SEMI if not all(file_uscita(v, s).exists() for v in quali)]
    with contextlib.ExitStack() as stack:
        processi = []
        for s in coda:
            fh = stack.enter_context(
                experiments.percorso(f"log_bloccoB_v2_s{s}.txt").open("w", encoding="utf-8"))
            processi.append((s, fh, subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "goalmodel.experiments.bloccoB_migliorie",
                 "--seed", str(s), *(["--controllo"] if controllo else [])],
                stdout=fh, stderr=subprocess.STDOUT, cwd=config.ROOT)))
        for s, _fh, p in processi:
            p.wait()
            print(f"seme {s}: {'ok' if p.returncode == 0 else 'FALLITO'}", flush=True)


def _media(variante: str) -> pd.DataFrame:
    pezzi = [pd.read_parquet(file_uscita(variante, s)).sort_values(KEYS).reset_index(drop=True)
             for s in SEMI]
    base = pezzi[0]
    for p in pezzi[1:]:
        if not p[KEYS].equals(base[KEYS]):
            raise ValueError("semi con righe diverse: non si mediano")
    out = base[KEYS + ["date", "matchday", "FTHG", "FTAG", "FTR"]].copy()
    out["model"] = f"media5 {variante}"
    return pd.concat([out, media_log_lambda([p[PRED_COLS] for p in pezzi], base.index)], axis=1)


def analizza() -> None:
    rif = _media(RIFERIMENTO)
    righe = []
    for v in VARIANTI:
        if v == RIFERIMENTO:
            continue
        alt = _media(v)
        r = paired_pairs(pd.concat([alt, rif], ignore_index=True),
                         [(alt["model"].iloc[0], rif["model"].iloc[0])]).iloc[0]
        esito = ("IPOTESI PRE-REGISTRATA" if r["ic_alto"] < 0 else "scartata")
        righe.append({"variante": v, "differenza": r["differenza"],
                      "ic_basso": r["ic_basso"], "ic_alto": r["ic_alto"],
                      "quota_peggiore": r["quota_peggiore"], "esito": esito})
    tab = pd.DataFrame(righe)

    controllo = None
    if all(file_uscita("base", s).exists() for s in SEMI):
        b = _media("base")
        r = paired_pairs(pd.concat([rif, b], ignore_index=True),
                         [(rif["model"].iloc[0], b["model"].iloc[0])]).iloc[0]
        controllo = r
        print(f"\nCONTROLLO giocatori - base (sul test valeva -0.00027): "
              f"{r['differenza']:+.5f} [{r['ic_basso']:+.5f}, {r['ic_alto']:+.5f}]")
    print(f"validazione {config.VALIDATION_SEASONS}, media di {len(SEMI)} semi — "
          f"variante meno {RIFERIMENTO}; test non toccato, m resta 3")
    print(tab.to_string(index=False, float_format=lambda x: f"{x:+.5f}"))

    testo = [
        "# Blocco B, migliorie — validazione",
        "",
        f"Stagioni {', '.join(config.VALIDATION_SEASONS)}, media di {len(SEMI)} semi, "
        f"confronto annidato contro BASE+GIOCATORI. Test set non toccato: m resta 3.",
        "",
        "| variante | differenza | IC 95% | quota peggiore | esito |",
        "|---|---|---|---|---|",
        *[f"| {r.variante} | {r.differenza:+.5f} | [{r.ic_basso:+.5f}, {r.ic_alto:+.5f}] "
          f"| {r.quota_peggiore:.3f} | {r.esito} |" for r in tab.itertuples()],
        "",
        "Regola scritta prima: intervallo tutto sotto zero -> ipotesi pre-registrata "
        "(non promossa); altrimenti scartata.",
        "",
        *([
            "## Controllo: la validazione vede il blocco?",
            "",
            f"BASE+GIOCATORI meno BASE, stessa validazione: **{controllo['differenza']:+.5f}**, "
            f"IC [{controllo['ic_basso']:+.5f}, {controllo['ic_alto']:+.5f}] "
            "(sul test set: -0.00027, IC [-0.00054, -0.00001]).",
            "",
            ("**Il controllo e' a cavallo dello zero: la validazione e' CIECA per il "
             "blocco, e gli 'scartata' qui sopra non decidono niente.** Le varianti "
             "diventano l'ipotesi pre-registrata n.3 di CLAUDE.md, da testare sulla "
             "stagione 2026/27."
             if controllo["ic_basso"] <= 0 <= controllo["ic_alto"] else
             "Il controllo esclude lo zero: la validazione vede il blocco, e l'esito "
             "delle varianti qui sopra vale."),
        ] if controllo is not None else []),
    ]
    experiments.percorso("riassunto_bloccoB_migliorie_validazione.md").write_text(
        "\n".join(testo) + "\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="Blocco B, migliorie, in validazione")
    ap.add_argument("--descrivi", action="store_true")
    ap.add_argument("--lancia", action="store_true", help="i 5 semi in parallelo")
    ap.add_argument("--analizza", action="store_true")
    ap.add_argument("--seed", type=int, help="un seme solo (lo usa --lancia)")
    ap.add_argument("--controllo", action="store_true",
                    help="solo la variante BASE, per vedere se la validazione vede il blocco")
    args = ap.parse_args()
    if args.seed is not None:
        esegui(args.seed, CONTROLLO if args.controllo else None)
    elif args.lancia:
        lancia(controllo=args.controllo)
    elif args.analizza:
        analizza()
    else:
        descrivi()


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
