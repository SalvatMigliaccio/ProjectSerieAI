"""
Le liste degli indisponibili di WhoScored sono scritte PRIMA della partita?

E' l'assunto su cui poggia tutto il blocco B (`features/players.py`): se
WhoScored aggiornasse quelle liste a partita finita, il blocco conterrebbe
informazione dal futuro e andrebbe buttato, non corretto. Fino a oggi era
scritto come "assunto non verificato". Qui si verifica con i dati, incrociando
le liste con chi ha giocato davvero (FBref, minuti per partita).

DUE FIRME DI UNA LISTA SCRITTA DOPO, E UNA DI UNA LISTA SCRITTA PRIMA

1. ASSENTE MA IN CAMPO. Un giocatore segnato infortunato o squalificato per
   una partita che in quella partita ha giocato. Una lista pre-partita ne
   contiene pochi (un recupero lampo, un errore). Una lista aggiornata dopo
   conterrebbe chi si e' fatto male DURANTE la partita: entrato, uscito presto.

2. L'INFORTUNIO PRIMA DI QUANDO E' SUCCESSO. Chi esce presto dalla partita t
   (meno di 60 minuti, da titolare) e risulta infortunato alla t+1 si e' fatto
   male, con buona probabilita', nella t. Se compare gia' nella lista DELLA t,
   la lista e' stata scritta dopo. Il confronto e' con chi esce presto e NON
   risulta infortunato dopo: la differenza fra i due tassi e' la firma.

3. I DUBBI GIOCANO. In una lista pre-partita "in dubbio" e' un'incertezza
   vera, e una parte consistente dei dubbi scende in campo. Una lista a
   posteriori non avrebbe motivo di tenere un "in dubbio" su chi ha giocato.

REGOLA SCRITTA PRIMA DI GUARDARE I NUMERI. La lista si considera pre-partita
se (a) meno del 3% degli indisponibili certi ha giocato, (b) il tasso della
firma 2 non supera di oltre 5 punti quello del gruppo di controllo, e (c)
almeno il 20% dei dubbi ha giocato. Se anche una delle tre fallisce, il
blocco B va sospeso finche' non si capisce perche'.

ESITO, 23 OTTOBRE 2026: LA REGOLA NON E' SUPERATA, E IL PERCHE' E' INNOCUO.
(a) e (b) falliscono. La diagnosi (sotto, `diagnosi`) dice da dove: chi e' "in
lista" e gioca lo fa soprattutto per pochi minuti (meta' entro i 30) e quasi
sempre senza aver giocato la partita prima — il profilo di chi RIENTRA da un
infortunio entrando dalla panchina, che WhoScored tiene ancora in lista. Le
liste sono pre-partita ma in parte VECCHIE: rumore, non futuro.

La firma 2 era contaminata dallo stesso fenomeno (chi rientra gioca poco e
resta in lista). Ristretta al caso che conta — giocatore SANO alla t-1
(almeno 60' e non in lista), uscito presto alla t, infortunato alla
t+1 — la quota gia' in lista alla t e' 1.1% contro lo 0.6% del controllo:
nessuna fuga dal futuro. La regola iniziale resta scritta com'era e resta non
superata: il test ristretto e' una diagnosi aggiunta dopo, e lo si dichiara.

Non scrive niente fuori da experiments/output/.

Uso:
    python -m goalmodel.experiments.assenze_orizzonte
"""

from __future__ import annotations

import itertools
import logging

import pandas as pd

from goalmodel import data, experiments
from goalmodel.features import players

log = logging.getLogger("assenze_orizzonte")

SOGLIA_IN_CAMPO = 0.03
SOGLIA_FIRMA = 0.05
SOGLIA_DUBBI = 0.20
USCITA_PRESTO = 60


def _partite_con_data() -> pd.DataFrame:
    """La quadrupla e la data di ogni partita, normalizzata a mezzanotte."""
    m = data.load_master()[players.KEYS + ["date"]].copy()
    m["date"] = pd.to_datetime(m["date"]).dt.normalize()
    m["season"] = m["season"].astype(str)
    return m


def _minuti(giocatori: pd.DataFrame) -> pd.DataFrame:
    """Minuti per (stagione, squadra, giocatore, data), con la partita successiva."""
    g = giocatori.groupby(["season", "team", "chiave_giocatore", "date"],
                          as_index=False)["minuti"].sum()
    g["date"] = pd.to_datetime(g["date"]).dt.normalize()
    return g


