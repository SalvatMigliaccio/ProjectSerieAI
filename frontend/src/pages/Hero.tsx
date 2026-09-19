import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Match, MatchPoint, Round, StandingRow } from "../api/types";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { HowItWorks } from "../components/HowItWorks";
import { Colophon, Eyebrow, Masthead } from "../components/Layout";
import { StandingsPanel } from "../components/StandingsPanel";
import { ErrorState, Skeleton } from "../components/States";
import { useApi } from "../hooks/useApi";
import { localTime, num, prob, ratio } from "../lib/format";

/**
 * La landing.
 *
 * QUELLO CHE NON PROMETTE, E PERCHE'. Niente "intelligenza artificiale" ne'
 * "previsioni accurate": il modello in produzione e' la quota di apertura del
 * book con il margine tolto, e il GBM e' risultato indistinguibile dal mercato
 * su 1140 partite. Il track record e' pubblicato due schermate piu' in la' e
 * smentirebbe lo slogan da solo.
 *
 * TUTTO QUELLO CHE SI VEDE E' UN DATO VERO. Il tablet mostra la giornata
 * realmente in registro, le schede flottanti la serie dell'RPS e la classifica
 * calcolata dai risultati. Un finto screenshot sarebbe stato piu' facile e non
 * avrebbe dimostrato niente.
 */

function lastWithPredictions(rounds: Round[] | null): Round | null {
  if (!rounds) return null;
  return rounds.filter((round) => (round.matches_predicted ?? 0) > 0).at(-1) ?? null;
}

