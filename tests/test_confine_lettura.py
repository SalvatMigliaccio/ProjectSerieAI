"""
Il confine di lettura regge, e resta l'unico punto che tocca il disco.

PERCHE' ESISTE. `data.py` e' nato per essere l'unico posto che sa dove stanno
i file, ma niente lo imponeva: a distanza di mesi undici moduli di produzione
avevano ricominciato ad aprire il parquet per conto loro, ognuno ripetendo il
percorso, il controllo di esistenza e il messaggio. Non e' un difetto che si
vede — funziona tutto — finche' non si cambia il formato.

E' esattamente cio' che la fase 2 fara': `docs/adr/0002-postgres-fonte-di-verita.md`
sposta i dati in Postgres. Con una lettura sparsa, migrare significa modificare
una dozzina di file e **dimenticarne uno non da' errore**: da' un modulo che
legge un parquet stantio mentre tutti gli altri leggono il database, e le
metriche restano plausibili.

DUE CONFINI, NON UNO, ed e' una decisione. `goalmodel` legge da `data.py`;
`backend/api` legge da `store.py`, perche' e' un deployable separato (ADR 0001)
con i percorsi come parametri, e instradarlo su `goalmodel.data` ne fisserebbe
il layout proprio mentre la fase 3 lo prepara a diventare un repository suo.

    python -m tests.test_confine_lettura
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent

# I due confini: qui leggere dal disco e' il mestiere, non un'eccezione.
CONFINI = {
    Path("src/goalmodel/data.py"),
    Path("backend/api/store.py"),
}

# LE ECCEZIONI, UNA PER UNA E CON IL MOTIVO. Non e' una lista di comodo: ogni
# riga qui e' un posto che la migrazione a Postgres dovra' toccare a mano,
# quindi allungarla e' una decisione, non una scorciatoia.
AMMESSI = {
    Path("src/goalmodel/ingest.py"):
        "lato produttore: rilegge il parquet che ha appena scritto per "
        "accodare la stagione nuova. `load_raw` farebbe la cosa sbagliata, "
        "perche' solleva quando il file manca — e al primo run manca sempre.",
    Path("src/goalmodel/normalize.py"):
        "lato produttore: conta le righe del file prima di sovrascriverlo, "
        "per dire di quanto e' cresciuto.",
}

# `experiments/` per progetto legge tutto e non e' importato da nessuno.
ESCLUSI = ("experiments/",)

CHIAMATA = re.compile(r"\bpd\.read_parquet\s*\(|\bread_parquet\s*\(")


def _sorgenti() -> list[Path]:
    file = [*(RADICE / "src").rglob("*.py"), *(RADICE / "backend").rglob("*.py")]
    return sorted(
        p.relative_to(RADICE) for p in file
        if not any(e in p.as_posix() for e in ESCLUSI)
    )


def _legge_dal_disco(path: Path) -> bool:
    """Solo le CHIAMATE contano: nominarla in un commento non e' leggerla."""
    righe = (RADICE / path).read_text(encoding="utf-8").splitlines()
    return any(CHIAMATA.search(r) for r in righe if not r.lstrip().startswith("#"))


def test_nessuna_lettura_fuori_dai_confini() -> None:
    fuori = [
        p for p in _sorgenti()
        if p not in CONFINI and p not in AMMESSI and _legge_dal_disco(p)
    ]
    assert not fuori, (
        "questi moduli aprono il parquet saltando il confine di lettura:\n  "
        + "\n  ".join(p.as_posix() for p in fuori)
        + "\n\nInstradali su `data.py` (o su `store.py` se stanno in backend/). "
          "Se e' davvero un'eccezione, aggiungila ad AMMESSI con il motivo: "
          "ogni riga di quella lista e' un posto che la migrazione a Postgres "
          "dovra' toccare a mano."
    )
    print(f"  {len(_sorgenti())} moduli, nessuna lettura fuori dai confini  ok")


def test_le_eccezioni_esistono_ancora() -> None:
    """
    Un'eccezione che non serve piu' va tolta, non lasciata li'.

    Una lista di deroghe che nessuno pota si allarga e basta, e dopo un po'
    non racconta piu' niente: sembra che il confine abbia dieci buchi quando
    ne ha due.
    """
    for path, motivo in AMMESSI.items():
        assert (RADICE / path).exists(), f"{path} non esiste piu': togli l'eccezione"
        assert _legge_dal_disco(path), (
            f"{path} non legge piu' dal disco: togli l'eccezione da AMMESSI.\n"
            f"Era ammessa perche' {motivo}"
        )
    print(f"  {len(AMMESSI)} eccezioni, tutte ancora necessarie             ok")


def test_i_confini_sono_dove_diciamo() -> None:
    """Se un confine sparisce o cambia nome, questo test lo dice subito."""
    for path in CONFINI:
        assert (RADICE / path).exists(), f"confine dichiarato ma assente: {path}"
        assert _legge_dal_disco(path), (
            f"{path} e' dichiarato confine ma non legge dal disco: "
            f"o il confine si e' spostato, o la dichiarazione e' vecchia")
    print("  i due confini esistono e leggono davvero             ok")


def main() -> None:
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
