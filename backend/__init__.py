"""
Backend del monorepo: l'API in sola lettura sul track record.

PERCHE' E' UN PACCHETTO A PARTE DA `goalmodel`. `goalmodel` e' la
pipeline: ingestion, feature, modelli, i due comandi di giornata. Quella e'
produzione e non si tocca. Dal 6 novembre 2026 il suo sorgente sta qui dentro,
in `backend/src/goalmodel/`, ma resta un pacchetto distinto e installabile da
solo: la cartella che lo contiene non ne fa un sottomodulo di `backend`. Il backend la LEGGE e basta — importa
`goalmodel.config`, `goalmodel.prediction.rounds`, `goalmodel.prediction.predict`, `goalmodel.prediction.backtest_log`,
`goalmodel.evaluation.evaluate` e `goalmodel.models.baseline`, e non scrive da nessuna parte.

La dipendenza e' quindi a senso unico: `backend` -> `goalmodel`, mai il
contrario. Se un giorno si volesse far girare l'API su un'altra macchina,
la si taglia esportando uno snapshot JSON, non spostando moduli.

Si lancia dalla radice del repository, dove `goalmodel` (installato con
`pip install -e .`) e `backend` sono entrambi importabili:

    python -m backend.api --port 8000
    uvicorn backend.api:app --port 8000
"""
