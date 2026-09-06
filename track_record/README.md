# track_record

**Questa cartella e' versionata di proposito.**

`predictions_log.csv` e' l'unico dato del progetto che non si puo' rigenerare.
Ogni altro file — parquet grezzi, feature, previsioni del walk-forward, report
HTML — si ricostruisce rilanciando un comando. Le previsioni no: vanno scritte
prima del calcio d'inizio, e quel momento non torna. Se il file si perde, il
track record riparte da zero e servono mesi per ricostruirlo.

Per questo sta fuori da `data/`, che e' in `.gitignore`: cosi' ha una storia
completa in git e una copia in ogni clone.

## Regole

- **Append-only.** Non si riscrive e non si riordina. Rilanciare la previsione
  della stessa partita non aggiunge una riga: `predict.append_log` deduplica
  su (partita, `model_version`).
- **Le righe sbagliate restano.** Due previsioni furono registrate dopo il
  calcio d'inizio, per un errore di fuso orario. Non sono state cancellate:
  `backtest_log.flag_post_kickoff` le riconosce e le esclude dalle metriche.
  Il registro conserva anche gli errori, altrimenti non e' un registro.
- **Se le quote cambiano non si aggiorna la riga.** Falsificherebbe il track
  record a posteriori. Per una seconda opinione si usa un `model_version`
  diverso.

## Cosa NON sta qui

`predictions_backfill.csv` sono previsioni ricostruite a posteriori con
`predict --as-of`, prodotte conoscendo il calendario completo e da un modello
scelto guardando quelle stagioni. Non sono un track record e non sono
versionate.

`report.html` sta in questa cartella perche' e' la lettura del registro — lo
scrive `python -m src.weekly` e lo rigenera `python -m src.report` — ma **non
e' versionato**: si ricostruisce dal CSV in due secondi, e una copia nuova a
ogni settimana renderebbe illeggibile la storia del file che conta.
