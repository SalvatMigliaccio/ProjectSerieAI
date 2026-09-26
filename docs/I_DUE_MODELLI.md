# I due modelli, dall'inizio

Come sono costruiti **M1** (quello che gira in produzione) e il **modello di
machine learning** (M4/M5/M6, la famiglia LightGBM), passo per passo, dalle
quote grezze alla probabilita' di ogni mercato.

Questo documento spiega **come funzionano**. Perche' sia M1 a girare in
produzione, e con quali numeri lo si e' deciso, sta in `CLAUDE.md`. Come si
lanciano i comandi sta in `docs/COMANDI.md`.

---

## 0. La cosa che hanno in comune, e da cui conviene partire

Qualunque modello in questo progetto — dal piu' stupido al piu' complicato —
produce **due numeri per partita**:

```
lambda_casa    i gol attesi della squadra di casa
lambda_fuori   i gol attesi della squadra in trasferta
```

Non produce "1", non produce "over 2.5", non produce una percentuale di
vittoria. Produce due medie di gol. Tutto il resto **si deriva**, e si deriva
con lo stesso identico codice per tutti i modelli.

Questa e' la decisione di fondo del progetto, ed e' bloccata: *il target sono i
gol, l'1X2 e' un output derivato*. Il motivo e' che dai gol si ricava ogni
mercato in modo coerente, mentre dall'1X2 non si torna indietro. Un modello che
stimasse direttamente P(1), P(X), P(2) non saprebbe dire niente sull'over 2.5,
e un secondo modello per l'over potrebbe contraddire il primo — dando
l'impressione di aver trovato un'occasione dove c'e' solo un'incoerenza.

### Dai due lambda a tutti i mercati

Il passaggio e' in `models/baseline.py::score_matrix` e vale per ogni modello.

**Primo: la matrice dei risultati esatti.** Con due Poisson indipendenti,

```
P(i gol in casa, j gol fuori) = Poisson(i | lambda_casa) * Poisson(j | lambda_fuori)
```

Si calcola per ogni `i` e `j` da 0 a `config.MAX_GOALS` (= 10), ottenendo una
matrice 11x11 per partita. Si rinormalizza perche' il troncamento a 10 gol
perde un filo di massa nella coda.

**Secondo: ogni mercato e' una somma di celle di quella matrice.**

| mercato | quali celle |
|---|---|
| 1 | tutte quelle con `i > j` |
| X | la diagonale, `i == j` |
| 2 | tutte quelle con `i < j` |
| over 2.5 | tutte quelle con `i + j > 2.5` |
| gol-gol | tutto tranne la prima riga e la prima colonna (con lo 0-0 ridato indietro, altrimenti tolto due volte) |
| 1X | l'unione delle celle di 1 e di X — **esattamente** P(1) + P(X) |

**Perche' questo conta**: doppia chance, over/under e mercati gol non sono
modelli diversi, sono **viste diverse sulle stesse celle**. Per costruzione non
possono contraddirsi. Se la doppia chance venisse da una stima separata, prima
o poi P(1X) risulterebbe diversa da P(1) + P(X), e quella differenza sembrerebbe
un'opportunita' mentre sarebbe un bug.

**La correzione di Dixon-Coles** (`rho`) modifica i quattro punteggi bassi —
0-0, 0-1, 1-0, 1-1 — dove l'indipendenza fra i due Poisson sbaglia di piu': nel
calcio 0-0 e 1-1 sono piu' frequenti di quanto due Poisson indipendenti
prevedano, perche' il punteggio in corso cambia il modo di giocare. **In
produzione e' spenta** (`config.DC_RHO = 0.0`): stimare rho e' il mestiere di
M3, non di un baseline.

**La quota equa** e' `1 / p`, e basta. E' il prezzo a cui la puntata avrebbe
valore atteso zero. Serve a leggere la quota del book accanto: sopra la quota
equa si sarebbe in vantaggio, sotto in svantaggio. **Con M1 la quota reale e'
sempre sotto**, e il capitolo 2.6 spiega perche' non puo' essere altrimenti.

