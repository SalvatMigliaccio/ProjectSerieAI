# Serie A Match Predictor — Documento master

Modello probabilistico per la previsione degli esiti delle partite di Serie A,
allenato sui Big 5 campionati europei, con filtro finale sulle partite del Napoli.

Questo documento consolida tutte le decisioni, le verifiche e le motivazioni
discusse. Sostituisce e ingloba il blueprint precedente.

---

## Indice

1. [Impostazione del problema](#1-impostazione-del-problema)
2. [Perche' non un modello del solo Napoli](#2-perche-non-un-modello-del-solo-napoli)
3. [Perche' modellare i gol e non l1x2](#3-perche-modellare-i-gol-e-non-l1x2)
4. [Fonti dati: cosa esiste, cosa manca, cosa scrapare](#4-fonti-dati)
5. [Il problema centrale: il data leakage](#5-il-problema-centrale-il-data-leakage)
6. [Il layer giocatori](#6-il-layer-giocatori)
7. [Specificita' del calcio italiano](#7-specificita-del-calcio-italiano)
8. [Schema dati](#8-schema-dati)
9. [Catalogo feature](#9-catalogo-feature)
10. [Modelli](#10-modelli)
11. [Valutazione](#11-valutazione)
12. [Aspettative realistiche](#12-aspettative-realistiche)
13. [Struttura del repo](#13-struttura-del-repo)
14. [Pipeline di ingestion](#14-pipeline-di-ingestion)
14bis. [Ciclo di aggiornamento settimanale](#14bis-ciclo-di-aggiornamento-settimanale)
15. [Milestone](#15-milestone)
16. [Cosa serve da te](#16-cosa-serve-da-te)
17. [Trappole operative](#17-trappole-operative)

---

## 1. Impostazione del problema

**Obiettivo.** Produrre, per ogni partita di Serie A, una distribuzione di
probabilita' sui tre esiti (1, X, 2), con la possibilita' di filtrare le
predizioni sulle partite del Napoli.

**Output secondari gratuiti** dall'approccio scelto: Over/Under gol,
Goal/NoGoal, risultato esatto, handicap asiatico.

**Decisioni architetturali prese:**

| Decisione | Scelta | Motivazione |
|---|---|---|
| Perimetro training | Big 5 europei, dal 2014/15 | ~1.800 partite/stagione invece di 380; feature complete su tutti |
| Target | **Solo gol casa e gol trasferta** (Poisson). L'1X2 e' un output derivato, non un modello a se' | Vedi sezione 3. Nessun classificatore multiclasse diretto |
| Quote bookmaker | **Incluse** come feature | Massima accuratezza; il modello market-only resta come benchmark obbligatorio |
| Serie B | Solo priori per neopromosse | Non ha xG/PPDA, vedi sezione 4 |
| Orizzonte temporale | **Solo T-24h** in versione 1 | Predizione disponibile con anticipo reale. Il T-1h con formazioni ufficiali e' rimandato a fase successiva |
| Reti neurali | Escluse | ~20.000 righe e ~50 feature non le giustificano |
| Riaddestramento | Da zero a ogni giornata | A questa scala dura secondi. Nessun apprendimento incrementale, nessun drift accumulato |

---

## 2. Perche' non un modello del solo Napoli

Il Napoli gioca 38 partite a stagione. Anche con dieci stagioni sono 380 righe:
troppo poche per qualsiasi modello serio, e con distribuzione degli esiti
fortemente sbilanciata (il Napoli vince circa il 55-60% delle partite).

Il modello si allena su **tutte le partite disponibili** e poi si filtrano le
predizioni sul Napoli. Il Napoli e' il **caso d'uso**, non il **dataset**.

Estensione naturale dello stesso ragionamento: se 380 partite/stagione della
sola Serie A sono poche, i Big 5 ne danno ~1.800 con lo stesso identico set di
feature. Serve un parametro di lega per catturare le differenze sistematiche di
vantaggio casalingo e di numero medio di gol, ma il guadagno statistico e' netto.

---

## 3. Perche' modellare i gol e non l'1X2

L'approccio classico e tuttora competitivo e' modellare i gol attesi di casa e
trasferta come due distribuzioni di Poisson, con correzione della correlazione
tra i due (modello **Dixon-Coles**), e derivare l'1X2 integrando sulla matrice
dei risultati esatti.

Tre vantaggi concreti:

1. **Il pareggio.** L'esito X e' quasi impossibile da predire in modo diretto:
   un classificatore multiclasse non mette praticamente mai la X come classe
   piu' probabile, perche' il pareggio raramente e' l'esito piu' probabile —
   ma e' spesso al 25-28%. Il modello a gol lo gestisce naturalmente.
2. **Mercati derivati gratis.** Dalla matrice degli score ottieni Over/Under,
   Goal/NoGoal, risultato esatto, handicap.
3. **Interpretabilita'.** I parametri sono forza offensiva e difensiva di ogni
   squadra piu' il vantaggio casalingo: leggibili, diagnosticabili, discutibili.

Un classificatore diretto (gradient boosting multiclasse) resta come modello di
confronto, ma a parita' di dati il modello a gol di solito lo batte.

---

## 4. Fonti dati

### 4.1 Cosa NON scrapare

Il 90% dello scraping e' gia' fatto e mantenuto da altri. Scrivere scraper su
FBref significa gestire rate limiting aggressivo, HTML che cambia, e
normalizzazione dei nomi squadra tra fonti ("Inter" vs "Internazionale" vs
"Inter Milan") — lavoro noioso e sottovalutato.

**`soccerdata`** (Pieter Robberechts, KU Leuven) copre gia' tutto il necessario,
restituisce DataFrame pandas con nomi di colonna coerenti tra le fonti e mantiene
una cache locale persistente.

Classi disponibili nella versione 1.9.1, verificate:

```
ClubElo, ESPN, FBref, MatchHistory, SoFIFA, Sofascore, Understat, WhoScored
```

Identificativi Serie A, verificati:

```python
'ITA-Serie A': {
    'ClubElo': 'ITA_1',  'MatchHistory': 'I1',  'FBref': 'Serie A (M)',
    'ESPN': 'ita.1',     'Sofascore': 'Serie A', 'SoFIFA': '[Italy] Serie A',
    'Understat': 'Serie A', 'WhoScored': 'Italy - Serie A',
    'season_start': 'Aug', 'season_end': 'May'
}
```

Leghe supportate di default: `ENG-Premier League`, `ESP-La Liga`,
`FRA-Ligue 1`, `GER-Bundesliga`, `ITA-Serie A`, piu' le competizioni
internazionali. Altre leghe si aggiungono con un `league_dict.json` custom.

Firme dei metodi, verificate:

```python
FBref.read_schedule(force_cache=False)
FBref.read_team_match_stats(stat_type='schedule', opponent_stats=False, team=None, force_cache=False)
FBref.read_player_match_stats(stat_type='summary', match_id=None, force_cache=False)
FBref.read_lineup(match_id=None, force_cache=False)
FBref.read_events(match_id=None, force_cache=False)

Understat.read_schedule(force_cache=False)
Understat.read_team_match_stats(force_cache=False)
Understat.read_player_match_stats(match_id=None)
Understat.read_shot_events(match_id=None)

WhoScored.read_missing_players(match_id=None, force_cache=False)
WhoScored.read_events(match_id=None, ...)

ClubElo.read_by_date(date=None)
ClubElo.read_team_history(team, max_age=1)

MatchHistory.read_games()
```

### 4.2 Cosa fornisce ogni fonte

| Fonte | Copertura | Contenuto | Costo |
|---|---|---|---|
| **football-data.co.uk** (`MatchHistory`) | Serie A + Serie B + Big 5, 30+ stagioni | Risultati FT/HT, tiri, tiri in porta, corner, falli, cartellini, arbitro, quote 1X2 di ~10 bookmaker piu' medie e massime | Secondi |
| **Understat** | Big 5 + Premier russa, dal 2014/15 | xG, npxG, npxGD, PPDA, deep completions, punti, punti attesi — per squadra e partita. Piu' eventi tiro con coordinate | Minuti |
| **FBref** | Big 5, avanzate dal 2017/18 | Statistiche Opta di squadra e giocatore per partita, formazioni, minuti, eventi | Ore |
| **WhoScored** | Big 5 | **Infortunati e squalificati per singola partita** | Ore + browser |
| **ClubElo** | Tutte, storico completo | Rating Elo giornaliero, include risultati di coppa europea | Minuti |
| **Sofascore** | Big 5 | Classifiche, calendario | Minuti |

La scoperta piu' utile: `WhoScored.read_missing_players()` fornisce infortunati e
squalificati per partita. E' esattamente il pezzo che serve per il layer giocatori
e che altrimenti avresti dovuto scrapare a mano.

### 4.3 Colonne Understat per squadra/partita (verificate nel sorgente)

```
home_points / away_points
home_expected_points / away_expected_points
home_goals / away_goals
home_xg / away_xg
home_np_xg / away_np_xg                    (xG esclusi i rigori)
home_np_xg_difference / away_np_xg_difference
home_ppda / away_ppda                      (passaggi concessi per azione difensiva)
home_deep_completions / away_deep_completions
```

Piu' i metadati: `league`, `season`, `game_id`, `date`, `home_team`, `away_team`,
`home_team_id`, `away_team_id`, codici squadra.

### 4.4 La lacuna della Serie B

**La Serie B non ha xG ne' PPDA.** Verificato:

- Understat copre solo sei campionati (Big 5 + Premier russa). La Serie B non c'e'.
- Su FBref la pagina delle statistiche difensive Serie B 2025/26 ha le colonne
  Tackles, Blocks e Clearances **vuote**, con popolati solo TklW e Int. Gli
  Expected Goals e i dati avanzati sono forniti da Opta e disponibili solo per un
  elenco ristretto di competizioni, tra cui la Serie B non figura.

Quadro comparativo reale:

| | Serie A | Serie B |
|---|---|---|
| Risultati, quote bookmaker | Si', 30+ stagioni | Si', 30+ stagioni |
| Tiri, corner, falli, cartellini, arbitro | Si' | Si' |
| xG, npxG, PPDA, deep completions, xPts | Si', dal 2014/15 | **No** |
| Statistiche Opta squadra/giocatore | Si' | **No** |
| Formazioni e minuti | Si' | Parziale |
| Infortunati/squalificati | Si' (WhoScored) | Parziale |
| Elo | Si' (ClubElo) | Si' (ClubElo) |

**Conseguenza sul piano.** La Serie B non puo' essere una seconda sorgente di
training con lo stesso set di feature: avresti meta' dataset con xG e meta'
senza, e dovresti o buttare via le feature migliori o imputarle (pessima idea).

**Ruolo corretto della Serie B:** fornire i **priori per le neopromosse**,
risolvendo il cold start con un rating basato su gol e tiri, calibrato sul
differenziale storico B→A. Un modello separato, piccolo, con un solo output:
la stima di forza iniziale della squadra promossa.

**Se in futuro vuoi davvero l'xG di Serie B:** FotMob lo pubblica. Non e' coperto
da `soccerdata` in questa versione, quindi sarebbe l'unico punto del progetto in
cui uno scraper custom e' giustificato. Fase 4, non ora.

### 4.5 Cosa scrapare davvero in proprio

| Cosa | Perche' | Difficolta' |
|---|---|---|
| **Cambi allenatore** | Non esiste in nessuna fonte strutturata, ed e' un segnale forte | CSV manuale, ~15 righe per stagione. Wikipedia le ha tutte |
| **Transfermarkt** (valori di mercato) | Non coperto da soccerdata | HTML statico, scraping semplice. Opzionale |
| **FotMob** (xG Serie B) | Unica fonte gratuita di xG per la B | Fase 4 |
| Lista derby | Serve una flag `is_derby` | Lista manuale, 10 minuti |

### 4.6 Partenza rapida senza dipendenze

```python
import pandas as pd

seasons = ['1516','1617','1718','1819','1920','2021','2122','2223','2324','2425','2526']
df = pd.concat([
    pd.read_csv(f"https://www.football-data.co.uk/mmz4281/{s}/I1.csv")
    .assign(season=s) for s in seasons
])
# I1 = Serie A, I2 = Serie B
# HomeTeam, AwayTeam, FTHG, FTAG, FTR, HS, AS, HST, AST, HC, AC, B365H/D/A, ...
```

---

## 5. Il problema centrale: il data leakage

E' il punto in cui fallisce silenziosamente la grande maggioranza dei progetti
amatoriali: accuracy dell'85% in sviluppo che in produzione crolla al 45%.

### 5.1 La regola

Ogni feature della riga *i* deve essere calcolabile con la sola informazione
disponibile **prima del calcio d'inizio** di quella partita.

### 5.2 Le trappole concrete

**Statistiche della partita stessa.** Il CSV di football-data.co.uk contiene
tiri, corner, cartellini *di quella partita*: `HS`, `AS`, `HST`, `AST`, `HC`,
`AC`, `HF`, `AF`, `HY`, `AY`, `HR`, `AR`. Sono informazione post-partita. Usarle
come feature della stessa riga significa barare con se stessi. Servono
esclusivamente per costruire medie mobili storiche.

Lo stesso vale per xG, PPDA e deep completions di Understat, e per tutte le
statistiche giocatore di FBref della partita in corso.

**Split casuale.** Niente `train_test_split` con shuffle. Split temporale, o
meglio walk-forward validation.

**Selezione degli iperparametri sul test set.** Se ottimizzi guardando il test,
il test non e' piu' un test.

**Quote incoerenti.** Usare le quote di **chiusura** nel backtest e quelle di
**apertura** in produzione gonfia artificialmente le performance. Decidi quale
delle due e usala ovunque.

### 5.3 Come difendersi

- `.shift(1)` sistematico su ogni aggregato per squadra, ordinato per data.
- **Test di ordinamento**: se rimuovi l'ordinamento per data e il modello
  migliora, hai leakage.
- **`tests/test_no_leakage.py`** — deve esistere davvero. Verifica che, per un
  campione casuale di righe, ogni feature sia ricalcolabile usando solo le
  partite con data strettamente precedente. E' l'unica difesa automatica
  contro l'errore che uccide questo tipo di progetti. **Va scritto prima delle
  feature, non dopo.**

---

## 6. Il layer giocatori

La domanda difficile non e' "dove prendo i dati dei giocatori" ma **come li
comprimo in feature di partita** senza far esplodere la dimensionalita'.

### 6.1 I quattro livelli

**A — Valore rosa disponibile.**
Somma dei valori di mercato Transfermarkt dell'XI titolare o della rosa al netto
degli assenti. Banale, sorprendentemente competitivo. Limite: il valore di
mercato e' a sua volta un rating rumoroso e retrospettivo.

**B — Indisponibilita' pesata sui minuti.**
Per ogni partita, la quota di minuti stagionali (fino alla giornata precedente)
giocati dai calciatori assenti. Cattura "manca meta' del centrocampo titolare"
con un solo numero, e' leakage-safe per costruzione, ed e' il **miglior rapporto
segnale/sforzo dell'intero progetto**.

**C — Rating individuali appresi.**
Ogni giocatore ha un parametro di forza offensiva e difensiva stimato su tutte
le partite, in stile adjusted plus-minus del basket. La forza di squadra diventa
l'aggregazione dei rating degli undici schierati. Problema: il calcio ha pochi
eventi e poche sostituzioni, quindi i parametri individuali sono mal
identificati. Serve ridge regression con penalizzazione pesante, altrimenti
stimi rumore.

**D — VAEP su event data.**
Ogni singola azione di ogni giocatore viene valutata per quanto cambia la
probabilita' di segnare e di subire. Libreria: **`socceraction`**, dello stesso
gruppo di ricerca di `soccerdata`, che si aggancia direttamente agli eventi
WhoScored in formato Opta. E' lo stato dell'arte accessibile gratuitamente, ma
pesante da raccogliere.

### 6.2 Raccomandazione, e il motivo e' controintuitivo

**Fermati a B ora. C nella fase 3. D solo se il progetto ti appassiona davvero.**

Il punto e' che i rating di squadra **assorbono gia' gran parte
dell'informazione sui giocatori**: se il Napoli ha una rosa forte, il suo Elo e'
alto per costruzione. Il layer giocatori non aggiunge granche' in condizioni
normali.

Paga quasi tutto il suo valore nelle **discontinuita'**: mercato invernale,
infortuni pesanti prolungati, turnover nelle settimane di coppa europea. E' li'
che il modello di squadra sbaglia sistematicamente, ed e' li' che i giocatori
diventano segnale.

### 6.3 Il vincolo temporale

Le formazioni ufficiali escono circa un'ora prima del calcio d'inizio. Questo
costringe a scegliere l'orizzonte del modello:

| Modello | Informazione | Trade-off |
|---|---|---|
| **T-24h** | Nessuna formazione, solo indisponibili gia' noti (infortuni, squalifiche) | Predizione disponibile con anticipo reale, meno accurata |
| **T-1h** | XI ufficiale disponibile | Piu' accurato, finestra d'uso strettissima |

**Costruisci il dataset con entrambi i set di feature e allena due modelli.**
La differenza di RPS tra i due quantifica esattamente il valore
dell'informazione-formazione: e' un risultato interessante di per se'.

---

## 7. Specificita' del calcio italiano

**Vantaggio casalingo.** In Serie A vale circa 0.25-0.35 gol, in calo strutturale
nell'ultimo decennio. Va stimato dai dati, non fissato, e va lasciato variare nel
tempo.

**Neopromosse — cold start.** Nessuno storico in Serie A. Tre soluzioni,
combinabili:
- shrinkage verso la media di lega
- priori derivati dalla Serie B (`I2.csv`, o il modello priori della sezione 4.4)
- rating iniziale penalizzato, circa -0.3 gol di forza rispetto alla media

**Cambio allenatore.** Rompe la continuita' del rating. Il Napoli e' l'esempio da
manuale: sistemi di gioco radicalmente diversi tra un ciclo tecnico e l'altro
rendono i dati di 18 mesi prima quasi non informativi. Gestione: flag "partite
dal cambio tecnico" e abbassamento della half-life del decadimento temporale
nelle settimane successive.

**Congestione da coppe europee.** Determinante per il Napoli. Le settimane di
Champions producono turnover pesante e cali di rendimento in campionato. Va
modellato esplicitamente, non lasciato al caso.

**Mercato invernale.** Discontinuita' di rosa a meta' stagione. E' uno dei
momenti in cui il layer giocatori paga.

---

## 8. Schema dati

Tre livelli: **raw** (scaricato, immutabile), **interim** (normalizzato e unito),
**processed** (feature pronte al training).

```
data/
  raw/         un parquet per fonte/stage, mai modificato
  interim/     matches_master.parquet   una riga per partita, tutte le fonti unite
               players_master.parquet   una riga per giocatore/partita
  processed/   features.parquet         una riga per partita, solo colonne leakage-safe
               targets.parquet          y_home_goals, y_away_goals, y_1x2
```

### Tabelle

| Tabella | Contenuto | Fonte |
|---|---|---|
| `matches` | Risultato, quote, arbitro, data, giornata | football-data.co.uk + FBref |
| `team_match` | xG, xGA, PPDA, deep completions, xPts, tiri, possesso per squadra/partita | Understat + FBref |
| `lineups` | Chi ha giocato, minuti, titolare o subentrato | FBref |
| `missing` | Infortunati e squalificati per partita | WhoScored |
| `elo_daily` | Rating Elo giornaliero per squadra | ClubElo |

### Chiave di join

Ogni fonte usa identificativi propri. La chiave canonica e'
`(data_partita, squadra_casa_normalizzata, squadra_trasferta_normalizzata)`.

I nomi squadra vanno normalizzati con `teamname_replacements.json` di soccerdata
**prima** di qualsiasi join. Va costruito e testato per primo: e' la causa numero
uno di righe perse silenziosamente.

**Test di integrita' obbligatorio dopo ogni join:** il numero di righe deve
restare pari al numero di partite del calendario. Se cala, hai un mismatch di
nomi che sta buttando via dati senza avvisarti.

---

## 9. Catalogo feature

Regola universale: ogni feature calcolabile solo con informazione pre-partita.
La colonna "shift" indica come si ottiene.

### Blocco 1 — Forza di squadra

| Feature | Shift | Note |
|---|---|---|
| `elo_home`, `elo_away`, `elo_diff` | Valore ClubElo alla data, pre-partita per costruzione | Include coppe europee: cattura il fatto che il Napoli affronta avversari di livello diverso rispetto a chi gioca solo il campionato |
| `elo_league_home/away` | Elo calcolato in proprio, aggiornato dopo ogni partita | Solo campionato, K da tarare (30-40 ragionevole) |
| `dc_attack_home`, `dc_defence_home`, e simmetrici | Coefficienti Dixon-Coles ristimati su finestra mobile | I piu' informativi, i piu' costosi |

Usare **entrambi** gli Elo: quello ClubElo cattura il livello europeo, quello
proprio e' tarato sulla sola competizione modellata.

### Blocco 2 — Forma recente

Tutte con `.shift(1)` per squadra ordinata per data, media esponenziale con
half-life di circa 6 partite. Calcolate anche nella variante separata
casa/trasferta con finestra piu' lunga (10 partite), perche' i campioni si
dimezzano.

| Feature | Fonte |
|---|---|
| `np_xg_for`, `np_xg_against` | Understat |
| `ppda_for`, `ppda_against` | Understat — intensita' del pressing, cattura l'identita' tattica |
| `deep_completions_for/against` | Understat — passaggi completati vicino all'area |
| `expected_points_avg` | Understat |
| `points_minus_expected_points` | Derivata |
| `shots_for/against`, `sot_for/against` | football-data.co.uk |
| `goals_for/against` | Ridondante con xG, tenerla per confronto |

**`points_minus_expected_points` e' la feature piu' sottovalutata del catalogo.**
Misura quanti punti una squadra ha raccolto in piu' rispetto a quelli che il suo
gioco meritava. Regredisce con forza alla media, e i bookmaker sono
relativamente lenti a prezzarlo perche' il pubblico guarda la classifica, non
gli xPts.

Nota metodologica: l'xG e' molto meno rumoroso dei gol e predice i risultati
futuri meglio dei gol passati. E' il motivo per cui Understat e' la fonte piu'
importante del progetto dopo i risultati.

### Blocco 3 — Contesto partita

| Feature | Note |
|---|---|
| `rest_days_home/away` | Giorni dall'ultima partita ufficiale |
| `matches_last_14d_home/away` | Congestione |
| `european_midweek_home/away` | Ha giocato coppa europea nei 4 giorni precedenti. **Critico per il Napoli** |
| `matchday`, `month` | Effetti stagionali |
| `is_derby` | Lista manuale |
| `travel_distance_away` | Da coordinate stadi. Opzionale |
| `referee_*` | Media storica cartellini e rigori dell'arbitro. Segnale debole, costo quasi zero |

### Blocco 4 — Giocatori

| Feature | Disponibile a | Note |
|---|---|---|
| `missing_minutes_share_home/away` | T-24h | Quota di minuti stagionali degli assenti. **La feature migliore del blocco** |
| `missing_npxg90_sum_home/away` | T-24h | npxG/90 stagionale sommato degli assenti — pesa l'attaccante piu' del terzino di rotazione |
| `days_since_coach_change_home/away` | T-24h | Da CSV manuale |
| `new_coach_flag` | T-24h | Prime 5 partite di un nuovo tecnico |
| `squad_value_home/away` | T-24h | Transfermarkt, opzionale |
| ~~`turnover_index_home/away`~~ | T-1h | **Rimandata.** Titolari cambiati rispetto alla partita precedente |
| ~~`lineup_strength_home/away`~~ | T-1h | **Rimandata.** Aggregazione dei rating individuali dell'XI |

Versione 1: **solo feature T-24h**. Le due feature T-1h restano documentate
perche' il confronto tra i due orizzonti e' un esperimento da fare in seguito,
ma non entrano nel modello iniziale.

Le statistiche FBref dei giocatori della partita in corso (minuti, gol, xG)
servono **solo** a costruire aggregati storici. Mai come feature dirette della
stessa riga.

### Blocco 5 — Mercato

| Feature | Note |
|---|---|
| `p_home_mkt`, `p_draw_mkt`, `p_away_mkt` | Probabilita' implicite dalle quote, overround rimosso. Baseline: normalizzazione proporzionale di `1/quota`. Versione corretta: **metodo di Shin**, che tiene conto della presenza di scommettitori informati |
| `overround` | Margine del book, proxy dell'incertezza percepita |
| `odds_drift_home/draw/away` | Movimento apertura → chiusura. Segnale di informazione entrata nel mercato |
| `max_vs_avg_spread` | Dispersione tra bookmaker |

Le quote di chiusura di Pinnacle sono la miglior singola predizione esistente.

### Blocco 6 — Scontri diretti

`h2h_home_wins_last5`, `h2h_goals_avg`.

Da includere e poi **verificare**: in letteratura il segnale h2h scompare quasi
del tutto una volta controllata la forza delle squadre. Se il modello lo ignora,
e' un risultato corretto, non un bug.

### Target

```
y_home_goals, y_away_goals    per i modelli Poisson
y_1x2                          H / D / A, per i classificatori e la valutazione
```

---

## 10. Modelli

In sequenza. Ogni modello va valutato prima di passare al successivo.

Tutti i modelli predittivi hanno come target i **gol**. L'1X2 non e' mai un target
diretto: si ottiene sempre integrando la matrice dei risultati esatti.

| ID | Modello | Target | Libreria | Ruolo |
|---|---|---|---|---|
| **M0** | Vince sempre il padrone di casa | — | — | Sanity floor, ~44% accuracy |
| **M1** | Solo probabilita' implicite di mercato | — | — | **Benchmark da battere** |
| **M2** | GLM Poisson semplice (forza attacco/difesa + vantaggio casa) | Gol | statsmodels | Baseline interpretabile |
| **M3** | Dixon-Coles con decadimento temporale | Gol | statsmodels + ottimizzatore custom | Primo salto di qualita' |
| **M4** | Due LightGBM con obiettivo Poisson (gol casa, gol trasferta) | Gol | lightgbm | Interazioni non lineari sulle feature |
| **M5** | Ensemble di M3 e M4 sulle lambda attese, piu' il mercato | Gol | scikit-learn | Modello finale |

### Dalla matrice dei gol all'1X2

Dato `lambda_home` e `lambda_away`, si costruisce la matrice congiunta dei
risultati esatti fino a un massimo ragionevole (8-10 gol per lato) e si sommano
le celle:

```
P(1) = somma delle celle con gol_casa > gol_trasferta
P(X) = somma della diagonale
P(2) = somma delle celle con gol_casa < gol_trasferta
```

La correzione di Dixon-Coles si applica alle quattro celle a punteggio basso
(0-0, 1-0, 0-1, 1-1), dove l'indipendenza tra le due Poisson e' violata: i
pareggi a reti bianche e le partite bloccate sono piu' frequenti di quanto la
Poisson pura preveda.

Dalla stessa matrice escono **gratis**: Over/Under a qualsiasi soglia,
Goal/NoGoal, risultato esatto, handicap asiatico.

**Usa sempre le probabilita', mai le classi.**

**Calibrazione.** Dopo M4, applicare isotonic regression o Platt scaling sul
validation set. I gradient boosting producono probabilita' sistematicamente
sovra-confidenti, e con RPS come metrica la calibrazione vale piu' di mezzo punto.

**Regolarizzazione.** Con ~20.000 righe e ~50 feature, LightGBM va regolarizzato
in modo aggressivo: `num_leaves` basso, `min_child_samples` alto,
`feature_fraction` e `bagging_fraction` sotto 1.

**Estensione opzionale:** Poisson bivariato gerarchico bayesiano con PyMC, per
avere incertezza sui parametri e shrinkage automatico sulle neopromosse. Elegante,
ma da rimandare.

**Reti neurali: escluse.** A questa scala di dati non c'e' guadagno atteso
sufficiente a giustificare la complessita'.

### Librerie

| Ruolo | Libreria |
|---|---|
| ETL | `pandas`, `numpy`, `pyarrow` |
| Ingestion | `soccerdata` |
| Pipeline e metriche | `scikit-learn` |
| GLM Poisson | `statsmodels` |
| Gradient boosting | `lightgbm` |
| Grafici e calibrazione | `matplotlib` |
| Tracking esperimenti | `mlflow`, o anche solo un CSV di log |
| Event data (fase 4) | `socceraction` |

Il tracking degli esperimenti non e' un lusso: con walk-forward validation ne
farai molti.

---

## 11. Valutazione

### 11.1 Split

**Mai casuale.** Walk-forward per giornata: si allena su tutto cio' che precede
la giornata N, si predice la N, si riallena includendola. Test set: le ultime
tre stagioni complete.

E' l'unico schema che rispecchia l'uso reale del modello.

### 11.2 Metriche

| Metrica | Ruolo |
|---|---|
| **RPS (Ranked Probability Score)** | **Primaria.** Standard nel forecasting calcistico: tiene conto del fatto che gli esiti sono ordinali (H > D > A), quindi sbagliare prevedendo 1 quando esce 2 e' peggio che sbagliare prevedendo X |
| Log loss | Secondaria |
| Brier score multiclasse | Secondaria |
| Curva di calibrazione + ECE | Diagnostica: quando dici 70%, deve uscire il 70% |
| Accuracy | **Solo per comunicare, mai per selezionare modelli.** Butta via l'informazione probabilistica e non distingue una previsione al 51% da una al 90% |
| **Delta RPS vs M1** | La domanda vera: sto aggiungendo informazione al mercato? |

### 11.3 Il benchmark

Converti le quote di chiusura in probabilita' implicite, rimuovi l'overround
(proporzionale come baseline, Shin come versione corretta) e calcola l'RPS del
bookmaker sullo stesso test set. **Quello e' il muro.**

### 11.4 Segnali di allarme

Se un modello batte il mercato di piu' del 2-3% di RPS, la spiegazione piu'
probabile, in ordine:

1. Leakage
2. Split non temporale
3. Selezione degli iperparametri sul test set
4. Backtest su quote di apertura invece che di chiusura

Verificare prima di festeggiare.

---

## 12. Aspettative realistiche

Il tetto pratico per l'1X2 in Serie A e' **53-58% di accuracy**, e i bookmaker
stanno li'. RPS di un buon modello: circa **0.19-0.21**, sostanzialmente in linea
con il mercato.

**Nessun modello amatoriale batte le quote di chiusura in modo sistematico.**
Quelle incorporano informazioni non disponibili: formazioni ufficiali, flussi di
scommesse, informazione privata.

Questo non rende il progetto inutile — lo rende onesto. L'obiettivo realistico e'
costruire un modello **calibrato e competitivo** con il mercato, capire dove e
perche' diverge, e imparare l'intera catena da dati grezzi a predizione in
produzione.

---

## 13. Struttura del repo

```
serie-a-predictor/
  data/                      raw / interim / processed  (gitignored)
  src/
    ingest.py                download multi-fonte (gia' scritto)
    normalize.py             mapping nomi squadra, chiave canonica, join
    features/
      team_strength.py       Elo, coefficienti Dixon-Coles
      form.py                rolling window leakage-safe
      context.py             riposo, congestione, coppe
      players.py             assenze pesate, turnover, forza XI
      market.py              de-vigging (proporzionale e Shin), drift quote
    models/
      baseline.py            M0, M1, M2
      dixon_coles.py         M3
      gbm.py                 M4, M5
      ensemble.py            M6
    evaluate.py              RPS, calibrazione, walk-forward
    predict.py               pipeline di inferenza settimanale
  manual/
    coach_changes.csv        cambi allenatore
    derbies.csv              lista derby
    team_name_map.json       mapping nomi tra fonti
  notebooks/                 esplorazione, non codice di produzione
  tests/
    test_no_leakage.py       il test piu' importante del repo
    test_join_integrity.py   conteggio righe dopo ogni join
```

---

## 14. Pipeline di ingestion

Il modulo `ingest.py` e' gia' scritto, con API verificata. Stage indipendenti e
ri-eseguibili: `soccerdata` mantiene una cache locale persistente in
`~/soccerdata/data/`, quindi rilanciare uno stage completato non ri-scarica nulla.

```bash
pip install soccerdata pandas pyarrow lightgbm scikit-learn statsmodels
```

| Stage | Comando | Tempo | Note |
|---|---|---|---|
| `matches` | `python ingest.py --stage matches` | ~10 secondi | Risultati e quote |
| `understat` | `python ingest.py --stage understat` | ~2 minuti | **xG, PPDA, xPts** |
| `schedule` | `python ingest.py --stage schedule` | ~5 minuti | Fornisce i `match_id` |
| `elo` | `python ingest.py --stage elo` | ~1 minuto | Richiede `schedule` |
| `shots` | `python ingest.py --stage shots` | Decine di minuti | Opzionale |
| `team_stats` | `python ingest.py --stage team_stats` | Ore | Opta avanzate |
| `lineups` | `python ingest.py --stage lineups` | Ore | Una richiesta per partita |
| `player_stats` | `python ingest.py --stage player_stats` | Molte ore | Lanciare una stagione alla volta |
| `missing` | `python ingest.py --stage missing` | Ore | **Richiede browser (Selenium)** |

**I primi quattro stage bastano per l'80% del potere predittivo.** In una decina
di minuti hai xG, PPDA, xPts, Elo e quote: cioe' i blocchi feature 1, 2, 3 e 5.

Gli stage lenti si lanciano di notte. Sono interrompibili e riprendibili grazie
alla cache.

---

## 14bis. Ciclo di aggiornamento settimanale

### L'aggiornamento e' automatico

Verificato nel sorgente: `soccerdata` distingue le stagioni concluse da quella
in corso tramite `_is_complete()`, che confronta la data odierna con la fine
stagione dichiarata nel `LEAGUE_DICT`. Per la stagione corrente passa
`no_cache=True`, quindi **bypassa la cache e riscarica sempre**. Le stagioni
concluse restano in cache e non vengono mai ri-scaricate.

In `match_history`:

```python
current_season = not self._is_complete(lkey, skey)
reader = self.get(url, filepath, no_cache=current_season)
```

In `understat` la logica e' identica, con in piu' un parametro `force_cache`
per ottenere il comportamento opposto se servisse.

**Conseguenza: non devi aggiungere nulla a mano.** Basta rilanciare gli stessi
script dopo ogni giornata.

### Il ciclo

```bash
python ingest.py --stage matches      # riscarica solo la stagione corrente
python ingest.py --stage understat    # idem
python build_features.py              # ricostruisce features.parquet
python train.py                       # riallena da zero
python predict.py --matchday N        # predice la prossima giornata
```

**Riallenare da zero, sempre.** Con ~20.000 righe l'addestramento dura secondi
o pochi minuti. L'apprendimento incrementale introdurrebbe complessita' e drift
accumulato senza alcun beneficio. Elo e coefficienti Dixon-Coles si aggiornano
naturalmente perche' ricalcolati sull'intera storia.

**Logga le predizioni prima che le partite si giochino**, su file append-only
con timestamp. E' l'unico modo di costruire un track record credibile e non
contaminabile a posteriori, e serve ad accorgersi se il modello degrada.

```
predictions_log.csv
  timestamp_prediction, match_date, home_team, away_team,
  lambda_home, lambda_away, p_home, p_draw, p_away,
  p_over25, model_version
```

### Il cold start di inizio stagione

Problema concreto: alla terza o quarta giornata le medie mobili della stagione
corrente hanno due o tre partite. Se le finestre si azzerano al cambio stagione,
il modello e' cieco fino a novembre.

**Regola: le finestre mobili non si azzerano mai al confine di stagione.**
L'ordinamento e' per squadra e per data, continuo attraverso le stagioni.

Al confine tra stagioni si applica una **regressione verso la media di lega del
25-35%**, che rappresenta l'incertezza introdotta da mercato e cambi tecnici.
E' quello che fa ClubElo, ed e' il motivo per cui il suo Elo a settembre e' gia'
informativo.

Aggiungere la feature `matches_this_season`: dice al modello quanto fidarsi dei
dati di stagione corrente rispetto a quelli riportati dall'anno precedente.

**Corollario.** A inizio stagione le quote dei bookmaker valgono piu' del solito,
perche' incorporano le informazioni di mercato che i dati storici non hanno
ancora visto. Avendole incluse come feature, il modello e' coperto proprio nel
periodo in cui il resto e' debole.

---

## 15. Milestone

| # | Obiettivo | Deliverable | Stima |
|---|---|---|---|
| 1 | Dati grezzi scaricati e normalizzati | `matches_master.parquet`, conteggio righe verificato, `team_name_map.json` completo | 2-3 giorni |
| 2 | Feature blocchi 1, 2, 3, 5 | `features.parquet`, `test_no_leakage.py` verde | 1 settimana |
| 3 | M0, M1, M2 valutati in walk-forward | Tabella RPS con M1 come riferimento | 3 giorni |
| 4 | Dixon-Coles (M3) | Modello a gol funzionante, coefficienti attacco/difesa interpretabili | 1 settimana |
| 5 | Layer giocatori (blocco 4) | Feature assenze, confronto T-24h vs T-1h | 1 settimana |
| 6 | GBM ed ensemble (M4, M5, M6) | Modello finale calibrato | 1 settimana |
| 7 | Pipeline di inferenza | Report settimanale con probabilita' e filtro Napoli | 3 giorni |

**Fase 4 opzionale**, se il progetto continua a interessare: event data e VAEP
con `socceraction`, scraper FotMob per l'xG di Serie B, modello bayesiano
gerarchico, layer di spiegazione delle predizioni in linguaggio naturale.

---

## 16. Cosa serve da te

1. **`manual/coach_changes.csv`** — colonne:
   `league, season, team, date, coach_out, coach_in`.
   Per Serie A ultimi 10 anni sono circa 150 righe. Wikipedia le ha tutte.
   E' un'ora di lavoro per una delle feature piu' informative del progetto.

2. **Decisione su Transfermarkt** — vale lo scraper? Se il blocco 4 con le sole
   assenze funziona bene, probabilmente no.

3. **Vincolo di tempo macchina** — lo stage `player_stats` richiede diverse ore.
   Se hai una macchina che puoi lasciare accesa, si parte subito; altrimenti si
   rimanda il blocco 4 e si costruisce tutto il resto.

4. **Output dei primi stage** — dopo aver lanciato `matches` e `understat`,
   serve `sorted(set(df["home_team"]))` per entrambe le fonti, per costruire il
   mapping dei nomi squadra e i test di integrita' dei join.

---

## 17. Trappole operative

| Trappola | Sintomo | Soluzione |
|---|---|---|
| **Nomi squadra non allineati tra fonti** | Righe che spariscono silenziosamente nei join | `team_name_map.json` + test di conteggio righe dopo ogni join. Causa numero uno di problemi |
| **Understat richiede una libreria TLS da GitHub al primo avvio** | Errore poco chiaro all'istanziazione dello scraper, tipicamente dietro proxy o firewall aziendale | Download manuale dalle release di `bogdanfinn/tls-client` |
| **FBref rate limiting** | Stage che sembrano bloccati | Sono lenti per design. La cache rende tutto incrementale: interrompi e riprendi |
| **Copertura FBref sulle stagioni vecchie** | Colonne avanzate assenti prima del 2017/18 | Usare Understat come base (dal 2014/15) e FBref come arricchimento opzionale |
| **WhoScored richiede Selenium** | Stage `missing` fallisce | Installare Chrome/Chromium. Il modello T-24h puo' partire anche senza |
| **Statistiche post-partita usate come feature** | Accuracy irrealisticamente alta | `test_no_leakage.py` |
| **Split casuale** | Accuracy irrealisticamente alta | Walk-forward per data |
| **Quote di apertura nel backtest, chiusura in produzione (o viceversa)** | Performance gonfiate | Coerenza: una sola scelta ovunque |
| **Accuracy come metrica di selezione** | Modelli mal calibrati che sembrano buoni | RPS come primaria |
