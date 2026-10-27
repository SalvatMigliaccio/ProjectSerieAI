# Blocco B, migliorie — validazione

Stagioni 2122, 2223, media di 5 semi, confronto annidato contro BASE+GIOCATORI. Test set non toccato: m resta 3.

| variante | differenza | IC 95% | quota peggiore | esito |
|---|---|---|---|---|
| xga | +0.00001 | [-0.00001, +0.00004] | 0.864 | scartata |
| lungo | -0.00001 | [-0.00002, +0.00001] | 0.220 | scartata |
| entrambe | +0.00001 | [-0.00001, +0.00004] | 0.780 | scartata |

Regola scritta prima: intervallo tutto sotto zero -> ipotesi pre-registrata (non promossa); altrimenti scartata.

## Controllo: la validazione vede il blocco?

BASE+GIOCATORI meno BASE, stessa validazione: **-0.00001**, IC [-0.00005, +0.00003] (sul test set: -0.00027, IC [-0.00054, -0.00001]).

**Il controllo e' a cavallo dello zero: la validazione e' CIECA per il blocco, e gli 'scartata' qui sopra non decidono niente.** Le varianti diventano l'ipotesi pre-registrata n.3 di CLAUDE.md, da testare sulla stagione 2026/27.
