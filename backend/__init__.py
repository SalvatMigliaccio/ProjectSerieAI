"""
Backend del monorepo: l'API in sola lettura sul track record.

PERCHE' E' UN PACCHETTO A PARTE E NON STA IN `src/`. `src/` e' la
pipeline: ingestion, feature, modelli, i due comandi di giornata. Quella e'
produzione e non si tocca. Il backend la LEGGE e basta — importa
`src.config`, `src.rounds`, `src.predict`, `src.backtest_log`,
`src.evaluate` e `src.models.baseline`, e non scrive da nessuna parte.

La dipendenza e' quindi a senso unico: `backend` -> `src`, mai il
contrario. Se un giorno si volesse far girare l'API su un'altra macchina,
la si taglia esportando uno snapshot JSON, non spostando moduli.

Si lancia dalla radice del repository, dove `src` e `backend` sono
entrambi importabili:

    python -m backend.api --port 8000
    uvicorn backend.api:app --port 8000
"""