---

## 1. M1 — il modello market-only

### Cos'e', in una riga

**M1 e' la quota di apertura del bookmaker con il margine tolto, riportata
sulla scala dei gol.** Non si addestra, non ha parametri stimati, non vede un
solo risultato passato. E' una trasformazione deterministica di tre quote 1X2
e due quote over/under.

Per capirlo davvero serve vedere i cinque passaggi con dei numeri veri.
L'esempio che segue e' **Napoli-Bologna 2026/27**, giornata 4, calcolato con il
codice di produzione.

### Passo 0 — da dove arrivano le quote

Da `data/raw/fixtures_odds.parquet`, lo snapshot che
`goalmodel ingest --stage fixtures` scarica da football-data.co.uk. Sono le
quote di **apertura**, fotografate il **venerdi' entro le 17:00 UK**.

Il riferimento e' **B365**, e la scelta non e' arbitraria: e' l'unico book con
la terzina 1X2 completa al 100% su tutte e tredici le stagioni dello storico
(`goalmodel features-market --coverage`). Esiste una catena di ripieghi
(`BOOKS_1X2 = B365, BW, IW, PS, WH, VC, Avg, BbAv`) che tappa buchi sporadici
riga per riga, e la colonna `mkt_1x2_source` registra chi ha risposto.

**Perche' l'apertura e non la chiusura.** La quota di chiusura si forma pochi
minuti prima del fischio e incorpora le formazioni ufficiali, che sono **fuori
dall'orizzonte T-24h** del progetto. Usarla in addestramento darebbe un modello
piu' bravo nel backtest e non alimentabile in produzione. Il drift
apertura-chiusura ha deviazione standard di ~3 punti di probabilita': non e'
rumore, e' esattamente l'informazione che a T-24h non si ha.

```
1) QUOTE B365 apertura   1: 1.80   X: 3.60   2: 4.75
   over/under 2.5        O: 2.10   U: 1.73
```

### Passo 1 — togliere il margine (de-vigging)

Una quota **non e' una probabilita'**. Il suo reciproco e' una probabilita'
*gonfiata*:

```
2) reciproci   [0.55556  0.27778  0.21053]   somma = 1.04386
```

Quella somma dovrebbe fare 1 e fa 1.044: il **4.4% in piu' e' il margine del
banco**. Vanno tolti, e come li si toglie cambia il risultato.

**Metodo 1, proporzionale**: si divide ciascun reciproco per la somma. Assume
che il book carichi il margine in proporzione alla probabilita' di ogni esito.
E' l'ipotesi piu' semplice ed e' quasi certamente falsa.

**Metodo 2, Shin (1992)** — quello usato. Il margine e' la difesa del book
contro gli scommettitori informati, che sono una frazione `z` del volume:

```
p_i = [ sqrt(z² + 4(1-z) · pi_i² / PI) - z ] / (2(1-z))
```

con `pi_i = 1/quota_i`, `PI` la loro somma, e `z` scelto in modo che le `p_i`
sommino a 1. **`z` si trova per bisezione**, non con Newton: la somma delle
`p_i` e' monotona decrescente in `z` e i bordi sono noti, quindi 60 bisezioni
vettorializzate su `[0, 0.99]` costano meno di una singola iterazione di Newton
riga per riga.

```
   proporzionale  [0.53221  0.26611  0.20168]
   Shin           [0.53870  0.26389  0.19741]   z = 0.02203
   scarto (punti %)  +0.649   -0.221   -0.428
```

**Cosa ha fatto Shin, e perche' e' il verso giusto.** Ha spostato mezzo punto
percentuale *verso* il favorito e l'ha tolto agli altri due. Sotto il modello di
Shin il margine grava di piu' sugli esiti improbabili, quindi normalizzare e
basta — il metodo proporzionale — lascerebbe sovrastimati gli esiti a quota
alta. La `z` stessa e' informativa: misura quanta asimmetria informativa il
book percepisce su quella partita.

