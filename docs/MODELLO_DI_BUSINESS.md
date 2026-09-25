# Modello di business

Fase 4 della roadmap. Stato: **bozza da discutere**, nessuna riga di codice
scritta per questo.

Questo documento dice cosa si vende, a chi, a che prezzo e con quali regole.
Non dice come si implementa: quello sta in `ARCHITETTURA_MULTIUTENTE.md` e nelle
fasi 2 e 3 di `ROADMAP.md`.

---

## 1. Il fatto che vincola tutto, e da cui conviene partire

**Il modello non produce valore atteso positivo, ed e' misurato, non temuto.**
Su 1140 partite fuori campione, **zero puntate su 3420** hanno EV positivo
contro le quote B365. Non e' sfortuna, e' aritmetica: M1 *e'* la linea di
apertura del bookmaker con il margine tolto, quindi l'EV calcolato sulle sue
probabilita' contro quelle stesse quote vale **-5.2% su ogni riga**.

Ne segue una cosa sola, ma pesante: **non possiamo vendere vincite.** Non come
scelta di prudenza — come descrizione di cio' che abbiamo. Un prodotto che
promettesse profitto mentirebbe sui propri numeri, e i numeri sono pubblicati
nel nostro stesso repository.

E ne segue anche il rovescio, che e' il posizionamento: **tutti gli altri
promettono profitto.** Vendere una misura onesta, con il track record in
chiaro anche quando e' brutto, e' l'unica cosa che ci distingue in un mercato
dove la promessa standard e' invalidabile. E' un mercato piu' piccolo. E' anche
l'unico in cui possiamo restare.

### Cosa abbiamo davvero da vendere

| cosa | perche' vale |
|---|---|
| **probabilita' calibrate** su ogni esito, non un pronostico | un pronostico dice "1"; una probabilita' dice *quanto* |
| **tutti i mercati derivati** dalla stessa distribuzione | 1X2, doppia chance, over/under, gol: coerenti fra loro per costruzione |
| **la quota equa** accanto a ogni selezione | il prezzo a valore atteso zero, da confrontare con quello del book |
| **un track record verificabile** | scritto prima del fischio, append-only, con l'errore per singola partita |
| **il tempo** | la previsione della giornata in arrivo, prima che si giochi |

L'ultimo e' l'unico bene **deperibile** che abbiamo, ed e' su quello che si
costruisce il paywall. Torna al punto 3.

---

## 2. Cosa siamo, in una riga

> Un modello probabilistico sui gol della Serie A, con il mercato come
> riferimento misurato, e un registro pubblico di tutto cio' che ha detto.

Non "pronostici vincenti". Non "il sistema che batte i bookmaker". Le due
parole che useremo sempre sono **probabilita'** e **track record**; quelle che
non useremo mai stanno al punto 6.

---

## 3. Il prodotto a livelli

### La proposta di partenza

Iscrizione, area personale, tutte le giornate passate consultabili; sulla
giornata corrente solo una parte delle selezioni; il resto dietro pagamento,
ad abbonamento o singolo.

L'impianto e' giusto: **il passato e' la prova, il presente e' il prodotto.**
Regalare lo storico non regala niente di vendibile e costruisce l'unica cosa
che ci fa scegliere — la fiducia. Va corretto un punto solo, ed e' il punto 3.2.

### 3.1 L'asse del taglio — dove passa la linea

| asse | gratis | a pagamento | giudizio |
|---|---|---|---|
| **tempo** | tutto lo storico; la giornata corrente **dopo** il fischio | la giornata corrente **prima** del fischio | **il migliore** |
| **profondita'** | 1X2 | tutti i mercati derivati + quota equa | **buono, si combina col primo** |
| **richiamo** | una partita in anteprima | le altre nove | buono come aggancio |
| **quota** | selezioni a quota bassa | selezioni a quota alta | **da non fare**, vedi sotto |

**Raccomandazione: tempo + profondita', con una partita di richiamo.**

