# Modello di business

Fase 4 della roadmap. Stato: **bozza da discutere**, nessuna riga di codice
scritta per questo.

Questo documento dice cosa si vende, a chi, a che prezzo e con quali regole.
Non dice come si implementa: quello sta in `ARCHITETTURA_MULTIUTENTE.md` e nelle
fasi 2 e 3 di `ROADMAP.md`.

---

## 1. Cosa vendiamo

**Le probabilita' di M1 accanto alle quote del bookmaker**, su ogni partita
della Serie A, con lo storico completo di tutto cio' che il modello ha detto
prima che si giocasse.

Il cliente vede, su una riga sola:

| partita | esito | probabilita' | quota equa | quota book |
|---|---|---|---|---|
| Napoli – Bologna | 1 | 62% | 1.61 | 1.70 |

Tre numeri che oggi stanno in tre posti diversi e che nessuno mette insieme al
posto suo: quanto e' probabile secondo il modello, quanto varrebbe a valore
atteso zero, quanto lo paga il book. **Il prodotto e' quell'accostamento**, piu'
il registro che lo conserva.

Non vendiamo un pronostico e non vendiamo un sistema: vendiamo una
**distribuzione di probabilita'** — da cui ogni mercato si ricava coerente —
e il **prezzo di mercato** accanto.

### Cosa c'e' gia' e cosa no

Questo non e' da costruire: `GET /api/picks` restituisce gia' `probability`,
`fair_odds` e `book_odds` per ogni selezione. Due limiti del dato vanno pero'
conosciuti **prima** di disegnare le pagine, perche' non si aggirano:

- **la quota del book esiste solo per l'1X2.** Per doppia chance, over/under e
  mercati gol il registro la lascia vuota, e resta vuota anche in pagina.
  Stimarla applicando un margine medio significherebbe inventare un numero con
  l'aria di essere misurato. Su quei mercati mostriamo probabilita' e quota
  equa, e basta;
- **e' la quota di APERTURA di un book solo** (B365), fotografata il venerdi'
  entro le 17:00 UK. Non e' la quota che il cliente trovera' al momento di
  giocare, e va detto in pagina, non nelle condizioni d'uso. Il drift fra
  apertura e chiusura ha una deviazione standard di ~3 punti di probabilita':
  abbastanza perche' qualcuno se ne accorga e si senta preso in giro se non
  l'abbiamo scritto.

Il secondo limite e' anche una scelta metodologica, non una pigrizia: quella
fotografia e' raccolta **con lo stesso criterio** con cui sono state raccolte le
quote su cui il modello e' stato addestrato e misurato. Cambiarla romperebbe la
corrispondenza fra il backtest e la produzione senza che niente protesti.

---

## 2. Cosa siamo, in una riga

> Le probabilita' di un modello sui gol della Serie A, accanto alle quote di
> mercato, con un registro pubblico di tutto cio' che ha detto prima del fischio.

Le parole che useremo sempre sono **probabilita'**, **quota** e **track
record**; quelle che non useremo mai stanno al punto 7.

---

## 3. Il prodotto a livelli

### La proposta di partenza

Iscrizione, area personale, tutte le giornate passate consultabili; sulla
giornata corrente solo una parte delle selezioni; il resto dietro pagamento,
ad abbonamento o singolo.

L'impianto e' giusto: **il passato e' la prova, il presente e' il prodotto.**
Regalare lo storico non regala niente di vendibile e costruisce l'unica cosa
che ci fa scegliere — la fiducia. Va corretto un punto solo, il 3.2.

### 3.1 L'asse del taglio — dove passa la linea

| asse | gratis | a pagamento | giudizio |
|---|---|---|---|
| **tempo** | tutto lo storico; la giornata corrente **dopo** il fischio | la giornata corrente **prima** del fischio | **il migliore** |
| **profondita'** | 1X2 | tutti i mercati derivati + quota equa | **buono, si combina col primo** |
| **richiamo** | una partita in anteprima | le altre nove | buono come aggancio |
| **quota** | selezioni a quota bassa | selezioni a quota alta | **da non fare**, vedi 3.2 |

**Raccomandazione: tempo + profondita', con una partita di richiamo.**

- **Gratis**: tutte le giornate chiuse, con previsione, risultato ed errore;
  l'1X2 della giornata in arrivo **su una sola partita** (il Napoli, che e' il
  caso d'uso dichiarato del progetto, o il big match); il resto della giornata
  corrente si apre **dopo il calcio d'inizio**, quando non serve piu' per
  giocare ma serve ancora per valutarci.
