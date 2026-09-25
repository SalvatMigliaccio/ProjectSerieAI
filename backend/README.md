# backend/ — API in sola lettura sul track record

Questa cartella contiene **solo** l'API. La pipeline (ingestion, feature,
modelli, i due comandi di giornata) e' il pacchetto `goalmodel`, sotto
`src/goalmodel/`, ed e' produzione.

La dipendenza e' a senso unico: `backend` importa `goalmodel`, mai il
contrario. Sono due pacchetti installabili distinti e non uno dentro l'altro,
perche' hanno dipendenze diverse — FastAPI e uvicorn stanno nell'extra `api`,
e chi lavora al modello non deve installare un web server per lanciare un
walk-forward.

Il backend **legge** `track_record/` e `data/`, e non scrive da nessuna parte —
la regola, con il motivo per cui esiste, sta in `CLAUDE.md`.

```
backend/
  api/
    store.py      l'UNICO modulo che apre file
    schemas.py    modelli Pydantic: qui vive il contratto sui null
    routes.py     i sette endpoint, tutti GET
    status.py     cosa risponde /api/status
    last_run.py   scritto dallo scheduler, letto da /api/status
    __init__.py   app FastAPI, CORS, cache, guardie
    __main__.py   python -m backend.api
```

**I comandi non stanno qui.** Stanno in `COMANDI.md`, sezione 7ter: due
elenchi divergono, e viene sempre letto quello sbagliato. In breve:
`python -m backend.api --port 8000`. Dopo `pip install -e ".[api]"` funziona
da qualsiasi directory: `backend` e' dichiarato in `pyproject.toml` insieme a
`goalmodel`.

Il contratto per il frontend e' `web/openapi.json`, versionato e verificato da
`tests/test_api.py`: se diverge dall'app, il test fallisce.