- **Gratis**: tutte le giornate chiuse, con previsione, risultato ed errore;
  l'1X2 della giornata in arrivo **su una sola partita** (il Napoli, che e' il
  caso d'uso dichiarato del progetto, o il big match); tutto il resto della
  giornata corrente si apre **dopo il calcio d'inizio**, quando non serve piu' a
  scommettere ma serve ancora a valutarci.
- **A pagamento**: la giornata intera prima del fischio, tutti i mercati, le
  quote eque, il confronto con il book, e l'avviso quando la giornata e' pronta.

Perche' questo taglio regge: **e' onesto da spiegare in una frase.** "Paghi per
averlo prima, e per averlo completo." Non c'e' nessun segreto implicito, nessun
sottinteso di un segnale migliore riservato a chi paga. Ed e' facile da
imporre lato server, perche' l'orario di calcio d'inizio e' gia' nel dato e
gia' governa due difese del progetto.

### 3.2 Perche' NON tagliare per quota — la correzione

Tagliare a quota (gratis 1.20-1.30, a pagamento 1.50 e oltre) sembra naturale
e **e' rovesciato**, per due motivi misurati.

| soglia | vinte | quota equa media | giornate tutte vinte |
|---|---|---|---|
| 1.20 | **81%** | 1.26 | 16 su 114 |
| 1.30 | 74% | 1.36 | 3 su 114 |
| 1.40 | 68% | 1.48 | 2 su 114 |
| 1.50 | 62% | 1.62 | 0 su 114 |

1. **Il livello gratuito vincerebbe piu' spesso di quello a pagamento.** Chi
   paga vedrebbe il proprio tasso di successo **scendere** dall'81% al 62%, e
   con ogni giornata che passa si convincerebbe di aver pagato per un prodotto
   peggiore. Non e' un problema di comunicazione: e' esattamente cio' che
   succede.
2. **La promessa implicita e' quella che abbiamo gia' misurato essere falsa.**
   Mettere le quote alte dietro il paywall suggerisce che li' ci sia piu'
   valore. Non c'e': il margine del book e' **identico su tutti i mercati**
   derivati dalle stesse quote, cambia solo la varianza. La doppia chance piu'
   sicura vince l'80.6% delle volte e rende **-2.9%**, con intervallo che
   esclude lo zero.

La soglia di quota resta cio' che e' oggi — un parametro dichiarato del
modello (`config.QUOTA_MINIMA_SELEZIONE`, 1.30) — e **non diventa una leva di
prezzo**. Se una soglia scelta per vendere potesse ridefinire le selezioni, il
track record misurerebbe una cosa e il prodotto ne venderebbe un'altra.

---

## 4. Prezzi

Tutte ipotesi, da testare. L'unita' di misura e' la **giornata**, perche' e'
l'unita' del progetto ovunque: nel codice, nel registro, nel report.

| formula | prezzo ipotizzato | a chi parla |
|---|---|---|
| **sblocco singola giornata** | 1,99 € | chi prova, e chi gioca solo i big match |
| **abbonamento mensile** | 7,99 € (circa 4 giornate) | il caso normale |
| **abbonamento stagionale** | 49 € (38 giornate) | chi c'e' gia' stato una stagione |

Due regole sul prezzo, che valgono piu' dei numeri:

- **il prezzo non si lega mai ai risultati.** Niente "rimborso se la giornata
  va male", niente sconti dopo una serie negativa, niente livelli legati alle
  vincite. Legare il prezzo all'esito ci renderebbe di fatto un prodotto di
  gioco, e ci toglierebbe la sola cosa che possiamo dire con certezza: che
  vendiamo un'analisi, non un risultato;
- **il prezzo sta sotto la soglia del ragionamento.** A 8 € al mese nessuno fa
  il conto di quanto dovrebbe vincere per rientrare — ed e' bene, perche' quel
  conto **non torna** e noi lo sappiamo. A 50 € al mese quel conto lo fanno
  tutti, e il prodotto diventa indifendibile.

---

## 5. Perche' qualcuno dovrebbe pagare — e il rischio di churn