- **A pagamento**: la giornata intera prima del fischio, tutti i mercati, le
  quote eque, la quota del book dove esiste, e l'avviso quando la giornata e'
  pronta.

Perche' questo taglio regge: **e' onesto da spiegare in una frase** — "paghi per
averlo prima, e per averlo completo". Nessun segreto implicito, nessun
sottinteso di un segnale migliore riservato a chi paga. Ed e' facile da imporre
lato server, perche' l'orario di calcio d'inizio e' gia' nel dato e governa gia'
due difese del progetto.

### 3.2 Perche' NON tagliare per quota — la correzione

Tagliare a quota (gratis 1.20-1.30, a pagamento 1.50 e oltre) sembra naturale
ed e' **rovesciato**, per due motivi gia' misurati sul test set.

| soglia | vinte | quota equa media | giornate tutte vinte |
|---|---|---|---|
| 1.20 | **81%** | 1.26 | 16 su 114 |
| 1.30 | 74% | 1.36 | 3 su 114 |
| 1.40 | 68% | 1.48 | 2 su 114 |
| 1.50 | 62% | 1.62 | 0 su 114 |

1. **Il livello gratuito vincerebbe piu' spesso di quello a pagamento.** Chi
   paga vedrebbe il proprio tasso di successo **scendere** dall'81% al 62%, e a
   ogni giornata si convincerebbe di aver comprato il prodotto peggiore. Non e'
   un problema di comunicazione: e' quello che succede.
2. **Suggerirebbe che nelle quote alte ci sia piu' valore.** Non ce n'e': il
   margine del book e' **identico su tutti i mercati** derivati dalle stesse
   quote, cambia solo la varianza.

La soglia di quota resta cio' che e' oggi — un parametro dichiarato del modello
(`config.QUOTA_MINIMA_SELEZIONE`, 1.30) — e **non diventa una leva di prezzo**.
Se una soglia scelta per vendere potesse ridefinire le selezioni, il track
record misurerebbe una cosa e il prodotto ne venderebbe un'altra.

---

## 4. Prezzi

Tutte ipotesi, da testare. L'unita' di misura e' la **giornata**, perche' e'
l'unita' del progetto ovunque: nel codice, nel registro, nel report.

| formula | prezzo ipotizzato | a chi parla |
|---|---|---|
| **sblocco singola giornata** | 1,99 € | chi prova, e chi guarda solo i big match |
| **abbonamento mensile** | 7,99 € (circa 4 giornate) | il caso normale |
| **abbonamento stagionale** | 49 € (38 giornate) | chi c'e' gia' stato una stagione |

Una regola sul prezzo che vale piu' dei numeri: **il prezzo non si lega mai ai
risultati.** Niente rimborsi se la giornata va male, niente sconti dopo una
serie negativa, niente livelli legati alle vincite. Legare il prezzo all'esito
ci renderebbe di fatto un prodotto di gioco — con tutto cio' che ne segue al
punto 8 — invece di un servizio di analisi.

---

## 5. Perche' qualcuno dovrebbe pagare

Va detto chiaro, perche' e' il punto debole del piano: **le probabilita' di M1
sono le quote del book con il margine tolto.** Un cliente sveglio puo' guardare
le quote di apertura e ricavarsi da solo quasi tutto. Dopo tre o quattro
giornate se ne accorge.

Il valore quindi non e' nel segnale, e' nel **lavoro che gli togliamo** e nel
**contesto che gli diamo**:

- non deve prendere tre quote, toglierne il margine con Shin e incrociare
  supremazia e totale per ricavare i gol attesi: gliel'abbiamo gia' fatto, con
  lo stesso codice che ha prodotto il backtest;
- vede la stessa distribuzione **su tutti i mercati insieme**, coerenti fra
  loro per costruzione — 1X2, doppia chance, over/under, gol;
- vede **quota equa e quota del book affiancate**, che e' il confronto che tutti
  vorrebbero fare e quasi nessuno fa;
- ha uno storico interrogabile — "cosa dicevate di Napoli-Inter?" — che non e'
  riscrivibile a posteriori;
- riceve la giornata pronta senza doversela ricordare.

E' un prodotto di **comodita', coerenza e trasparenza**. Chi cerca un vantaggio
sul book se ne andra', ed e' giusto: quel vantaggio non glielo diamo e non
glielo promettiamo.

**Conseguenza sulle metriche**: la retention si misura **per giornata**, non per
mese. Si perde un cliente alla seconda o terza giornata, quando capisce cosa ha
comprato. Se gli abbandoni si concentrano li', il problema e' la promessa fatta
in pagina, non il prodotto.

---