Caso limite gestito: se l'overround fosse `<= 1` (succede sulle colonne `Max`,
dove il massimo di mercato puo' produrre un libro in arbitraggio) il modello di
Shin non ha soluzione con `z` positivo, e si ricade sul proporzionale — che e'
esattamente il limite della formula per `z -> 0`.

### Passo 2 — dal mercato gol al totale atteso

Lo stesso de-vigging si applica alle due quote over/under 2.5:

```
3) over/under de-viggato  [0.44908  0.55092]   z = 0.05425
```

Ora serve il passaggio che porta il mercato **sulla scala dei gol**: quale
`lambda` totale, sotto Poisson, riproduce quel 44.908% di over?

Over 2.5 significa "almeno 3 gol", cioe' `P(X > 2) = sf(2, lambda)`. Quella
funzione e' strettamente crescente in `lambda`, quindi di nuovo **bisezione**,
su `[0.05, 8]` — nessun campionato ha mai una media credibile fuori da li'.

```
   lambda totale che riproduce quel P(over): 2.47236
```

Il mercato sta dicendo: *in questa partita ci si aspettano 2.47 gol in totale.*

### Passo 3 — spezzare il totale in due

Il totale non dice **chi** li segna. Quello lo dice l'1X2.

Si cerca la **supremazia** `d` tale che, ponendo

```
lambda_casa  = (totale + d) / 2
lambda_fuori = (totale - d) / 2
```

e incrociando due Poisson indipendenti, la probabilita' di vittoria casalinga
coincida con quella che il mercato esprime (0.53870). Ancora bisezione, 40
iterazioni, con il vincolo `|d| < totale` perche' un lambda negativo non
esiste.

```
4) supremazia d trovata: 0.70209
   lambda casa  = (2.47236 + 0.70209)/2 = 1.58723
   lambda fuori = (2.47236 - 0.70209)/2 = 0.88513
```

**Questi due numeri sono M1.** Tutto il resto e' la derivazione comune del
capitolo 0.

### Passo 4 — la matrice, e i mercati

```
5) matrice dei risultati: 11x11, somma = 1.0
   i cinque risultati piu' probabili:
      1-0   0.1339
      1-1   0.1186
      2-0   0.1063
      2-1   0.0941
      0-0   0.0844

7) mercati derivati, con la quota equa:
      1            p = 0.5387   quota equa = 1.86
      X            p = 0.2517   quota equa = 3.97
      2            p = 0.2096   quota equa = 4.77
      1X           p = 0.7904   quota equa = 1.27
      X2           p = 0.4613   quota equa = 2.17
      over 2.5     p = 0.4491   quota equa = 2.23
      under 2.5    p = 0.5509   quota equa = 1.82
      gol-gol      p = 0.4672   quota equa = 2.14
      casa segna   p = 0.7955   quota equa = 1.26
```

La partita e' finita **1-0**, che era il risultato singolo piu' probabile
secondo il modello.

### Passo 5 — il costo del giro sui gol, misurato

C'e' un dettaglio in quell'esempio che vale la pena guardare:

```
6) 1X2 ricostruito dalla matrice:  [0.53870  0.25168  0.20962]
   1X2 del mercato, diretto:       [0.53870  0.26389  0.19741]
   differenza:                     [ 0.0000  -0.0122  +0.0122 ]
```

La **P(1) torna identica** — ovvio, e' il bersaglio su cui la bisezione ha
cercato `d` — ma **pareggio e vittoria esterna si spostano di 1.2 punti**. Il
giro d'andata e ritorno attraverso due Poisson indipendenti non e' esatto:
l'indipendenza sottostima leggermente il pareggio, che e' proprio la cosa che
la correzione di Dixon-Coles servirebbe a rimettere a posto.

Per questo esistono **due versioni del market-only**:

| modello | cos'e' | RPS sul test |
|---|---|---|
| **M1b** `MarketDirect` | le probabilita' de-viggate **cosi' come sono**, senza passare dai gol | **0.1881** |
| **M1** `MarketOnly` | le stesse quote riportate sulla scala dei gol e ripassate dalla matrice | 0.1882 |

