"""
goalmodel — modello probabilistico sui gol, con il mercato come riferimento.

I livelli, dal basso verso l'alto. Ogni livello puo' importare solo quelli
sotto di se': e' la regola che tiene separati i confini e che rende meccanico
un eventuale split in piu' repository (vedi docs/adr/0001-monolite-modulare.md).

    config                 percorsi, leghe, iperparametri
    ingest, normalize      acquisizione e unificazione delle fonti
    features/              forma, mercato, contesto, giocatori
    models/                M0..M6, dalla lambda alla matrice dei risultati
    evaluation/            RPS, calibrazione, walk-forward, potenza
    prediction/            previsione, registro, ciclo della giornata
    reporting/             report HTML
    experiments/           fuori produzione, non importato da nessuno dei sopra
"""

__version__ = "0.1.0"
