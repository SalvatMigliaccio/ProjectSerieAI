# rho di Dixon-Coles per M1 — 21 ottobre 2026

`python -m goalmodel.experiments.rho_mercato --tara` / `--misura --rho -0.16`.
Regole scritte nel docstring del modulo prima di lanciare.

**Perche'.** `riassunto_bias_gol.md`: M1 prevede il 24.1% di pareggi contro il
28.0% osservato (IC dello scarto [+1.2, +6.6] punti), mentre le quote dirette
(25.8%) sono compatibili. Il pareggio si perde convertendo le quote in due
Poisson indipendenti.

## Taratura — validazione 2122-2223, 759 partite

Minimo dell'RPS a **rho = −0.16** (interno alla griglia −0.20..0), stesso
minimo della log loss 1X2. A −0.16 i pareggi previsti sono il 26.2% contro il
26.1% osservato. L'over 2.5 e' identico su tutta la griglia (0.531123), come
deve: la correzione sposta massa solo fra celle con al piu' due gol.

La log-verosimiglianza del **risultato esatto** ha invece il massimo a −0.02.

## Test — 2324-2526, 1140 partite

| confronto | diff. RPS | IC 95% |
|---|---|---|
| M1 rho=−0.16 − M1 rho=0 | −0.00040 | [−0.00092, +0.00010] |
| M1 rho=−0.16 − M1b diretto | −0.00036 | [−0.00088, +0.00016] |

| | p(pareggio) | osservati | scarto | IC 95% |
|---|---|---|---|---|
| M1 rho=0 | 0.2414 | 0.2798 | +0.0384 | [+0.0119, +0.0659] |
| **M1 rho=−0.16** | **0.2776** | 0.2798 | **+0.0022** | **[−0.0243, +0.0297]** |
| M1b diretto | 0.2582 | 0.2798 | +0.0216 | [−0.0050, +0.0491] |

Gol-gol: 47.6% -> 49.4%, osservato 49.7% (corretto anche lui). Over 2.5
invariato al sesto decimale.

**Regola a)** RPS non peggiora: SI. **Regola b)** pareggi corretti: SI.
**Esito: ADOTTARE.**

## Il prezzo, dichiarato

La verosimiglianza del risultato esatto peggiora appena (−2.7457 -> −2.7473 sul
test): −0.16 rimette i pareggi caricando 0-0 e 1-1 piu' di quanto i risultati
esatti giustifichino, e togliendo da 1-0 e 0-1. Per 1X2, doppia chance e
gol-gol e' un miglioramento; per il risultato esatto (`top_scorelines`) e'
una leggera distorsione. Un solo rho non sistema le due cose insieme: il
pareggio mancante non sta solo nelle quattro celle basse.