M1b e' il market-only nella sua forma piu' pura ed e' il **riferimento contro
cui si misura tutto**. M1 e' quello che gira in produzione, perche' produce i
due lambda — e dai lambda si ricavano tutti i mercati, mentre da tre
probabilita' 1X2 non si ricava l'over 2.5. La differenza fra i due,
**+0.00004 di RPS**, e' il prezzo di quella comodita': misurato, dichiarato, e
dentro il rumore.

### 2.6 — Perche' M1 non puo' produrre valore atteso positivo

Non e' una cautela, e' aritmetica. Se `p = pi / 1.044` (la probabilita'
de-viggata) e la quota offerta e' `1 / pi`, allora

```
EV = p · (1/pi) - 1 = (pi/1.044) · (1/pi) - 1 = 1/1.044 - 1 = -4.4%
```

**Lo stesso identico numero su tutti e tre gli esiti della partita**, e non
dipende da quale si sceglie: dipende solo dall'overround, cioe' dal margine che
quel book ha caricato su quella partita. Fra una partita e l'altra cambia
poco — sullo storico la media e' -5.2%.

Verificato sul test set: su 3420 possibili puntate contro B365, le puntate a EV
positivo sono **zero**. E' per questo che il progetto mostra la
quota equa ma non calcola mai il valore atteso al posto di chi legge: sarebbe un
numero circolare per costruzione.

### Dove sta il codice

| passo | file |
|---|---|
| scelta delle quote, riga per riga | `features/market.py::pick_odds` |
| de-vigging di Shin | `features/market.py::devig_shin` |
| dal P(over) al totale gol | `features/market.py::implied_total_goals` |
| dal P(1) alla supremazia | `features/market.py::implied_lambdas` |
| il modello | `models/baseline.py::MarketOnly` (M1), `MarketDirect` (M1b) |
| matrice e mercati | `models/baseline.py::score_matrix`, `all_markets` |

---

## 2. Il modello di machine learning

Qui l'impostazione e' completamente diversa: invece di leggere una risposta
gia' data dal mercato, si **impara** dai dati. La famiglia e' LightGBM, e
conta tre modelli piu' una variante — M4, M5, M6 e la media dei semi.

### 2.1 Primo ingrediente: le feature di forma

Prima del modello vengono le colonne, e sono costruite da
`features/form.py`.

**Il problema di partenza.** La tabella delle partite e' "larga": una riga per
partita, con colonne separate per casa e trasferta. Ma **la forma e' una
proprieta' della squadra, non della partita**. Quindi:

1. si passa a formato **lungo** — una riga per squadra per partita;
2. si calcolano le medie mobili per squadra, ordinate nel tempo;
3. si torna al formato largo agganciando i valori a casa e trasferta.

**Le otto statistiche mediate**, ognuna in due versi (fatta / subita):

```
goals    gol segnati                    shots    tiri
np_xg    xG esclusi i rigori            sot      tiri in porta
ppda     passaggi concessi per azione   xpts     punti attesi
deep     tocchi in area avversaria      points   punti veri
```

**La media e' esponenziale, con half-life 6 partite**
(`config.FORM_HALFLIFE`). Half-life 6 significa che dopo sei partite il peso di
un'osservazione si e' dimezzato:

```
alpha = 1 - 0.5^(1/6) = 0.109
stato_nuovo = stato + alpha · (valore_osservato - stato)
```

**Due regole rendono tutto corretto, e sono il cuore della faccenda.**

**Regola 1 — il valore assegnato a una partita e' lo stato PRIMA di quella
partita.** Nel ciclo si scrive l'output e *solo dopo* si aggiorna lo stato:

```python
# Output PRIMA dell'aggiornamento: qui sta la garanzia anti-leakage.
for c in value_cols:
    out[c][pos] = state.get(c, np.nan)
...
state[c] = state[c] + alpha * (v - state[c])
```

