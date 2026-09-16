# Blocco D — forma condizionata alla sede

16 settembre 2026. `python -m src.experiments.forma_venue --lancia` e
`--analizza`. 10 walk-forward (2 varianti x 5 semi) sulle sole stagioni di
**validazione** 2021/22 e 2022/23, 759 partite, 76 cluster.
**Nessun confronto sul test consumato: m resta 3.**

## Cosa sono le colonne

`FORM_HALFLIFE_VENUE = 10` era dichiarato in `config.py` e mai usato. Non
significa "finestra piu' lunga": significa forma **condizionata alla sede** —
la squadra di casa sulle sue sole partite in casa, quella in trasferta sulle
sole in trasferta. L'half-life e' piu' lunga di quella di BASE (6) perche'
restringendo alla sede i campioni si dimezzano.

48 colonne: 8 statistiche x 2 versi x (casa, trasferta, differenza), suffisso
`_ewm_sede`. Medie di lega della regressione di fine stagione calcolate per
sede. Costruite in memoria, nessun parquet, `form.py` non toccato.

Non sono una copia di BASE: correlazione con la gemella generale fra **0.75 e
0.89** in validazione.

## Regola di lettura, scritta prima dei risultati

Stima = media dei 5 semi. Intervallo che contiene lo zero, o differenza >= 0:
set **scartato**, questione chiusa a costo zero. Intervallo tutto sotto zero:
**ipotesi pre-registrata** per un test futuro, nessuna promozione.

## Risultati

| stima | differenza | IC 95% | p |
|---|---|---|---|
| **media dei 5 semi** | **+0.00001** | **[-0.00035, +0.00039]** | 0.5253 |
| seme 0 | +0.00005 | [-0.00034, +0.00044] | 0.5879 |
| seme 1 | -0.00008 | [-0.00045, +0.00030] | 0.3400 |
| seme 2 | -0.00007 | [-0.00047, +0.00033] | 0.3649 |
| seme 3 | +0.00008 | [-0.00033, +0.00052] | 0.6455 |
| seme 4 | +0.00008 | [-0.00031, +0.00049] | 0.6608 |

**Nullo. Set scartato.**

## La diagnosi, che vale piu' del verdetto

Il modello quelle colonne **le usa**: 50.5% del guadagno con il 48% delle
colonne (per seme da 39.6% a 59.4%), cioe' esattamente la loro quota. Non
vengono ignorate — sostituiscono le gemelle di BASE, che dicono quasi la stessa
cosa.

Terzo caso identico nel progetto: `diff_rest_days` quinta su 61 nel blocco A,
`home_quota_minuti_assenti` prima su 61 nel blocco B, e qui meta' del guadagno.
**Un'importanza alta dice dove il modello guarda, non se indovina di piu'.**

## Limite

Semiampiezza dell'intervallo 0.00037 su 76 cluster: esclude un effetto
dell'ordine di 0.00060 (shift di 0.10 gol), non un effetto piccolo come quello
del blocco B (0.00027). La specifica resta congelata in `sets.py`: se arrivano
i Big 5 si rimisura senza riscrivere niente.