Va detto chiaro, perche' e' il punto debole del piano: **il nostro segnale e'
indistinguibile dal mercato.** Un cliente sveglio puo' guardare le quote di
apertura e ricavarsi da solo quasi tutto. Dopo tre o quattro giornate se ne
accorge.

Quindi il valore non e' il segnale, e' **tutto quello che ci sta intorno**:

- non deve leggere quote, togliere il margine e incrociare due mercati per
  ricavare i gol attesi: gliel'abbiamo gia' fatto;
- vede la stessa distribuzione **su tutti i mercati insieme**, coerente;
- ha uno storico che puo' interrogare — "cosa dicevate di Napoli-Inter?" —
  e che non e' riscrivibile;
- riceve la giornata pronta senza doversela ricordare.

E' un prodotto di **comodita' e trasparenza**, non di vantaggio. Chi cerca il
vantaggio se ne andra', ed e' giusto cosi': non ce l'abbiamo.

**Conseguenza sulle metriche**: la retention si misura **per giornata**, non
per mese. Il momento in cui si perde un cliente e' la seconda o terza giornata,
quando capisce cosa ha comprato. Se il tasso di abbandono si concentra li',
il problema e' la promessa fatta in pagina, non il prodotto.

---

## 6. Regole che non si violano

Sono vincoli di prodotto, non preferenze. Alcune sono gia' scritte in
`CLAUDE.md` e valgono identiche qui.

1. **Mai esporre valore atteso, puntata consigliata, stake, "value".** Su
   quote B365 l'EV e' -5.2% su ogni riga: un numero del genere in pagina
   sarebbe circolare e falso insieme.
2. **Mai un linguaggio che prometta l'esito.** Niente "sicura", "banker",
   "colpo". La probabilita' e' il messaggio.
3. **Nessun link affiliato ai bookmaker.** E' la regola che protegge tutto il
   resto: con un'affiliazione guadagneremmo quando l'utente perde, e da quel
   momento ogni nostra scelta di prodotto sarebbe sospetta — a ragione.
4. **Il track record resta pubblico anche quando e' brutto.** E' l'unico asset
   che abbiamo; nasconderlo una volta lo azzera per sempre.
5. **Le selezioni non si congelano per venderle.** Si ricalcolano dai due
   lambda del registro, sempre, anche sulle giornate chiuse.
6. **Nessuna previsione entra nel registro da una richiesta HTTP.** Vale oggi e
   vale con gli utenti: una previsione conta solo se l'ha scritta un processo
   locale prima del fischio.
7. **18+, e detto in pagina.** Con il rimando ai canali di aiuto per il gioco
   problematico, non in fondo in grigio chiaro.

---

## 7. Rischi — quelli veri, in ordine di quanto possono fermarci

### 7.1 Legale — da risolvere PRIMA del primo euro incassato

In Italia l'articolo 9 del D.L. 87/2018 (decreto Dignita') vieta la pubblicita'
di giochi e scommesse con vincite in denaro, e l'AGCOM e' intervenuta anche su
soggetti che diffondono pronostici sportivi. **Un servizio a pagamento di
previsioni calcistiche sta in una zona grigia**: non e' un operatore di gioco e
non raccoglie scommesse, ma promuovere il servizio puo' essere letto come
attivita' collegata.

**Non sono un legale e questo documento non e' un parere.** Serve un avvocato
prima di aprire i pagamenti, e le tre domande da portargli sono precise:

1. un servizio in abbonamento di analisi probabilistiche, senza raccolta di
   scommesse e senza link a operatori, rientra nel divieto?
2. cambia qualcosa se il prodotto non nomina mai i bookmaker e non mostra le
   loro quote, ma solo le proprie probabilita' e la quota equa?
3. quali obblighi informativi servono in pagina (18+, avvertenze, termini)?

Il costo di sbagliare non e' teorico: e' una sanzione e la chiusura.

### 7.2 Pagamenti