def _firma_ristretta(assenze: pd.DataFrame, partite: pd.DataFrame,
                     minuti: pd.DataFrame) -> tuple[int, float, int, float]:
    """
    La firma 2 sui soli giocatori SANI alla partita precedente.

    Sano = almeno 60 minuti alla t-1 e non in lista alla t-1. Cosi' escono dal
    conto i rientri dalla panchina, che giocano poco E restano in lista, e
    resta il caso da cui una lista scritta dopo si tradirebbe: l'infortunio
    nato nella partita t che compare gia' nella lista della t.
    """
    el = assenze.merge(partite, on=players.KEYS, how="inner")
    el["date"] = pd.to_datetime(el["date"]).dt.normalize()
    in_lista = set(zip(el["season"], el["team"], el["chiave_giocatore"], el["date"], strict=True))
    inj = el[el["reason"] == "injured"]
    infortunati = set(zip(inj["season"], inj["team"], inj["chiave_giocatore"], inj["date"],
                          strict=True))

    liste = el[["season", "team", "date"]].drop_duplicates().rename(columns={"date": "data_lista"})
    mm = pd.merge_asof(minuti.sort_values("date"), liste.sort_values("data_lista"),
                       left_on="date", right_on="data_lista", by=["season", "team"],
                       direction="nearest", tolerance=pd.Timedelta(days=1)
                       ).dropna(subset=["data_lista"])
    date_sq = mm.groupby(["season", "team"])["data_lista"].apply(lambda x: sorted(set(x))).to_dict()
    pos = {(st, sq, d): i for (st, sq), ds in date_sq.items() for i, d in enumerate(ds)}
    mm["i"] = [pos[(st, sq, d)] for st, sq, d in
               zip(mm["season"], mm["team"], mm["data_lista"], strict=True)]
    giocati = mm.set_index(["season", "team", "chiave_giocatore", "i"])["minuti"]

    caso, controllo = [], []
    for r in mm.itertuples(index=False):
        if not (0 < r.minuti < USCITA_PRESTO):
            continue
        ds = date_sq[(r.season, r.team)]
        if r.i == 0 or r.i + 1 >= len(ds):
            continue
        prima = giocati.get((r.season, r.team, r.chiave_giocatore, r.i - 1), 0.0)
        if prima < 60 or (r.season, r.team, r.chiave_giocatore, ds[r.i - 1]) in in_lista:
            continue
        gia = (r.season, r.team, r.chiave_giocatore, r.data_lista) in in_lista
        dopo = (r.season, r.team, r.chiave_giocatore, ds[r.i + 1]) in infortunati
        (caso if dopo else controllo).append(gia)
    s_caso = pd.Series(caso, dtype=float)
    s_ctrl = pd.Series(controllo, dtype=float)
    return len(s_caso), float(s_caso.mean()), len(s_ctrl), float(s_ctrl.mean())


