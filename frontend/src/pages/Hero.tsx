import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Match, MatchPoint, Round, StandingRow } from "../api/types";
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

const ICONS: Record<string, ReactNode> = {
  registro: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" strokeLinecap="round" />
    </svg>
  ),
  mercato: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </svg>
  ),
  mercati: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M4 19V5M4 19h16" strokeLinecap="round" />
      <path d="M8 15l3.5-4 3 2.5L20 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  errore: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-3z" strokeLinejoin="round" />
      <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
};

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

/** Spark dell'RPS cumulativo: la stessa serie del track record, in piccolo. */
function Spark({ points }: { points: MatchPoint[] }) {
  const values = points
    .map((point) => point.cumulative_rps)
    .filter((value): value is number => value !== null);
  if (values.length < 2) return null;

  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const x = (i: number) => (i * 170) / (values.length - 1);
  const y = (v: number) => 44 - ((v - lo) / span) * 36 - 4;

  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `0,44 ${line} 170,44`;

  return (
    <svg className="floater__spark" viewBox="0 0 170 48" role="img" aria-label="RPS cumulativo">
      <polygon className="chart__area" points={area} />
      <polyline className="chart__line" points={line} />
    </svg>
  );
}

function Floaters({
  points,
  table,
  recent,
}: {
  points: MatchPoint[];
  table: StandingRow[];
  recent: Match[];
}) {
  const top = table.slice(0, 3);
  const most = Math.max(1, ...top.map((row) => row.goals_for));

  return (
    <div className="floaters" aria-hidden="false">
      <div className="floater">
        <h4>Andamento RPS</h4>
        <Spark points={points} />
      </div>

      <div className="floater">
        <h4>Gol fatti</h4>
        <div className="floater__bars">
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

      <div className="floater">
        <h4>Ultimi esiti</h4>
        <div className="floater__dots">
          {recent.slice(-6).map((match) => (
            <span
              key={`${match.home_team}-${match.away_team}`}
              className={`floater__dot floater__dot--${
                match.correct === true ? "ok" : match.correct === false ? "ko" : "idle"
              }`}
              title={`${match.home_team} – ${match.away_team}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function Hero() {
  const season = useApi(() => api.season(), []);
  const health = useApi(() => api.health(), []);
  const rounds = useApi(() => api.rounds(), []);
  const standings = useApi(() => api.standings(), []);
  const track = useApi(() => api.trackRecord(), []);

  const round = lastWithPredictions(rounds.data);
  const matches = useApi<Match[] | null>(
    () => (round ? api.roundMatches(round.matchday) : Promise.resolve(null)),
    [round?.matchday],
  );

  const list = matches.data ?? [];
  const head = list[0] ?? null;
  const cards = list.slice(0, 3);
  const resolved = list.filter((match) => match.correct !== null);

  return (
    <>
      <div className="hero">
        <Masthead current="hero" updatedAt={health.data?.updated_at ?? null} />

        <div className="wrap hero__grid">
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
                <span className="brand__mark" style={{ width: 24, height: 24 }} aria-hidden="true">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
                  </svg>
                </span>
                Match<em>Point</em>
              </div>

              <Link className="device__search" to="/dashboard">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="7" />
                  <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
                </svg>
                Cerca una squadra o una partita…
              </Link>

              {head ? (
                <>
                  <span className="device__label">
                    {round?.closed ? "Ultima giornata chiusa" : "Prossima partita"}
                  </span>
                  <div className="device__teams">
                    {head.home_team} vs {head.away_team}
                  </div>
                  <div className="device__when">
                    {localTime(head.kickoff_utc)} · Serie A
                  </div>

                  <OddsRow match={head} />
                  <Link className="device__cta" to="/dashboard">
                    Vedi analisi completa →
                  </Link>

                  <div className="device__section">
                    <span className="device__label">
                      Le partite della giornata {round?.matchday ?? ""}
                    </span>
                    <div className="device__list">
                      {list.slice(1, 5).map((match) => (
                        <div className="device__row" key={`${match.home_team}-${match.away_team}`}>
                          <span>
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
                                className={code === match.predicted_outcome ? "on" : undefined}
                              >
                                {label} <b>{prob(value)}</b>
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

            {track.data && standings.data && (
              <Floaters
                points={track.data.by_match}
                table={standings.data.table}
                recent={resolved}
              />
            )}
          </div>
        </div>
      </div>

      <div className="strip" id="come-funziona">
        <div className="wrap strip__grid">
          {(
            [
              [
                "registro",
                "Scritte prima del fischio",
                "Un assert blocca la registrazione a partita iniziata, e un secondo controllo esclude dalle medie le righe scritte tardi.",
              ],
              [
                "mercato",
                "Mercato de-viggato",
                "Il modello parte dalle quote di apertura di B365 e ne toglie il margine con il metodo di Shin.",
              ],
              [
                "mercati",
                "Tutti i mercati, coerenti",
                "1X2, doppia chance, over/under, gol-gol: somme diverse sulla stessa matrice, quindi non possono contraddirsi.",
              ],
              [
                "errore",
                "Errore pubblicato",
                "Ogni giornata chiusa entra nel track record con il suo RPS, anche quando è andata male.",
              ],
            ] as Array<[string, string, string]>
          ).map(([icon, title, body]) => (
            <div className="strip__item" key={title}>
              <span className="strip__icon" aria-hidden="true">
                {ICONS[icon]}
              </span>
              <div>
                <h3>{title}</h3>
                <p>{body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <main className="wrap">
        <section className="section" id="statistiche">
          {rounds.error && (
            <ErrorState error={rounds.error} what="I dati non sono raggiungibili." />
          )}

          <div className="showcase">
            <div>
              <Eyebrow>partite in evidenza</Eyebrow>
              <h2>Le sfide di oggi</h2>
              <p className="lead">
                Le partite con le previsioni registrate, con le probabilità che il
                modello ha scritto prima del calcio d'inizio.
              </p>
              <Link className="link-mint" to="/dashboard">
                Vedi tutte le partite →
              </Link>
            </div>

            {cards.map((match) => (
              <article className="matchcard" key={`${match.home_team}-${match.away_team}`}>
                <div className="matchcard__head">
                  Serie A · {localTime(match.kickoff_utc)}
                </div>
                <div className="matchcard__body">
                  <div className="matchcard__teams">
                    <span className="matchcard__team">
                      <span className="matchcard__crest">{match.home_team.slice(0, 3)}</span>
                      <span className="matchcard__name">{match.home_team}</span>
                    </span>
                    <span className="matchcard__vs">vs</span>
                    <span className="matchcard__team">
                      <span className="matchcard__crest">{match.away_team.slice(0, 3)}</span>
                      <span className="matchcard__name">{match.away_team}</span>
                    </span>
                  </div>

                  <OddsRow match={match} compact />

                  <Link className="matchcard__cta" to="/dashboard">
                    Vedi analisi →
                  </Link>

                  <span className="matchcard__note">
                    {match.goals_home !== null && match.goals_away !== null
                      ? `finita ${match.goals_home}–${match.goals_away}`
                      : "da giocare"}
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
              <StandingsPanel limit={5} />
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

          <aside className="notice" style={{ marginTop: "var(--s6)" }}>
            <span className="notice__label">onestà prima del marketing</span>
            <p>
              Qui non troverai mai un valore atteso, una puntata consigliata o uno
              stake. Il modello <strong>è</strong> la linea di apertura del book con
              il margine tolto: un EV calcolato sulle sue probabilità contro quelle
              stesse quote sarebbe circolare, e misurato vale −5,2% su ogni riga.
              Quota equa e overround sì, quelli descrivono il prezzo.
            </p>
          </aside>
        </section>
      </main>

      <Colophon
        modelVersion={season.data?.model_version ?? null}
        updatedAt={health.data?.updated_at ?? null}
      />
    </>
  );
}