Cosi' una feature **non puo' fisicamente** contenere informazione della partita
che deve predire. E' la regola non negoziabile n.1 del progetto, resa vera da
tre righe di codice e dal loro ordine.

**Regola 2 — le finestre non si azzerano al cambio stagione, si attenuano.**
Al confine lo stato viene tirato verso la media di lega del **30%**
(`config.SEASON_REGRESSION`), a rappresentare l'incertezza da mercato e cambi
tecnici. Azzerare renderebbe il modello cieco fino a novembre; non fare niente
ignorerebbe che una squadra ad agosto non e' quella di maggio.

Ed e' scritto come ciclo Python e non con `pandas.ewm()` di proposito: `ewm()`
non sa niente dei confini di stagione e non permette di intervenire sullo
stato. Su ~9000 righe il ciclo costa meno di un secondo.

**Il risultato: 52 colonne**, il set `BASE`, congelato nel registro
`features/sets.py`:

```
8 statistiche x 2 versi x 3 viste (casa, trasferta, differenza) = 48
+ 4 contatori (partite giocate in stagione e in totale, per lato) = 52
```

### 2.2 M4 — il gradient boosting sui gol

`models/gbm.py::PoissonGBM`.

**Due modelli separati, non uno.** Un LightGBM per i gol di casa, un altro per
i gol in trasferta, entrambi con obiettivo **Poisson** — cioe' la stessa
famiglia di distribuzioni della matrice dei risultati. Le uscite sono i due
lambda, e da li' in poi si riusa la derivazione comune del capitolo 0.

**Niente identita' di squadra.** Non ci sono variabili dummy "questa e'
l'Inter". Tutta la forza di una squadra deve arrivare dalle sue medie mobili.
E' una limitazione voluta: l'identita' la modellano gia' M2 (GLM Poisson) e M3
(Dixon-Coles), e cosi' il confronto diventa interpretabile — se M4 perde contro
M3, vuol dire che la forza stimata dai risultati batte le medie mobili delle
statistiche.

