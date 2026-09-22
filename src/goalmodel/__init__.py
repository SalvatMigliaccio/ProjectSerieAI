# Copyright (C) 2026 gli autori di goalmodel
#
# Questo programma e' software libero: puoi ridistribuirlo e/o modificarlo
# secondo i termini della GNU Affero General Public License, versione 3 o
# (a tua scelta) una successiva, pubblicata dalla Free Software Foundation.
#
# E' distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA,
# nemmeno quella implicita di COMMERCIABILITA' o IDONEITA' A UNO SCOPO
# PARTICOLARE. Vedi la GNU Affero General Public License per i dettagli.
#
# Copia della licenza: il file LICENSE nella radice del repository.
#
# NOTA SULLA AGPL, che la distingue dalla GPL: se fai girare una versione
# modificata di questo programma come SERVIZIO accessibile in rete, devi
# offrire il sorgente corrispondente a chi lo usa. E' il motivo per cui e'
# stata scelta: il progetto potrebbe diventare un servizio, e questa
# clausola vale anche per chiunque altro voglia farlo.
#
# LA LICENZA COPRE IL CODICE, NON I DATI. Risultati, quote, xG e calendari
# arrivano da fonti terze (football-data.co.uk, Understat, FBref,
# WhoScored) con i loro termini d'uso, che questa licenza non estende e non
# puo' estendere.

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