## 6. Quello che non promettiamo — e perche' e' scritto qui

Non promettiamo profitto, e non pubblichiamo valore atteso, puntata consigliata
o "value". Non e' una posizione morale, e' che quei numeri **li conosciamo**: su
1140 partite fuori campione, contro le quote B365, l'EV e' -5.2% su ogni riga e
le giocate a EV positivo sono **zero su 3420**. M1 *e'* la linea di apertura del
book senza margine, quindi un EV calcolato sulle sue probabilita' contro quelle
stesse quote e' circolare per costruzione.

**Questo non impedisce di mostrare le quote del book: le mostriamo.** Sono un
dato descrittivo e sono meta' del prodotto. Quello che non facciamo e' la
sottrazione al posto del cliente e chiamarla opportunita'.

E' anche il posizionamento: tutti gli altri promettono profitto con una promessa
che nessuno puo' verificare. Noi pubblichiamo un registro che chiunque puo'
controllare. Mercato piu' piccolo, ma e' l'unico in cui possiamo restare senza
dire cose false.

---

## 7. Regole che non si violano

Vincoli di prodotto, non preferenze. Alcune sono gia' in `CLAUDE.md` e valgono
identiche qui.

1. **Mai valore atteso, puntata consigliata, stake, "value".** Vedi il punto 6.
2. **Mai un linguaggio che prometta l'esito.** Niente "sicura", "banker",
   "colpo". La probabilita' e' il messaggio.
3. **Nessun link affiliato ai bookmaker.** E' la regola che protegge tutte le
   altre: con un'affiliazione guadagneremmo quando l'utente perde, e da quel
   momento ogni scelta di prodotto sarebbe sospetta — a ragione. Mostrare una
   quota e' informare; mandare traffico a chi la offre e' un'altra attivita'.
4. **Il track record resta pubblico anche quando e' brutto.** E' l'unico asset
   che abbiamo; nasconderlo una volta lo azzera per sempre.
5. **Le selezioni non si congelano per venderle.** Si ricalcolano dai due lambda
   del registro, sempre, anche sulle giornate chiuse.
6. **Nessuna previsione entra nel registro da una richiesta HTTP.** Vale oggi e
   vale con gli utenti: una previsione conta solo se l'ha scritta un processo
   locale prima del fischio.
7. **18+ detto in pagina**, con il rimando ai canali di aiuto per il gioco
   problematico.

---

## 8. Rischi — in ordine di quanto possono fermarci

### 8.1 Legale — da risolvere PRIMA del primo euro incassato

In Italia l'articolo 9 del D.L. 87/2018 (decreto Dignita') vieta la pubblicita'
di giochi e scommesse con vincite in denaro, e l'AGCOM e' intervenuta anche su
soggetti che diffondono pronostici sportivi. **Un servizio a pagamento di
previsioni calcistiche sta in zona grigia**: non e' un operatore di gioco e non
raccoglie scommesse, ma promuoverlo puo' essere letto come attivita' collegata.

**Non sono un legale e questo documento non e' un parere.** Serve un avvocato
prima di aprire i pagamenti, e le domande da portargli sono precise:

1. un servizio in abbonamento di analisi probabilistiche, senza raccolta di
   scommesse e senza link a operatori, rientra nel divieto?
2. **mostrare le quote di un bookmaker nominandolo cambia la risposta?** E'
   la domanda piu' importante delle quattro, perche' tocca meta' del prodotto.
   Se la risposta fosse si', la via d'uscita esiste ed e' tecnica: mostrare la
   quota **senza nominare il book**, o mostrare solo la quota equa;
3. quali obblighi informativi servono in pagina (18+, avvertenze, termini);
4. serve una partita IVA e quale inquadramento.

Il costo di sbagliare non e' teorico: sanzione e chiusura.

### 8.2 Licenza dei dati — il rischio nuovo, e nasce proprio dalle quote

Le quote arrivano da **football-data.co.uk**, gratuito e generoso, ma con
condizioni d'uso proprie. Finche' il progetto e' personale la questione non si
pone; **rivenderle dentro un prodotto a pagamento e' ridistribuzione
commerciale**, ed e' un'altra cosa.

Lo dice gia' la nostra licenza, in tre punti diversi: l'AGPL copre il **codice**,
non i **dati** — risultati, quote, xG e calendari arrivano da terzi con i loro
termini, che non possiamo estendere. Da verificare, nell'ordine:

- i termini di football-data.co.uk per l'uso commerciale, e se basta scrivere
  a chi lo gestisce (spesso basta);
- gli stessi per Understat e FBref, che alimentano le feature ma **non** finiscono
  in pagina: il rischio e' minore ma esiste;