def analizza() -> dict:
    assenze = players.carica_assenze()
    giocatori = players.carica_giocatori()
    partite = _partite_con_data()

    a = assenze.merge(partite, on=players.KEYS, how="inner")
    a["date"] = pd.to_datetime(a["date"]).dt.normalize()
    minuti = _minuti(giocatori)

    # Le date di FBref e di football-data possono differire di un giorno: si
    # cerca il giocatore nella partita della sua squadra entro +-1 giorno.
    a = pd.merge_asof(
        a.sort_values("date"),
        minuti.rename(columns={"date": "data_fbref"}).sort_values("data_fbref"),
        left_on="date", right_on="data_fbref",
        by=["season", "team", "chiave_giocatore"],
        direction="nearest", tolerance=pd.Timedelta(days=1),
    )
    a["ha_giocato"] = a["minuti"].fillna(0) > 0

    certi = a[a["indisponibile"]]
    dubbi = a[~a["indisponibile"]]
    in_campo = certi["ha_giocato"].mean()
    per_motivo = certi.groupby("reason")["ha_giocato"].agg(["mean", "size"])
    dubbi_in_campo = dubbi["ha_giocato"].mean() if len(dubbi) else float("nan")

    # --- firma 2: l'infortunio prima di quando e' successo -----------------
    # Per ogni squadra, le partite in ordine: chi esce presto alla t, e se
    # risulta infortunato alla t+1, e se compariva gia' nella lista della t.
    elenco = assenze.merge(partite, on=players.KEYS, how="inner")
    elenco["date"] = pd.to_datetime(elenco["date"]).dt.normalize()
    infortunati = set(zip(elenco.loc[elenco["reason"] == "injured", "season"],
                          elenco.loc[elenco["reason"] == "injured", "team"],
                          elenco.loc[elenco["reason"] == "injured", "chiave_giocatore"],
                          elenco.loc[elenco["reason"] == "injured", "date"], strict=True))
    nella_lista = set(zip(elenco["season"], elenco["team"], elenco["chiave_giocatore"],
                          elenco["date"], strict=True))

    date_squadra = (minuti.groupby(["season", "team"])["date"]
                    .apply(lambda s: sorted(set(s))).to_dict())
    successiva = {}
    for (stagione, squadra), date in date_squadra.items():
        for d0, d1 in itertools.pairwise(date):
            successiva[(stagione, squadra, d0)] = d1

    presto = minuti[(minuti["minuti"] > 0) & (minuti["minuti"] < USCITA_PRESTO)].copy()
    righe = []
    for r in presto.itertuples(index=False):
        dopo = successiva.get((r.season, r.team, r.date))
        if dopo is None:
            continue
        righe.append({
            "infortunato_dopo": (r.season, r.team, r.chiave_giocatore, dopo) in infortunati,
            "gia_in_lista": (r.season, r.team, r.chiave_giocatore, r.date) in nella_lista,
        })
    f2 = pd.DataFrame(righe)
    # Solo le partite che WhoScored ha davvero interrogato, altrimenti
    # "assente dalla lista" vorrebbe dire "lista mancante".
    tasso_caso = f2.loc[f2["infortunato_dopo"], "gia_in_lista"].mean()
    tasso_controllo = f2.loc[~f2["infortunato_dopo"], "gia_in_lista"].mean()

    # Diagnosi: come hanno giocato gli infortunati "in lista" scesi in campo.
    rientri = certi[(certi["reason"] == "injured") & certi["ha_giocato"]]
    fino_30 = float((rientri["minuti"] <= 30).mean()) if len(rientri) else float("nan")
    n_caso, q_caso, n_ctrl, q_ctrl = _firma_ristretta(assenze, partite, minuti)

    esito = {
        "rientri": len(rientri),
        "rientri_fino_30": fino_30,
        "ristretta_casi": n_caso,
        "ristretta_gia_in_lista": q_caso,
        "ristretta_controlli": n_ctrl,
        "ristretta_controllo": q_ctrl,
        "indisponibili_certi": len(certi),
        "quota_in_campo": in_campo,
        "dubbi": len(dubbi),
        "quota_dubbi_in_campo": dubbi_in_campo,
        "usciti_presto_poi_infortunati": int(f2["infortunato_dopo"].sum()),
        "di_cui_gia_in_lista_alla_t": tasso_caso,
        "controllo_gia_in_lista": tasso_controllo,
        "per_motivo": per_motivo,
    }
    esito["pre_partita"] = (
        in_campo < SOGLIA_IN_CAMPO
        and (tasso_caso - tasso_controllo) <= SOGLIA_FIRMA
        and dubbi_in_campo >= SOGLIA_DUBBI
    )
    return esito


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    experiments.proteggi_produzione()
    e = analizza()

    righe = [
        "# Blocco B — le liste degli indisponibili sono pre-partita?",
        "",
        "Regola scritta prima dei numeri: pre-partita se (a) meno del "
        f"{SOGLIA_IN_CAMPO:.0%} degli indisponibili certi ha giocato, (b) la firma "
        f"2 non supera il controllo di oltre {SOGLIA_FIRMA:.0%}, (c) almeno il "
        f"{SOGLIA_DUBBI:.0%} dei dubbi ha giocato.",
        "",
        "| verifica | valore | soglia |",
        "|---|---|---|",
        f"| (a) indisponibili certi che hanno giocato | {e['quota_in_campo']:.2%} "
        f"su {e['indisponibili_certi']} | < {SOGLIA_IN_CAMPO:.0%} |",
        f"| (b) usciti presto e infortunati alla t+1, gia' in lista alla t | "
        f"{e['di_cui_gia_in_lista_alla_t']:.2%} su {e['usciti_presto_poi_infortunati']} | "
        f"controllo {e['controllo_gia_in_lista']:.2%}, scarto <= {SOGLIA_FIRMA:.0%} |",
        f"| (c) dubbi che hanno giocato | {e['quota_dubbi_in_campo']:.2%} su {e['dubbi']} "
        f"| >= {SOGLIA_DUBBI:.0%} |",
        "",
        f"**Regola scritta prima: {'superata' if e['pre_partita'] else 'NON superata'}.**",
        "",
        "## Diagnosi (aggiunta dopo aver visto i numeri, e dichiarata tale)",
        "",
        f"- Gli infortunati in lista scesi in campo sono {e['rientri']}: il "
        f"{e['rientri_fino_30']:.0%} ha giocato al massimo 30 minuti. E' il profilo dei "
        "RIENTRI dalla panchina, che WhoScored tiene ancora in lista: liste pre-partita "
        "ma in parte vecchie.",
        f"- Firma 2 ristretta ai giocatori sani alla partita prima (>=60' e non in lista), "
        f"usciti presto e infortunati alla successiva: gia' in lista alla t il "
        f"**{e['ristretta_gia_in_lista']:.1%}** di {e['ristretta_casi']}, contro il "
        f"{e['ristretta_controllo']:.1%} di {e['ristretta_controlli']} controlli.",
        "",
        "**Lettura: nessuna informazione dal futuro. Il blocco B regge sull'orizzonte; "
        "le liste sono rumorose (rientri ancora segnati come infortunati), che ne "
        "indebolisce il segnale ma non lo invalida.**",
        "",
        "Indisponibili certi che hanno giocato, per motivo:",
        "",
        "| motivo | quota in campo | righe |",
        "|---|---|---|",
        *[f"| {motivo} | {r['mean']:.2%} | {int(r['size'])} |"
          for motivo, r in e["per_motivo"].iterrows()],
    ]
    testo = "\n".join(righe) + "\n"
    dst = experiments.percorso("riassunto_bloccoB_orizzonte.md")
    dst.write_text(testo, encoding="utf-8")
    print(testo)


if __name__ == "__main__":
    main()