**Il numero di alberi non e' un iperparametro.** Lo decide l'**arresto
anticipato** su un ritaglio della **coda del training** (le ultime 380 righe,
cioe' una stagione). Poi si riaddestra su tutto il training con il numero di
alberi trovato. Non e' leakage: la coda del training e' informazione gia'
disponibile a T-24h.

**`use_market` decide quale domanda si sta facendo**: con le quote fra le
feature (M4-con-mercato) o senza (M4-senza-mercato). Sono due esperimenti
diversi, non due configurazioni.

### 2.3 M5 — il GBM ancorato al mercato

`models/gbm.py::MarketAnchoredGBM`. **E' il modello ML piu' importante del
progetto**, e la ragione per cui esiste vale piu' del codice.

**Il problema di M4-con-mercato.** Fra cinquanta colonne, le quote sono una
delle cinquanta. Il modello deve *prima* imparare a ricostruire il mercato dai
suoi ingressi — spendendo alberi, e sbagliando — e solo dopo puo' correggerlo.
Il test risulta cosi' sotto-potenziato: non sta chiedendo "le feature
aggiungono qualcosa alle quote?", sta chiedendo "il modello sa ricostruire le
quote e poi migliorarle?".

**La soluzione: il mercato come punto di partenza, non come feature.**
LightGBM accetta un `init_score`, un offset iniziale in scala logaritmica. M5
gli passa il logaritmo dei lambda di mercato:

```python
init = np.log(df["mkt_lambda_home"].to_numpy())
model = self._fit_one(x, df["FTHG"].to_numpy(), n_head, init=init)
```

Cosi' il modello **non stima i gol: stima lo scarto dai gol gia' previsti dal
mercato.** Ogni albero puo' occuparsi solo del residuo, con tutta la capacita'
puntata sulla domanda giusta. E le feature sono **solo quelle non di mercato**:
ridargli le quote insieme all'ancoraggio significherebbe chiedergli di
correggere il mercato usando il mercato.

**La proprieta' che rende il test decisivo.** Se nelle feature non c'e'
informazione che il mercato non abbia gia', il modello non trova nulla da
correggere, l'arresto anticipato ferma subito il bosco e **M5 degenera
esattamente nel mercato**. Verificato direttamente: azzerando le feature si
ferma a **1 albero** e lo scarto logaritmico dal mercato e' **0.00000**.

Quindi la lettura del risultato e' netta:

| esito | significato |
|---|---|
| M5 **meglio** del mercato, intervallo netto | il segnale c'e' |
| M5 **indistinguibile** dal mercato | non c'e', e la domanda e' chiusa |
| M5 **peggio** del mercato | il modello aggiunge rumore: regolarizzazione troppo debole, il test non e' valido e va rifatto |

L'ultimo caso e' il motivo per cui M5 ha uno spazio di ricerca **piu'
regolarizzato** di M4 (`learning_rate` fino a 0.0025, `reg_lambda` fino a 150):
un residuo si sovradatta piu' facilmente di un livello, e un M5
sotto-regolarizzato perderebbe contro il mercato per il motivo sbagliato.

**La trappola del `predict()`, che va saputa.** LightGBM restituisce
`exp(somma degli alberi)` e **non riaggiunge l'init_score**. Va sommato a mano
in scala logaritmica:

```python
raw  = model.predict(x, raw_score=True)      # solo la somma degli alberi
base = np.log(test["mkt_lambda_home"])       # l'ancoraggio
lam  = np.exp(base + raw)                    # il lambda vero
```

Sbagliarlo **non solleva nessun errore**: produce semplicemente il modello
sbagliato, che prevede il residuo al posto dei gol.

### 2.4 M6 — la miscela, e perche' esiste

`models/gbm.py::LogBlend`. Media geometrica fra i lambda del mercato e quelli
di M4-senza-mercato, con un peso `w` unico:

```
lambda = lambda_mercato^(1-w) · lambda_M4^w
```

E' il **controllo povero**: un solo parametro libero, impossibile
sovradattarlo. Se M5 — flessibilissimo — e M6 — rigidissimo — danno la stessa
risposta, quella risposta non dipende dalla forma del modello. (Danno la stessa
risposta: entrambi indistinguibili dal mercato.)

### 2.5 La media dei semi

`experiments/modelli.py::M5MediaSemi`. Cinque M5 identici tranne che per il
seme, e la media geometrica dei loro lambda (cioe' la media aritmetica dei
log-lambda).

**Non e' un modello nuovo ed e' importante capirlo**: e' una riduzione di
varianza della *stima*, ed e' ricostruibile esatta dai cinque modelli singoli
(verificato bit a bit). Serve perche' un risultato non dipenda dal seme che
capita: sul blocco giocatori l'intervallo della differenza si stringe da
±0.00027 a ±0.00022 e la stima si assesta al centro dei cinque semi.

**Per le misure di un blocco si usa la media dei semi, dichiarata prima.**

### 2.6 Gli iperparametri

Sei dimensioni, esplorate per **ricerca casuale** e non a griglia — 144
combinazioni per due varianti non sono sostenibili, e su sei dimensioni la
ricerca casuale copre meglio a parita' di budget. Un giro costa 2-7 minuti su
8 processi.

```
learning_rate      0.01, 0.02, 0.03          (M5: 0.0025, 0.005, 0.01)
num_leaves         4, 6, 8
min_child_samples  50, 75, 100
colsample_bytree   0.6, 0.7, 0.8
subsample          0.6, 0.7, 0.8
reg_lambda         1, 5, 20                  (M5: 20, 50, 150)
```

Gli alberi non ci sono, per il motivo gia' detto. La taratura sta in
`evaluation/taratura.py`, non dentro il modello: tarare e' misurare, e il
modello e' l'oggetto della misura, non chi la fa.

### Dove sta il codice

| pezzo | file |
|---|---|
| le medie mobili | `features/form.py` |
| il registro dei set di colonne | `features/sets.py` |
| M4 | `models/gbm.py::PoissonGBM` |
| M5 | `models/gbm.py::MarketAnchoredGBM` |
| M6 | `models/gbm.py::LogBlend` |
| media dei semi | `experiments/modelli.py::M5MediaSemi` |
| taratura | `evaluation/taratura.py` |

---

## 3. Come si confrontano, e perche' in produzione gira M1

Sul test set (2023/24, 2024/25, 2025/26 — 1140 partite, walk-forward per
giornata con riaddestramento da zero):

| modello | RPS | quanta strada copre |
|---|---|---|
| **M1b** market-only diretto | **0.1881** | riferimento |
| M1 market-only via lambda | 0.1882 | 99.9% |
| **M5** GBM ancorato al mercato | 0.1882 | 99.7% |
| M6 miscela | 0.1886 | 99.0% |
| M4 con mercato | 0.1905 | 94.1% |
| M4 senza mercato | 0.1944 | 84.6% |
| M0b frequenze di base | 0.2291 | 0% (il pavimento) |

La colonna di destra e' `skill_closed`: quanta parte della distanza fra il
pavimento (prevedere sempre le frequenze storiche) e il mercato viene coperta.
Comunica molto piu' del valore assoluto — 0.1944 non dice niente da solo,
"l'85% della strada verso il mercato" si'.

**Il fatto centrale**: M4-senza-mercato, che non vede una sola quota, copre
l'84.6% della strada. I dati di gioco da soli **ricostruiscono gran parte di
cio' che le quote sanno**. Ma e' la stessa informazione, prezzata peggio — e
infatti ancorare al mercato (M5) e poi cercare un residuo non trova niente:
+0.00011 con intervallo [-0.00056, +0.00077], indistinguibile.

**Quindi in produzione gira M1.** Non perche' il ML non funzioni, ma perche'
il ML **arriva dove e' gia' arrivato il mercato**, e il mercato lo si ha
gratis. Il criterio per cambiare idea e' scritto e non negoziabile: un modello
entra in `predict_round` solo se **batte il mercato** con intervallo che non
tocca lo zero, dopo correzione per confronti multipli. Finora nessuno lo fa.

Il modello ML continua a girare in parallelo, senza toccare il registro, con
`python -m goalmodel.experiments.predici_gbm`: serve a vedere **dove** M5 si
scosta. Misurato sulla giornata 4: scarto massimo sull'1X2 **0.013**, mediano
**0.005**. Ancorato al mercato e senza segnale nuovo, M5 resta incollato al
mercato — che e' la conferma pratica di cio' che il test set dice in forma
statistica.

### Come si decide che una differenza e' reale

Mai guardando due RPS medi. Il metro e' il **confronto appaiato**
(`evaluation/evaluate.py::paired_comparison`):

1. RPS di ogni singola partita, per ogni modello;
2. differenza riga per riga contro il riferimento — le stesse partite, quindi
   la difficolta' della giornata si cancella;
3. bootstrap con **cluster sulla giornata** (114 cluster, non 1140 partite: le
   dieci partite di una giornata sono predette dallo stesso addestramento),
   intervallo al 95%.

Il cluster non e' un dettaglio: ignorarlo restringe l'intervallo del **38%**.

**Una differenza conta solo se il suo intervallo non contiene lo zero.**

---

## 4. Riassunto in dieci righe

**M1** prende tre quote 1X2 e due quote over/under, toglie il margine del book
con il metodo di Shin, converte la probabilita' di over in un totale di gol
atteso, spezza quel totale in due usando la probabilita' di vittoria casalinga,
e da quei due numeri costruisce la matrice di tutti i risultati possibili. Non
impara niente: traduce.

**Il modello ML** prende cinquantadue medie mobili delle statistiche di gioco,
costruite in modo che nessuna riga possa vedere la propria partita, e addestra
due LightGBM Poisson — uno per i gol di casa, uno per quelli in trasferta. Nella
sua forma migliore (M5) non parte da zero: parte dai gol previsti dal mercato e
prova a correggerli.

Il secondo non riesce a correggere il primo. **E' questo, misurato con
intervalli, il risultato principale del progetto.**