- se serve, un fornitore di quote con licenza commerciale esplicita — e li' il
  vincolo metodologico del punto 1 diventa un costo, perche' cambiare fonte
  significa rifare backtest e riferimenti.

### 8.3 Pagamenti

Stripe e gli altri processori tengono liste di attivita' vietate o ristrette in
cui i servizi legati a scommesse e "betting tips" possono ricadere. **Un account
chiuso a stagione iniziata, con abbonamenti attivi, e' un incidente da prevenire,
non da scoprire.** Va chiesto in anticipo e per iscritto, descrivendo il servizio
per quello che e'.

### 8.4 Prodotto

Vedi il punto 5: il segnale e' il mercato. Se il prodotto intorno non e' migliore
di un foglio di calcolo, non c'e' business.

### 8.5 Reputazione

Il track record pubblico puo' andare male, e a volte **andra'** male. Sulle tre
giornate chiuse l'RPS cumulativo e' **0.2083 su 25 previsioni**, contro lo
**0.1881** del backtest. Sembra peggio e non lo e': sulle stesse identiche
partite il **mercato** ha fatto **0.2057**. Erano giornate difficili per tutti —
ed e' esattamente il tipo di spiegazione che dovremo saper dare in pagina, con i
numeri accanto, invece di sperare che nessuno guardi.

La calibrazione, che e' la misura piu' convincente che abbiamo, **non e' ancora
disponibile**: ne servono almeno 50 previsioni risolte e siamo a 25. Prima di
vendere conviene averla.

---

## 9. Come si misura se funziona

| indicatore | dove si guarda | soglia di allarme |
|---|---|---|
| conversione gratuito -> pagante | per coorte di iscrizione | da stabilire dopo il primo mese |
| **retention per giornata** | sblocchi consecutivi, non mesi | caduta concentrata alla 2ª-3ª giornata |
| abbandoni dopo una giornata negativa | churn condizionato all'RPS della giornata | se e' forte, la promessa in pagina e' sbagliata |
| **coerenza fra promessa e consegna** | curva di calibrazione | se le partite date al 70% ne vincono il 50%, si ferma tutto |

L'ultimo non e' un indicatore commerciale ed e' il piu' importante: e' l'unico
che dice se il prodotto **e'** quello che abbiamo venduto.

---

## 10. Cosa significa per il codice

Niente di nuovo da inventare: la fase 2 ha gia' messo i pezzi.

- **le quote sono gia' nella risposta**: `Selection` porta `probability`,
  `fair_odds` e `book_odds`, con `book_odds` a `null` fuori dall'1X2 — cioe'
  il limite del punto 1 e' gia' rappresentato nel contratto, non va aggiunto;
- **i permessi esistono gia'**: `data:read` e `picks:read` sono separati apposta,
  e i ruoli sono **righe e non un enum** proprio perche' un livello di
  abbonamento sia un `INSERT` e non una migrazione;
- **il paywall si impone nel server, mai nascondendo in interfaccia.** Chiunque
  puo' modificare il JavaScript; nessuno puo' modificare la risposta dell'API;
- **un livello e' un filtro sulla risposta, non una tabella.** Le selezioni si
  ricalcolano dai lambda, quindi cambiare cosa vede un livello non tocca il dato;
- **attenzione alla cache, ed e' un bug che si crea da solo.** Oggi le risposte
  escono con `Cache-Control: public` e un ETag che non conosce l'utente: nel
  momento in cui una rotta diventa a pagamento, un proxy condiviso servirebbe il
  contenuto pagato a chi non ha pagato. Le rotte protette devono passare a
  `private, no-store`, e l'ETag deve entrare in gioco per livello.

---

## 11. Domande aperte — da rispondere prima di scrivere codice

1. **La verifica legale del punto 8.1 e la licenza dei dati del punto 8.2**,
   che vengono prima di tutto il resto.
2. **Chi e' il cliente?** Lo scommettitore che cerca un vantaggio (e non lo
   trovera') o l'appassionato di dati che vuole numeri onesti? Sono due
   prodotti, due pagine e due prezzi diversi.
3. **Solo Serie A o i Big 5?** Il modello e' addestrato sui Big 5 ma la
   produzione e' Serie A, e il caso d'uso dichiarato e' il Napoli. Allargare
   moltiplica il contenuto senza cambiare il codice, ma va deciso prima dei
   prezzi.
4. **Quanto storico si regala?** Tutto (tre giornate oggi, una stagione fra un
   anno) o l'ultimo mese? Io regalerei tutto: e' la prova, non il prodotto.