Stripe e gli altri processori tengono liste di attivita' vietate o ristrette in
cui i servizi legati a scommesse e "betting tips" possono ricadere. **Un
account chiuso a stagione iniziata, con abbonamenti attivi, e' un incidente da
prevenire, non da scoprire.** Va chiesto in anticipo e per iscritto, descrivendo
il servizio per quello che e'.

### 7.3 Prodotto

Vedi il punto 5: il segnale e' il mercato. Se il prodotto intorno non e'
migliore di un foglio di calcolo, non c'e' business.

### 7.4 Reputazione

Il track record pubblico puo' andare male, e a volte **andra'** male. Sulle tre
giornate chiuse finora l'RPS cumulativo e' **0.2083 su 25 previsioni**, contro
lo **0.1881** del backtest. Sembra peggio, e non lo e': sulle stesse identiche
partite il mercato ha fatto **0.2057**. Erano giornate difficili per tutti — e
questo e' esattamente il tipo di spiegazione che dovremo saper dare in pagina,
con i numeri accanto, invece di sperare che nessuno guardi.

La calibrazione, che e' la misura piu' convincente che abbiamo, **non e' ancora
disponibile**: ne servono almeno 50 di previsioni risolte e siamo a 25. Prima di
vendere conviene averla.

---

## 8. Come si misura se funziona

| indicatore | dove si guarda | soglia di allarme |
|---|---|---|
| conversione gratuito -> pagante | per coorte di iscrizione | — da stabilire dopo il primo mese |
| **retention per giornata** | sblocchi consecutivi, non mesi | caduta concentrata alla 2ª-3ª giornata |
| abbandoni dopo una giornata negativa | churn condizionato all'RPS della giornata | se e' forte, la promessa in pagina e' sbagliata |
| **coerenza fra promessa e consegna** | curva di calibrazione | se le partite date al 70% ne vincono il 50%, si ferma tutto |

L'ultimo indicatore non e' commerciale ed e' il piu' importante: e' l'unico che
dice se il prodotto **e'** quello che abbiamo venduto.

---

## 9. Cosa significa per il codice

Niente di nuovo da inventare: la fase 2 ha gia' messo i pezzi.

- **i permessi esistono gia'**: `data:read` e `picks:read` sono separati apposta,
  e i ruoli sono **righe e non un enum** proprio perche' un livello di
  abbonamento sia un `INSERT` e non una migrazione;
- **il paywall si impone nel server, mai nascondendo in interfaccia.** Chiunque
  puo' modificare il JavaScript; nessuno puo' modificare la risposta dell'API;
- **un livello e' un filtro sulla risposta, non una tabella.** Le selezioni si
  ricalcolano dai lambda, quindi cambiare cosa vede un livello non tocca il
  dato;
- **attenzione alla cache, ed e' un bug che si crea da solo.** Oggi le risposte
  escono con `Cache-Control: public` e un ETag che non conosce l'utente: nel
  momento in cui una rotta diventa a pagamento, un proxy condiviso servirebbe il
  contenuto pagato a chi non ha pagato. Le rotte protette devono passare a
  `private, no-store`, e l'ETag deve entrare in gioco per livello.

---

## 10. Domande aperte — da rispondere prima di scrivere codice

1. **Chi e' il cliente?** Lo scommettitore che cerca un vantaggio (e non lo
   trovera') o l'appassionato di dati che vuole numeri onesti? Sono due
   prodotti, due pagine e due prezzi diversi. La mia lettura e' che possiamo
   servire onestamente solo il secondo.
2. **Solo Serie A o i Big 5?** Il modello e' addestrato sui Big 5 ma la
   produzione e' Serie A, e il caso d'uso dichiarato e' il Napoli. Allargare
   moltiplica il contenuto senza cambiare il codice, ma va deciso prima dei
   prezzi.
3. **Quanto storico si regala?** Tutto (tre giornate oggi, una stagione fra un
   anno) o l'ultimo mese? Io regalerei tutto: e' la prova, non il prodotto.
4. **La verifica legale del punto 7.1**, che viene prima di tutte le altre.
