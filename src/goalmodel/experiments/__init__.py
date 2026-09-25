"""
Esperimenti: importano da `src/`, non scrivono MAI in produzione.

LA REGOLA. Il codice qui dentro puo' leggere tutto — dataset, feature, il
walk-forward gia' prodotto — ma scrive solo in `experiments/output/`. Non in
`data/processed/`, dove stanno le feature che la produzione rilegge ogni
venerdi'; non in `track_record/`, dove sta l'unico dato del progetto che non
si rigenera.

PERCHE' UNA GUARDIA A RUNTIME E NON SOLO UNA CONVENZIONE. Una convenzione
regge finche' qualcuno non copia una riga da `evaluate.py` — che scrive in
`data/processed/` — dentro uno script sperimentale. `proteggi_produzione()`
sostituisce `DataFrame.to_parquet` e `to_csv` nel solo processo
dell'esperimento, e rifiuta qualsiasi percorso che cada nelle due cartelle
protette. L'errore arriva al momento della scrittura, non a meta' della
settimana dopo quando il report e' diverso e nessuno sa perche'.

Il percorso di output e' definito qui e non in `config.py`: `config` e' un
modulo di produzione, e il vincolo e' di non modificarlo per un esperimento.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import config

OUTPUT = config.ROOT / "experiments" / "output"
PROTETTE = (config.PROCESSED.resolve(), config.TRACK_RECORD.resolve())

_protetto = False


def percorso(nome: str) -> Path:
    """Un file di output sperimentale. Crea la cartella se serve."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    return OUTPUT / nome


def _vietato(path) -> Path | None:
    try:
        p = Path(path).resolve()
    except TypeError:
        return None  # buffer in memoria, non un percorso: nessun rischio
    for protetta in PROTETTE:
        if p == protetta or protetta in p.parents:
            return protetta
    return None


def proteggi_produzione() -> None:
    """
    Da questo momento il processo non puo' scrivere in produzione.

    Idempotente. Si chiama in testa a ogni script sperimentale.

    COSA COPRE, ESATTAMENTE (audit B6). Due metodi di pandas:
    `DataFrame.to_parquet` e `DataFrame.to_csv`. Nient'altro.

    NON copre: `to_feather`, `to_pickle`, `to_hdf`, `to_excel`, `np.save`,
    `Path.write_text`, `Path.write_bytes`, `open(..., "w")`,
    `pyarrow.parquet.write_table`, `shutil.copy` — e quest'ultima la usa
    proprio `predict.append_log` per il backup del registro.

    **Perche' l'elenco sta scritto qui invece di essere allungato.** Questa
    guardia esiste per un caso preciso e osservato: qualcuno copia una riga da
    `evaluate.py` dentro un esperimento e sovrascrive un parquet di
    produzione. Quella riga usa `to_parquet` o `to_csv`, e per quel caso la
    rete c'e'. Inseguire ogni modo di scrivere un file darebbe una protezione
    piu' larga e, soprattutto, **piu' credibile di quanto sia** — mentre il
    danno vero di una guardia parziale non e' cio' che lascia passare, e' che
    smette di far pensare. Leggere questa lista deve costare quanto fidarsi.
    """
    global _protetto
    if _protetto:
        return

    originali = {"to_parquet": pd.DataFrame.to_parquet,
                 "to_csv": pd.DataFrame.to_csv}

    def _guardia(nome):
        originale = originali[nome]

        def scrivi(self, path=None, *args, **kwargs):
            protetta = _vietato(path) if path is not None else None
            if protetta is not None:
                raise PermissionError(
                    f"esperimento: scrittura in {path} rifiutata. "
                    f"{protetta} e' produzione. Usa experiments.percorso()."
                )
            return originale(self, path, *args, **kwargs)
        return scrivi

    for nome in originali:
        setattr(pd.DataFrame, nome, _guardia(nome))
    _protetto = True
