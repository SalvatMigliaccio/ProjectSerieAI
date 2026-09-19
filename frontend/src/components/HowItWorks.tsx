import type { Match } from "../api/types";
import { num, odds as fmtOdds, prob, score } from "../lib/format";
import { Eyebrow } from "./Layout";

/**
 * "Come funziona": quattro passi, dal dato grezzo alla schermata.
 *
 * I NUMERI DEI RIQUADRI SONO VERI. Le percentuali del passo 3 e la scheda del
 * passo 4 vengono dalla partita in cima all'ultima giornata registrata, non da
 * un mockup: una sezione che spiega come nascono le probabilita' mostrando
 * probabilita' inventate si smentisce da sola due schermate prima del track
 * record.
 *
 * DUE COSE DEL MOCKUP NON SONO STATE COPIATE, ED E' VOLUTO.
 *   "Scommessa consigliata" -> "Selezione piu' probabile" e quota equa. Il
 *     progetto mostra le selezioni e il loro prezzo a valore atteso zero, mai
 *     un consiglio di giocata: l'EV misurato su 1140 partite fuori campione e'
 *     -5.2% su ogni riga, e consigliarla implicherebbe un vantaggio che non c'e'.
 *   "Aggiornamenti in tempo reale" -> "Scritte prima del fischio". Qui le
 *     previsioni NON si aggiornano: il registro e' append-only e una riga non
 *     viene mai riscritta, perche' e' proprio questo a renderla verificabile.
 */