function OddsRow({ match, compact = false }: { match: Match; compact?: boolean }) {
  const cells: Array<["H" | "D" | "A", string, number | null]> = [
    ["H", "1", match.p_home],
    ["D", "X", match.p_draw],
    ["A", "2", match.p_away],
  ];
  return (
    <div className="odds-row">
      {cells.map(([code, label, value]) => (
        <div
          key={code}
          className={`odds-cell${code === match.predicted_outcome ? " odds-cell--best" : ""}`}
          style={compact ? { padding: "0.5rem 0.25rem" } : undefined}
        >
          <span className="odds-cell__key">{label}</span>
          <span className="odds-cell__value">{prob(value)}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * RPS cumulativo in piccolo, con la tratteggiata del backtest.
 *
 * LA TRATTEGGIATA E' IL MOTIVO PER CUI IL GRAFICO SERVE. Una curva dell'errore
 * senza riferimento non dice niente: sale, scende, ma rispetto a cosa? Con la
 * linea del mercato sul test set (0.1881) si vede subito se il track record ci
 * sta sopra o sotto. La scala include il riferimento, altrimenti a volte
 * finirebbe fuori dal riquadro proprio quando e' piu' utile.
 */
function Spark({ points, reference }: { points: MatchPoint[]; reference: number }) {
  const values = points
    .map((point) => point.cumulative_rps)
    .filter((value): value is number => value !== null);
  if (values.length < 2) return null;

  const lo = Math.min(...values, reference);
  const hi = Math.max(...values, reference);
  const span = hi - lo || 1;
  const x = (i: number) => (i * 170) / (values.length - 1);
  const y = (v: number) => 44 - ((v - lo) / span) * 36 - 4;

  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `0,44 ${line} 170,44`;
  const ultimo = values[values.length - 1] ?? 0;

  return (
    <svg
      className="floater__spark"
      viewBox="0 0 170 48"
      role="img"
      aria-label={`RPS cumulativo ${ultimo.toFixed(4)} contro il riferimento del backtest ${reference.toFixed(4)}`}
    >
      <polygon className="chart__area" points={area} />
      <line className="chart__ref" x1={0} x2={170} y1={y(reference)} y2={y(reference)} />
      <polyline className="chart__line" points={line} />
      <circle className="chart__last" cx={x(values.length - 1)} cy={y(ultimo)} r={2.6} />
    </svg>
  );
}

const LETTERA: Record<string, string> = { W: "V", D: "N", L: "P" };
const PAROLA: Record<string, string> = { W: "vittoria", D: "pareggio", L: "sconfitta" };

function Floaters({
  points,
  table,
  reference,
}: {
  points: MatchPoint[];
  table: StandingRow[];
  reference: number;
}) {
  const top = table.slice(0, 3);
  const most = Math.max(1, ...top.map((row) => row.goals_for));
  const ultimo = [...points].reverse().find((p) => p.cumulative_rps !== null)?.cumulative_rps ?? null;

  return (
    <div className="floaters">
      {/* Si chiamava "Analisi avanzata": un titolo che non diceva di che
          grafico si trattasse, su una curva senza numero e senza
          riferimento. Ora ha il nome della metrica, il valore attuale, il
          verso di lettura e la linea contro cui leggerla. */}
      <div className="floater">
        <div className="floater__head">
          <p className="floater__title">RPS cumulativo</p>
          {ultimo !== null && <span className="floater__value">{ultimo.toFixed(4)}</span>}
        </div>
        <span className="floater__sub">più basso è meglio · tratteggio: backtest {reference.toFixed(4)}</span>
        <Spark points={points} reference={reference} />
      </div>

      {/* IL NUMERO ACCANTO ALLA BARRA E' GOL FATTI, E ORA LO DICE. Con il
          titolo generico "Statistiche squadre" sembrava la classifica, e
          l'Inter con 13 accanto contraddiceva i 12 punti che si leggono due
          schermate piu' giu'. Una barra senza unita' di misura non e' un
          grafico, e' un indovinello. */}
      <div className="floater">
        <p className="floater__title">Gol fatti</p>
        <span className="floater__sub">prime tre in classifica</span>
        <div className="floater__row">
          <span className="floater__glyph" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 20V10M12 20V4M18 20v-7" strokeLinecap="round" />
            </svg>
          </span>
          <div className="floater__bars" style={{ flex: 1 }}>
            {top.map((row) => (
              <div className="floater__bar" key={row.team}>
                <span>{row.team.slice(0, 8)}</span>
                <span className="floater__track">
                  <i
                    className="floater__fill"
                    style={{ width: `${(row.goals_for / most) * 100}%` }}
                  />
                </span>
                <span>{row.goals_for}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Trend e forma: la SEQUENZA dei risultati, non i totali della
          classifica. Quattro vittorie non dicono se la squadra sta salendo.

          `form ?? []` non e' pignoleria: il tipo TypeScript garantisce il campo
          a compile-time, ma l'API e' un confine di rete e puo' rispondere un
          processo piu' vecchio del frontend. Senza la guardia, un campo assente
          fa esplodere il componente e con lui tutta la pagina. */}
      <div className="floater">
        <p className="floater__title">Forma recente</p>
        <span className="floater__sub">ultime partite, dalla più vecchia</span>
        {top.slice(0, 2).map((row) => (
          <div className="floater__form" key={row.team}>
            <span className="floater__team">{row.team}</span>
            {/* La lettera dentro il pallino: con il solo colore, verde e
                corallo sono la stessa cosa per chi non distingue il rosso dal
                verde — circa un uomo su dodici. */}
            {(row.form ?? []).map((result, index) => (
              <span
                key={`${row.team}-${index}`}
                className={`floater__result floater__result--${result.toLowerCase()}`}
                title={`${row.team}: ${PAROLA[result] ?? result}`}
              >
                {LETTERA[result] ?? result}
              </span>
            ))}
          </div>
        ))}
      </div>

      <p className="signature">
        Più dati.
        <br />
        Più insight.
        <em>Più MatchPoint.</em>
      </p>
    </div>
  );
}

export function Hero() {
  const season = useApi(() => api.season(), []);
  const rounds = useApi(() => api.rounds(), []);
  const standings = useApi(() => api.standings(), []);
  const track = useApi(() => api.trackRecord(), []);

  const round = lastWithPredictions(rounds.data);
  const matches = useApi<Match[] | null>(
    () => (round ? api.roundMatches(round.matchday) : Promise.resolve(null)),
    [round?.matchday],
  );

  const picks = useApi(() => api.picks(), []);

  const list = matches.data ?? [];
  const head = list[0] ?? null;
  const cards = list.slice(0, 3);

  // La selezione del modello per ogni partita in vetrina. E' `picks`, non la
  // banda regolabile della dashboard: qui non c'e' nessuna soglia da scegliere,
  // e mostrare una lettura secondaria come se fosse la linea del progetto
  // sarebbe il modo piu' rapido di far misurare al track record una cosa e
  // raccontarne un'altra.
  const selezioni = new Map(
    (picks.data?.with_min_odds ?? []).map((s) => [`${s.home_team}-${s.away_team}`, s]),
  );

  // Il titolo segue lo stato della giornata mostrata: finche' non e' chiusa,
  // "la scorsa giornata" e' semplicemente falso — le schede dicono "risultato
  // in attesa" due righe sotto.
  const titolo = round?.closed ? "La scorsa giornata" : "La prossima giornata";

  return (
    <>
      <div className="hero">
        <Masthead current="hero" />

        <div className="wrap hero__grid" id="contenuto" tabIndex={-1}>
          <div>
            <span className="pill-label">analisi e previsioni · serie a</span>

            <h1>
              La Serie A
              <br />
              ha un nuovo
              <br />
              <em>punto di vista.</em>
            </h1>

            <p className="hero__lead">
              MatchPoint parte dalle quote di apertura, ne toglie il margine e ne
              ricava le probabilità di ogni mercato. Ogni previsione è registrata
              prima del calcio d'inizio, e l'errore è pubblico.
            </p>

            <Link className="cta" to="/dashboard">
              Esplora le partite
              <span className="cta__arrow" aria-hidden="true">
                →
              </span>
            </Link>

            <div className="figures" style={{ marginTop: "var(--s7)" }}>
              <div className="figure">
                <span className="figure__label">previsioni registrate</span>
                <span className="figure__value">
                  {season.loading ? <Skeleton width="3rem" /> : season.data?.matches_predicted}
                </span>
                <span className="figure__note">
                  {season.data ? `${season.data.matches_resolved} già giocate` : ""}
                </span>
              </div>
              <div className="figure">
                <span className="figure__label">rps di produzione</span>
                <span className="figure__value">
                  {season.loading ? <Skeleton width="4rem" /> : num(season.data?.rps_production, 4)}
                </span>
                <span className="figure__note">più basso è meglio</span>
              </div>
              <div className="figure">
                <span className="figure__label">riferimento backtest</span>
                <span className="figure__value">
                  {season.loading ? (
                    <Skeleton width="4rem" />
                  ) : (
                    num(season.data?.rps_backtest_reference, 4)
                  )}
                </span>
                <span className="figure__note">il mercato su 1140 partite</span>
              </div>
            </div>
          </div>

          <div className="stage">
            <div className="device">
              <div className="device__brand">
                <img className="device__logo" src="/Logo_NoName.png" alt="" />
                <span>
                  Match<em>Point</em>
                </span>
              </div>

              {/* NIENTE FINTO CAMPO DI RICERCA. Prima qui c'era "Cerca una
                  squadra o una partita…" con la lente: aveva l'aspetto di un
                  input ed era un link, quindi prometteva una ricerca che non
                  esiste. Al suo posto lo stato vero della giornata, che e'
                  quello che chi arriva vuole sapere per primo. */}
              <Link className="device__status" to="/dashboard">
                <span className="device__dot" aria-hidden="true" />
                <span className="device__status-text">
                  {round ? (
                    <>
                      {/* Corta di proposito: con anche lo stato ("predetta in
                          parte") la riga finiva troncata a "9 in re…", e un
                          dato mozzato e' peggio di un dato in meno. Lo stato
                          e' nel titolo della dashboard, un clic piu' in la'. */}
                      Giornata {round.matchday} · {list.length} previsioni
                    </>
                  ) : (
                    "Serie A"
                  )}
                </span>
                <span className="device__status-go" aria-hidden="true">
                  Apri →
                </span>
              </Link>

              {head ? (
                <>
                  <div className="device__feature">
                    <span className="device__label">
                      {round?.closed ? "Ultima giornata chiusa" : "Prossima partita"}
                    </span>
                    <div className="device__teams">
                      {head.home_team} <em>vs</em> {head.away_team}
                    </div>
                    <div className="device__when">
                      {localTime(head.kickoff_utc)} · Serie A
                    </div>

                    <OddsRow match={head} />
                    <Link className="device__cta" to="/dashboard">
                      Vedi analisi completa →
                    </Link>
                  </div>

                  <div className="device__section">
                    <span className="device__label">
                      Le partite della giornata {round?.matchday ?? ""}
                    </span>
                    <div className="device__list">
                      {/* Le etichette 1 X 2 una volta sola, in testa: ripetute
                          su ogni riga raddoppiavano le righe di testo senza
                          dire niente di nuovo. */}
                      <div className="device__row device__row--head" aria-hidden="true">
                        <span />
                        <span className="device__probs">
                          <span className="device__prob"><i>1</i></span>
                          <span className="device__prob"><i>X</i></span>
                          <span className="device__prob"><i>2</i></span>
                        </span>
                      </div>
                      {list.slice(1, 5).map((match) => (
                        <div className="device__row" key={`${match.home_team}-${match.away_team}`}>
                          {/* Niente stemmi: sono marchi registrati, e i nomi
                              bastano a riconoscere la partita. */}
                          <span className="device__match">
                            {match.home_team} – {match.away_team}
                          </span>
                          <span className="device__probs">
                            {(
                              [
                                ["H", "1", match.p_home],
                                ["D", "X", match.p_draw],
                                ["A", "2", match.p_away],
                              ] as Array<["H" | "D" | "A", string, number | null]>
                            ).map(([code, label, value]) => (
                              <span
                                key={code}
                                className={`device__prob${
                                  code === match.predicted_outcome ? " device__prob--on" : ""
                                }`}
                              >
                                <b aria-label={`${label}: ${prob(value)}`}>{prob(value)}</b>
                              </span>
                            ))}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="device__teams">
                  <Skeleton width="11rem" />
                </div>
              )}
            </div>

            {/* Le schede laterali sono decorative rispetto al messaggio della
                pagina: se il loro dato manca, spariscono senza portarsi via
                il titolo e la chiamata all'azione. */}
            <ErrorBoundary label="schede laterali">
              {track.data && standings.data && (
                <Floaters
                  points={track.data.by_match}
                  table={standings.data.table}
                  reference={track.data.rps_backtest_reference}
                />
              )}
            </ErrorBoundary>
          </div>
        </div>
      </div>

      <HowItWorks match={head} />

      <main className="wrap">
        <section className="section" id="statistiche">
          {rounds.error && (
            <ErrorState error={rounds.error} what="I dati non sono raggiungibili." />
          )}

           <div className="showcase__label" aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              <Eyebrow>partite in evidenza</Eyebrow>
              <h2>{titolo}</h2>
              <p className="lead" style={{ maxWidth: "600px" }}>
                Le partite con le previsioni registrate, con le probabilità e la
                selezione che il modello ha scritto prima del calcio d'inizio.
              </p>
              <Link className="link-mint" to="/dashboard"  style={{ alignSelf: "flex-start", gap: "1rem" }}>
                Vedi tutte le partite →
              </Link>
            </div>


          <div className="showcase">
            {cards.map((match) => (
              <article className="matchcard" key={`${match.home_team}-${match.away_team}`}>
                <div className="matchcard__head">
                  Serie A · {localTime(match.kickoff_utc)}
                </div>
                <div className="matchcard__body">
                  <div className="matchcard__teams">
                    <span className="matchcard__team">
                      <span className="matchcard__name">{match.home_team}</span>
                    </span>
                    <span className="matchcard__vs">vs</span>
                    <span className="matchcard__team">
                      <span className="matchcard__name">{match.away_team}</span>
                    </span>
                  </div>

                  <OddsRow match={match} compact />

                  {/* La selezione del modello su questa partita. Probabilita' e
                      QUOTA EQUA, cioe' il prezzo a valore atteso zero: serve a
                      confrontarla con quella del tuo book, non a promettere un
                      vantaggio che su 1140 partite fuori campione non esiste. */}
                  {(() => {
                    const s = selezioni.get(`${match.home_team}-${match.away_team}`);
                    if (!s) return null;
                    const esito =
                      s.won === null ? "" : s.won ? " matchcard__pick--ok" : " matchcard__pick--ko";
                    return (
                      <span className={`matchcard__pick${esito}`}>
                        <span className="matchcard__pick-key">Selezione</span>
                        <span className="matchcard__pick-val">{s.market_label}</span>
                        <span className="matchcard__pick-odds">
                          {prob(s.probability)} · quota equa {s.fair_odds.toFixed(2)}
                        </span>
                      </span>
                    );
                  })()}

                  <Link className="matchcard__cta" to="/dashboard">
                    Vedi analisi →
                  </Link>

                  {/* "Da giocare" si leggeva come "da fare": sembrava che
                      mancasse la previsione, proprio sotto la previsione. La
                      riga dice quando e' stata scritta — l'unica cosa che la
                      rende verificabile — e che si aspetta il risultato. */}
                  <span className="matchcard__note">
                    {match.goals_home !== null && match.goals_away !== null ? (
                      `finita ${match.goals_home}–${match.goals_away}`
                    ) : (
                      <>
                        <span className="matchcard__ok" aria-hidden="true">✓</span> previsione
                        del {localTime(match.timestamp_prediction)} · risultato in attesa
                      </>
                    )}
                  </span>
                </div>
              </article>
            ))}

            <div className="side-card">
              <div className="side-card__head">
                <h3>Classifica Serie A</h3>
                <Link className="link-mint" to="/dashboard">
                  completa →
                </Link>
              </div>
              <ErrorBoundary
                label="classifica"
                fallback={<p className="state">Classifica non disponibile.</p>}
              >
                <StandingsPanel limit={5} />
              </ErrorBoundary>
            </div>
          </div>
        </section>

        <section className="section" id="faq" style={{ paddingTop: 0 }}>
          <Eyebrow>domande</Eyebrow>
          <h2>Quello che conviene sapere</h2>

          <div className="faq" style={{ marginTop: "var(--s5)" }}>
            <div className="faq__item">
              <h3>Batte i bookmaker?</h3>
              <p>
                No, e non lo nasconde. Il modello parte dalle loro quote: su 1140
                partite fuori campione zero giocate su 3420 hanno valore atteso
                positivo. Serve a leggere le probabilità, non a battere il banco.
              </p>
            </div>
            <div className="faq__item">
              <h3>Cos'è l'RPS?</h3>
              <p>
                Quanto la previsione era lontana da ciò che è successo, tenendo
                conto che gli esiti sono ordinati. Più basso è meglio: {" "}
                {num(season.data?.rps_backtest_reference, 4)} è il valore che il
                mercato ottiene sul test set.
              </p>
            </div>
            <div className="faq__item">
              <h3>Le previsioni cambiano dopo?</h3>
              <p>
                Mai. Il registro è append-only: se le quote si muovono, la riga
                vecchia resta com'è. Un registro che si aggiusta a posteriori non
                misura più niente.
              </p>
            </div>
            <div className="faq__item">
              <h3>Quante partite servono per giudicarlo?</h3>
              <p>
                Più di queste.{" "}
                {season.data && season.data.hits_denominator > 0
                  ? `Finora gli esiti indovinati sono ${ratio(
                      season.data.hits,
                      season.data.hits_denominator,
                    )}, e il campione è troppo piccolo per essere indicativo.`
                  : "Il campione è ancora troppo piccolo per essere indicativo."}
              </p>
            </div>
          </div>

          {/* <aside className="notice" style={{ marginTop: "var(--s6)" }}>
            <span className="notice__label">onestà prima del marketing</span>
            <p>
              Qui non troverai mai un valore atteso, una puntata consigliata o uno
              stake. Il modello <strong>è</strong> la linea di apertura del book con
              il margine tolto: un EV calcolato sulle sue probabilità contro quelle
              stesse quote sarebbe circolare, e misurato vale −5,2% su ogni riga.
              Quota equa e overround sì, quelli descrivono il prezzo.
            </p>
          </aside> */}
        </section>
      </main>

      <Colophon
        modelVersion={season.data?.model_version ?? null}
      />
    </>
  );
}
