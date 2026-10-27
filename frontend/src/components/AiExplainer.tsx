/**
 * Come funziona il modello AI, in parole: cosa fa, cosa usa, cosa dicono i test.
 *
 * SOLO COSE MISURATE. Ogni frase qui sotto viene da CLAUDE.md, dove ci sono i
 * numeri e il metodo: cosa e' M5, su quali partite e' stato provato, e il
 * risultato — indistinguibile dal mercato. Una sezione che spiega un modello
 * "AI" e' esattamente il posto dove e' facile promettere di piu' di quello che
 * il modello fa; per questo non dice niente che non sia stato verificato.
 */

export function AiExplainer() {
  return (
    <section className="aiex" aria-labelledby="aiex-titolo">
      <h2 id="aiex-titolo" className="aitr__title">Come funziona il modello AI</h2>
      <div className="aiex__grid">
        <div className="aiex__item">
          <h3>Parte dal mercato</h3>
          <p>
            È un modello LightGBM che prevede i gol di ciascuna squadra. Non riparte da zero:
            prende come punto di partenza i gol attesi ricavati dalle quote di apertura, e
            impara solo la <strong>correzione</strong> da applicare. Senza niente da correggere,
            resta identico al mercato.
          </p>
        </div>
        <div className="aiex__item">
          <h3>Cosa guarda</h3>
          <p>
            La forma recente delle due squadre: gol attesi (xG), tiri, pressing (PPDA), azioni
            pericolose, punti attesi. Le medie scorrono senza azzerarsi a fine stagione. Viene
            riaddestrato da zero a ogni giornata, e la previsione è la media di cinque
            addestramenti.
          </p>
        </div>
        <div className="aiex__item">
          <h3>Cosa dicono i test</h3>
          <p>
            Su 1140 partite di tre stagioni, mai viste durante l'addestramento, il suo errore è{" "}
            <strong>indistinguibile da quello del mercato</strong>: +0,0001 di RPS, con un
            intervallo che contiene lo zero. In pratica resta molto vicino alle quote: in una
            giornata tipo si scosta di mezzo punto percentuale sull'1X2.
          </p>
        </div>
        <div className="aiex__item">
          <h3>Perché è una seconda opinione</h3>
          <p>
            Il track record ufficiale è quello del modello classico, le quote senza il margine
            del book. L'AI resta accanto, misurata nello stesso modo: diventerebbe il modello
            principale solo se battesse il mercato con un margine statisticamente netto.
          </p>
        </div>
      </div>
    </section>
  );
}