function Glifo({ nome }: { nome: "dati" | "analisi" | "probabilita" | "scenari" }) {
  const comune = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (nome === "dati") {
    return (
      <svg aria-hidden="true" {...comune}>
        <ellipse cx="12" cy="6" rx="8" ry="3" />
        <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
        <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
      </svg>
    );
  }
  if (nome === "analisi") {
    return (
      <svg aria-hidden="true" {...comune}>
        <rect x="7" y="7" width="10" height="10" rx="2" />
        <path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" />
      </svg>
    );
  }
  if (nome === "probabilita") {
    return (
      <svg aria-hidden="true" {...comune}>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="4.5" />
        <circle cx="12" cy="12" r="1" fill="currentColor" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" {...comune}>
      <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/** Da dove viene ogni cosa. Quattro voci con la fonte accanto dicono molto piu'
 *  di quattro pastiglie con una parola sola, e riempiono la scheda invece di
 *  lasciarla mezza vuota. */
const FONTI: Array<[string, string]> = [
  ["Quote di apertura", "B365, venerdì"],
  ["Copertura", "100% delle partite"],
  ["Risultati", "13 stagioni"],
  ["Forma", "medie mobili"],
  ["xG e PPDA", "Understat"],
];

/** Il margine del book e' gia' "somma meno uno": si mostra com'e', col segno. */
const margine = (valore: number | null): string =>
  valore === null ? "—" : `+${(valore * 100).toFixed(1)}%`;

/** Percentuale e barra: la stessa forma della griglia in dashboard. */
function Barra({
  etichetta,
  valore,
  tono,
}: {
  etichetta: string;
  valore: number | null;
  tono: string;
}) {
  return (
    <span className="mini__col">
      <span className="mini__key">{etichetta}</span>
      <span className="mini__val">{prob(valore)}</span>
      <span className="mini__bar">
        <i style={{ width: `${(valore ?? 0) * 100}%`, background: tono }} />
      </span>
    </span>
  );
}

const FATTI: Array<[string, string]> = [
  ["Analisi basata sui dati", "Non opinioni, ma numeri."],
  ["Più mercati, più possibilità", "1X2, doppia chance, Over/Under, Goal/No Goal."],
  ["Scritte prima del fischio", "Registrate prima del calcio d'inizio, mai riscritte dopo."],
  ["Un unico strumento", "Tutto quello che ti serve, in un'unica schermata."],
];

export function HowItWorks({ match }: { match: Match | null }) {
  const over = match?.p_over25 ?? null;
  const under = over === null ? null : 1 - over;
  const gg = match?.p_btts ?? null;
  const ng = gg === null ? null : 1 - gg;

  const esiti: Array<["H" | "D" | "A", string, number | null, string]> = [
    ["H", "1", match?.p_home ?? null, "var(--cat-1)"],
    ["D", "X", match?.p_draw ?? null, "var(--cat-x)"],
    ["A", "2", match?.p_away ?? null, "var(--cat-2)"],
  ];
  const migliore = esiti.reduce((a, b) => ((b[2] ?? 0) > (a[2] ?? 0) ? b : a));
  const giocata = match?.goals_home !== null && match?.goals_away !== null;

  return (
    <section className="howto" id="come-funziona">
      <div className="wrap">
        <div className="howto__head">
          <div>
            <Eyebrow>come funziona matchpoint</Eyebrow>
            <h2 className="howto__title">
              Dai dati alle <em>previsioni.</em>
            </h2>
            <p className="lead">
              MatchPoint parte dai dati disponibili su ogni partita di Serie A e
              ne ricava una lettura chiara delle probabilità e degli scenari più
              probabili.
            </p>
          </div>

          <p className="howto__note">
            <span className="howto__note-glyph" aria-hidden="true">
              <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round">
                <path d="M12 3l7 3v6c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6l7-3z" />
                <path d="M9.5 12.2l1.8 1.8 3.3-3.6" strokeLinecap="round" />
              </svg>
            </span>
            <span>
              <strong>MatchPoint non predice il futuro.</strong>
              <br />
              Analizza i dati per stimare gli scenari più probabili.
            </span>
          </p>
        </div>

        <ol className="howto__steps">
          <li className="step">
            <span className="step__head">
              <span className="step__n">01</span>
              <span className="step__glyph"><Glifo nome="dati" /></span>
            </span>
            <h3>Raccogliamo i dati</h3>
            <p>
              Le quote di apertura del venerdì, dodici stagioni di risultati e le
              statistiche avanzate di ogni partita: xG, PPDA, forma delle squadre.
            </p>
            <div className="step__demo">
              <div className="pipe">
                {FONTI.map(([voce, fonte]) => (
                  <span className="pipe__row" key={voce}>
                    <span className="pipe__key">{voce}</span>
                    <span className="pipe__val pipe__val--quieta">{fonte}</span>
                  </span>
                ))}
              </div>
            </div>
          </li>

          <li className="step">
            <span className="step__head">
              <span className="step__n">02</span>
              <span className="step__glyph"><Glifo nome="analisi" /></span>
            </span>
            <h3>Analizziamo la partita</h3>
            <p>
              Al prezzo del mercato togliamo il margine del book con il metodo di
              Shin, e ne ricaviamo i gol attesi delle due squadre.
            </p>
            {/* Il passaggio vero, con i numeri veri della stessa partita: il
                prezzo del book, il margine che contiene, e i gol attesi che
                restano quando lo si toglie. Tre etichette generiche e
                un'icona al centro non mostravano niente di tutto questo. */}
            <div className="step__demo">
              <div className="pipe">
                <span className="pipe__row">
                  <span className="pipe__key">Quote B365</span>
                  <span className="pipe__val">
                    {fmtOdds(match?.odds_home ?? null)} · {fmtOdds(match?.odds_draw ?? null)} ·{" "}
                    {fmtOdds(match?.odds_away ?? null)}
                  </span>
                </span>
                <span className="pipe__row">
                  <span className="pipe__key">Margine del book</span>
                  <span className="pipe__val pipe__val--warn">{margine(match?.overround ?? null)}</span>
                </span>
                <span className="pipe__step">
                  <span className="pipe__line" aria-hidden="true" />
                  de-vigging di Shin
                  <span className="pipe__line" aria-hidden="true" />
                </span>
                <span className="pipe__row pipe__row--out">
                  <span className="pipe__key">Gol attesi</span>
                  <span className="pipe__val pipe__val--mint">
                    {num(match?.lambda_home ?? null, 2)} – {num(match?.lambda_away ?? null, 2)}
                  </span>
                </span>
              </div>
            </div>
          </li>

          <li className="step">
            <span className="step__head">
              <span className="step__n">03</span>
              <span className="step__glyph"><Glifo nome="probabilita" /></span>
            </span>
            <h3>Calcoliamo le probabilità</h3>
            <p>
              Dai gol attesi nasce la matrice dei risultati esatti: 1X2,
              Over/Under e Goal/No Goal ne sono somme diverse, e per questo non
              possono contraddirsi.
            </p>
            <div className="step__demo">
              <div className="mini">
                <span className="mini__head">1 · X · 2</span>
                <span className="mini__cols">
                  {esiti.map(([code, etichetta, valore, tono]) => (
                    <Barra key={code} etichetta={etichetta} valore={valore} tono={tono} />
                  ))}
                </span>
              </div>
              {/* Affiancati: due pannelli da due colonne stanno in riga e la
                  scheda smette di essere il doppio delle altre. */}
              <div className="mini-coppia">
                <div className="mini">
                  <span className="mini__head">O/U 2.5</span>
                  <span className="mini__cols mini__cols--due">
                    <Barra etichetta="Over" valore={over} tono="var(--mint)" />
                    <Barra etichetta="Under" valore={under} tono="var(--cat-1)" />
                  </span>
                </div>
                <div className="mini">
                  <span className="mini__head">Goal/No Goal</span>
                  <span className="mini__cols mini__cols--due">
                    <Barra etichetta="GG" valore={gg} tono="var(--mint)" />
                    <Barra etichetta="NG" valore={ng} tono="var(--cat-2)" />
                  </span>
                </div>
              </div>
            </div>
          </li>

          <li className="step">
            <span className="step__head">
              <span className="step__n">04</span>
              <span className="step__glyph"><Glifo nome="scenari" /></span>
            </span>
            <h3>Ti mostriamo gli scenari</h3>
            <p>
              Tutto in un'unica schermata: probabilità, quote e risultato, con la
              selezione più probabile e il suo prezzo a valore atteso zero.
            </p>
            <div className="step__demo">
              <div className="scene">
                <span className="scene__teams">
                  {match ? (
                    <>
                      {match.home_team} <em>vs</em> {match.away_team}
                    </>
                  ) : (
                    "Serie A"
                  )}
                </span>

                <span className="mini__cols">
                  {esiti.map(([code, etichetta, valore, tono]) => (
                    <Barra key={code} etichetta={etichetta} valore={valore} tono={tono} />
                  ))}
                </span>

                <span className="scene__boxes">
                  <span className="scene__box scene__box--mint">
                    <span className="scene__box-key">Selezione più probabile</span>
                    <span className="scene__box-val">
                      {migliore[1]} · {prob(migliore[2])}
                    </span>
                  </span>
                  <span className="scene__box">
                    <span className="scene__box-key">Quota equa</span>
                    <span className="scene__box-val">
                      {fmtOdds(migliore[2] && migliore[2] > 0 ? 1 / migliore[2] : null)}
                    </span>
                  </span>
                </span>

                {/* Il testo promette anche il risultato: senza questa riga la
                    scheda mostrerebbe solo meta' di quello che dice. */}
                {giocata && (
                  <span className="scene__esito">
                    <span className="scene__box-key">Risultato</span>
                    <span
                      className={`score-chip${
                        match?.correct === true
                          ? " score-chip--ok"
                          : match?.correct === false
                            ? " score-chip--ko"
                            : ""
                      }`}
                    >
                      {score(match?.goals_home ?? null, match?.goals_away ?? null)}
                    </span>
                  </span>
                )}
              </div>
            </div>
          </li>
        </ol>

        <ul className="howto__facts">
          {FATTI.map(([titolo, corpo]) => (
            <li key={titolo}>
              <span className="howto__fact-glyph" aria-hidden="true">
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              </span>
              <span>
                <strong>{titolo}</strong>
                <span className="howto__fact-body">{corpo}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
