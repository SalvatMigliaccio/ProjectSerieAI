# Sovradattamento di M4 e M5 — 21 ottobre 2026

`python -m goalmodel.experiments.overfitting --lancia --analizza`. Serie A,
test 2324-2526, 1140 partite, 114 blocchi, iperparametri di produzione, seme 0.
Non e' un test di blocco: m resta 3.

**Metodo.** Per ogni blocco del walk-forward: errore sul training appena usato
(in-sample) contro errore sulla giornata predetta (fuori campione). Si
sottrae lo stesso scarto misurato sul mercato (M1), che non stima parametri e
quindi misura solo la differenza di difficolta' fra training e test.
`eccesso = (test_mod - train_mod) - (test_mkt - train_mkt)`, IC con bootstrap
a cluster sulla giornata. Finestra "coda" = ultime 380 partite del training.

**Controllo di riproduzione.** RPS fuori campione: M1 0.18817, M4 con mercato
0.19054, M4 senza 0.19409, M5 0.18798. Coincidono con la tabella pubblicata
entro 0.0003 (il dataset e' stato ricostruito sui Big 5 da allora).

## Eccesso RPS (positivo = sovradatta)

| modello | train − mkt | test − mkt | eccesso (coda) | IC 95% |
|---|---|---|---|---|
| M4 senza mercato | −0.00534 | +0.00592 | **+0.01125** | [+0.00837, +0.01414] |
| M4 con mercato | −0.00693 | +0.00237 | **+0.00930** | [+0.00768, +0.01095] |
| M5 ancorato | −0.00098 | −0.00019 | **+0.00079** | [+0.00042, +0.00115] |

Stesso verdetto con la finestra intera e con la devianza Poisson sui gol.

**Per stagione** (eccesso, coda): M4 senza 0.0147 → 0.0112 → 0.0079;
M4 con 0.0116 → 0.0090 → 0.0073; M5 0.0010 → 0.0007 → 0.0007. Cala al
crescere del training, come ci si aspetta da un sovradattamento.

**Alberi scelti dall'arresto anticipato.** M4: 80-540 per lato, stabili. M5:
da 1 a 2228 sul lato casa (mediana 383), da 1 a 1762 in trasferta (mediana
71). Nessun blocco degenere su entrambi i lati.

## Lettura

1. **M4 sovradatta molto.** In-sample batte il mercato di 0.005-0.007, fuori
   campione perde di 0.002-0.006. Per M4-con-mercato l'eccesso (0.0093) vale
   circa quattro volte la sua distanza dal mercato fuori campione (0.0024). Non contraddice la taratura:
   l'arresto anticipato sceglie gia' la capacita' ottima FUORI campione;
   quello che l'eccesso dice e' che a quella capacita' gran parte di cio' che
   il modello impara e' rumore del training. E' la stessa conclusione del
   risultato acquisito vista da un'altra parte.
2. **M5 sovradatta poco ma in modo misurabile.** Tre quarti del suo vantaggio
   in-sample sul mercato (−0.00098) non sopravvivono fuori campione
   (−0.00019, indistinguibile da zero). L'eccesso, +0.0008, e' dell'ordine
   degli effetti di blocco che si cercano (0.0003): **un guadagno di M5
   misurato in-sample o sulla coda del training non va mai creduto**.
3. **Il numero di alberi di M5 e' instabile** (da 1 a oltre 2000 fra blocchi
   vicini): la curva di arresto e' piatta, cioe' non c'e' un residuo chiaro da
   imparare, e la scelta e' governata dal rumore. E' un argomento in piu' per
   la media su piu' semi (`M5MediaSemi`) nelle misure future.
4. **La produzione non cambia**: M1 non ha parametri e non puo' sovradattare.
