# Blocco B — verifiche di robustezza

15 settembre 2026. `python -m src.experiments.blocco_b_robustezza --lancia` e
`--analizza`. 15 walk-forward completi sul test set (3 varianti x 5 semi),
M5 ancorato al mercato, bootstrap a cluster sulla giornata.

**Regola di lettura, scritta prima dei risultati:** queste verifiche possono
solo declassare il blocco, mai promuoverlo.

## Determinismo

Il seme 0 rifatto coincide con la misura originale: scarto massimo
**0.00e+00** su 1140 partite, sia per `con` sia per `senza`. Tutto cio' che
segue e' confrontabile con il -0.00027 di partenza.

## Semi: M5+GIOCATORI − M5

| seme | differenza | IC 95% | p |
|---|---|---|---|
| 0 | -0.00027 | [-0.00054, -0.00001] | 0.0225 |
| 1 | -0.00026 | [-0.00053, +0.00001] | 0.0283 |
| 2 | -0.00016 | [-0.00038, +0.00006] | 0.0686 |
| 3 | -0.00022 | [-0.00045, +0.00000] | 0.0255 |
| 4 | -0.00032 | [-0.00056, -0.00007] | 0.0050 |

**Il segno e' stabile, la significativita' no.** Cinque stime su cinque sono
negative e nello stesso ordine di grandezza (da -0.00016 a -0.00032).
L'intervallo sta tutto sotto zero solo in 2 semi su 5; negli altri tre tocca
o attraversa lo zero. Il seme 0 — quello della misura originale — non era un
caso fortunato, ma nemmeno il piu' rappresentativo.

## Simmetria: senza le due colonne separate di minuti

Tolte `home_quota_minuti_assenti` e `away_quota_minuti_assenti`, resta
`diff_quota_minuti_assenti` con le altre sei colonne del set.

| seme | differenza | IC 95% | p |
|---|---|---|---|
| 0 | -0.00024 | [-0.00044, -0.00004] | 0.0101 |
| 1 | -0.00032 | [-0.00054, -0.00010] | 0.0017 |
| 2 | -0.00020 | [-0.00039, -0.00001] | 0.0169 |
| 3 | -0.00032 | [-0.00052, -0.00013] | 0.0007 |
| 4 | -0.00022 | [-0.00043, -0.00002] | 0.0166 |

**Il guadagno non evapora.** Cinque intervalli su cinque sotto zero, e la
variante simmetrica vale quanto la completa (sotto). L'asimmetria 20:1 era una
**rappresentazione**: con due colonne quasi gemelle LightGBM ne sceglie una
secondo il campionamento delle colonne, e l'importanza si concentra su quella.
Con la sola differenza la stessa informazione passa da una feature simmetrica.

## Media dei cinque semi

| confronto | differenza | IC 95% | p |
|---|---|---|---|
| media con − media senza | -0.00025 | [-0.00047, -0.00002] | 0.0159 |
| media simmetrico − media senza | -0.00026 | [-0.00044, -0.00008] | 0.0021 |
| media simmetrico − media con | -0.00001 | [-0.00013, +0.00011] | 0.4104 |

## Confronti multipli — conclusione esplicita

Tre test di blocco (contesto senza coppe, contesto con coppe, giocatori).
Bonferroni a una coda con alfa 0.025: **soglia 0.0083**.

- Misura pre-registrata, seme 0: **p = 0.0225 — non passa.**
- Stessa ipotesi stimata con meno varianza, media dei 5 semi:
  **p = 0.0159 — non passa.**
- Variante simmetrica, media dei 5 semi: p = 0.0021 passerebbe, **ma non e'
  la specifica pre-registrata** e non puo' sostituirla dopo aver visto i
  risultati: sarebbe una ricerca di specifica.

Con m = 4 la soglia scende a 0.00625 e la conclusione non cambia.

**Il blocco GIOCATORI resta PROVVISORIO.** Non e' scartato — la simmetria ha
tenuto, il segno e' stabile su tutti i semi — e non e' confermato: non
sopravvive alla correzione per i confronti multipli.

## Criterio di promozione

| confronto | differenza | IC 95% |
|---|---|---|
| media con − mercato | -0.00011 | [-0.00081, +0.00057] |
| media simmetrico − mercato | -0.00012 | [-0.00082, +0.00056] |

**Nessuna variante batte il mercato.** La produzione resta su M1.
