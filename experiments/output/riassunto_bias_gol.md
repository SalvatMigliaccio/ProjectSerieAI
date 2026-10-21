# Bias sui gol — 21 ottobre 2026

`python -m goalmodel.experiments.bias_gol`. Previsioni fuori campione del
walk-forward di `overfitting.py`: Serie A, test 2324-2526, 1140 partite, M1,
M4 con e senza mercato, M5. IC con bootstrap a cluster sulla giornata.
Diagnostica, non segnale: nessuna di queste viste diventa una feature.

## Livello e pendenza dei gol — nessun bias

| modello | gol totali previsti | segnati | scarto | IC 95% | pendenza (se) |
|---|---|---|---|---|---|
| M1 | 2.586 | 2.533 | −0.053 | [−0.148, +0.042] | 1.016 (0.054) |
| M4 senza mercato | 2.615 | 2.533 | −0.082 | [−0.179, +0.016] | 1.076 (0.061) |
| M4 con mercato | 2.573 | 2.533 | −0.040 | [−0.137, +0.057] | 1.083 (0.059) |
| M5 | 2.561 | 2.533 | −0.029 | [−0.124, +0.067] | 1.021 (0.054) |

Tutti prevedono qualche gol in piu' di quelli segnati (soprattutto in casa,
−0.06), ma nessun intervallo esclude lo zero. Pendenze compatibili con 1: i
lambda non separano ne' troppo ne' troppo poco. Over 2.5 calibrato per tutti
(M1: 47.6% previsto, 47.7% osservato).

Sottogruppi (fascia di favorito, quintile di gol attesi, stagione): un solo
intervallo esclude lo zero, il 2o quintile di gol attesi (−0.19, tutti i
modelli). Su ~15 celle per modello e' quanto ci si aspetta dal caso.

Per squadra (M1, 25 squadre x 2 versi, Bonferroni a 0.0010): nessuna oltre la
soglia. Napoli: segna 0.17 gol a partita meno del previsto, IC [−0.38,
+0.05], p = 0.13 — compatibile con il caso.

## I pareggi — il solo bias netto, e sta nella conversione in lambda

| modello | p(pareggio) media | osservata | scarto | IC 95% |
|---|---|---|---|---|
| M1b mercato diretto | 0.258 | 0.280 | +0.022 | [−0.005, +0.049] |
| **M1 via lambda (produzione)** | **0.241** | 0.280 | **+0.038** | **[+0.012, +0.066]** |
| M3 Dixon-Coles (rho stimato) | 0.260 | 0.280 | +0.020 | [−0.007, +0.047] |
| M4 / M5 | 0.243-0.247 | 0.280 | +0.033/+0.037 | escludono lo zero |

Il mercato da solo e' compatibile con la frequenza vera. **Il pareggio si
perde nel passaggio quote -> lambda -> matrice**: due Poisson indipendenti
(`config.DC_RHO = 0`) tolgono 1.7 punti di pareggio rispetto alle quote da cui
partono, e il difetto e' piu' grande nelle partite equilibrate (27.5% contro
29.9% del mercato diretto). M3, che stima rho, non ce l'ha.

Conseguenza: in produzione ogni mercato derivato dalla matrice (X, doppia
chance, risultato esatto) parte da un pareggio sottostimato. L'effetto
sull'RPS 1X2 e' piccolo (M1 0.1882 contro M1b 0.1881, indistinguibili).

**Correzione possibile, NON applicata**: un rho negativo in `DC_RHO`, tarato
sulla validazione, con `valid_rho_floor` come vincolo. E' un cambiamento di
produzione: richiede la taratura in validazione, la misura sul test e la
rigenerazione dichiarata di `test_production_unchanged`.
