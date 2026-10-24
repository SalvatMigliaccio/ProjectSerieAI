import { Link } from "react-router-dom";

import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Match, Round, RoundPoint, Scorer, StandingRow } from "../api/types";
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

/**
 * La giornata in vetrina, che dipende da CHI GUARDA.
 *
 * Chi puo' vedere le partite non ancora giocate (`picks:read`) vede l'ultima
 * giornata con previsioni, cioe' la prossima. Tutti gli altri vedono l'ultima
 * con almeno un risultato: l'API filtra le partite future per chi non ha quel
 * permesso, e scegliere la prossima giornata dava loro una lista VUOTA — il
 * tablet restava sullo scheletro di caricamento per sempre, da ogni venerdi'
 * in cui `predict_round` registrava il turno nuovo. L'ultima giornata giocata
 * invece e' sempre pubblica (`routes.FREE_WINDOW_DAYS` e il suo pavimento).
 */
function showcaseRound(rounds: Round[] | null, seesUpcoming: boolean): Round | null {
  if (!rounds) return null;
  const predicted = rounds.filter((round) => (round.matches_predicted ?? 0) > 0);
  if (seesUpcoming) return predicted.at(-1) ?? null;
  return predicted.filter((round) => round.matches_resolved > 0).at(-1) ?? null;
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
 * Quante volte, su cento, l'esito 1X2 piu' probabile secondo il modello e'
 * uscito davvero nelle 1140 partite di prova (2023/24-2025/26, walk-forward).
 * Riportato in CLAUDE.md ("esito piu' probabile: 53.9%").
 *
 * PERCHE' SERVE IN PAGINA. "52% indovinate" da solo sembra un lancio di
 * moneta, e lo sembra a chiunque non sappia che nel calcio il pareggio esiste
 * e il favorito perde spesso. Accanto al valore dei test si legge per quello
 * che e': in linea, sopra o sotto quanto ci si aspetta. E' un numero storico e
 * congelato, non una regola viva: per questo sta qui e non in un endpoint.
 */
const INDOVINATE_TEST = 0.539;

const LETTERA: Record<string, string> = { W: "V", D: "N", L: "P" };
const PAROLA: Record<string, string> = { W: "vittoria", D: "pareggio", L: "sconfitta" };

/**
 * Le schede accanto al tablet.
 *
 * NUMERI CHE SI LEGGONO SENZA SPIEGAZIONE. C'era la curva dell'RPS cumulativo:
 * giusta, ma per capirla bisognava sapere cos'e' l'RPS e da che parte e'
 * meglio. Chi arriva sulla home vuole sapere quante ne indovina, chi segna e
 * chi sta andando forte. L'RPS resta nella dashboard, sezione Modello, dove
 * c'e' lo spazio per spiegarlo.
 */
function Floaters({
  rounds,
  table,
  scorers,
}: {
  rounds: RoundPoint[];
  table: StandingRow[];
  scorers: Scorer[];
}) {
  // Le ultime giornate chiuse: per ognuna, quante partite su quante.
  const giornate = rounds
    .filter((r) => r.accuracy !== null && (r.matches_valid ?? 0) > 0)
    .slice(-5)
    .map((r) => {
      const su = r.matches_valid ?? 0;
      return { matchday: r.matchday, su, prese: Math.round((r.accuracy ?? 0) * su) };
    });
  const bomber = scorers.slice(0, 3);
  const most = Math.max(1, ...bomber.map((s) => s.goals));
  const top = table.slice(0, 2);

  return (
    <div className="floaters">
      {giornate.length > 0 && (
        <div className="floater">
          <p className="floater__title">Risultati indovinati</p>
          <span className="floater__sub">1X2, ultime giornate</span>
          <div className="floater__bars">
            {giornate.map((g) => (
              <div className="floater__bar" key={g.matchday ?? "?"}>
                <span>Giorn. {g.matchday}</span>
                <span className="floater__track">
                  <i className="floater__fill" style={{ width: `${(g.prese / g.su) * 100}%` }} />
                </span>
                <span>{g.prese} su {g.su}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {bomber.length > 0 && (
        <div className="floater">
          <p className="floater__title">Capocannonieri</p>
          <span className="floater__sub">gol in campionato</span>
          <div className="floater__bars">
            {bomber.map((s) => (
              <div className="floater__bar" key={`${s.player}-${s.team}`}>
                <span title={`${s.player} (${s.team})`}>{s.player.split(" ").at(-1)}</span>
                <span className="floater__track">
                  <i className="floater__fill" style={{ width: `${(s.goals / most) * 100}%` }} />
                </span>
                <span>{s.goals}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trend e forma: la SEQUENZA dei risultati, non i totali della
          classifica. Quattro vittorie non dicono se la squadra sta salendo.

          `form ?? []` non e' pignoleria: il tipo TypeScript garantisce il campo
          a compile-time, ma l'API e' un confine di rete e puo' rispondere un
          processo piu' vecchio del frontend. Senza la guardia, un campo assente
          fa esplodere il componente e con lui tutta la pagina. */}
      <div className="floater">
        <p className="floater__title">Chi va più forte</p>
        <span className="floater__sub">le prime due, ultime partite</span>
        {top.map((row) => (
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
  const players = useApi(() => api.players(3), []);

  // Durante la verifica della sessione si parte come anonimi: i dati pubblici
  // compaiono subito, e se l'accesso c'e' la vetrina passa alla prossima
  // giornata un istante dopo. Aspettare la verifica lascerebbe tutti davanti
  // a uno scheletro, anche chi non ha mai fatto l'accesso.
  const { can } = useAuth();
  const round = showcaseRound(rounds.data, can("picks:read"));
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
      {/* FUORI DA `.hero`, e non per ordine. La testata e' `sticky`, e un
          elemento sticky resta incollato solo dentro il suo contenitore:
          dentro la hero (che per di piu' ha `overflow: hidden`) spariva
          appena la hero usciva dallo schermo. */}
      <Masthead current="hero" />

      <div className="hero">

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

            {/* TRE NUMERI CHE CHIUNQUE LEGGE. Prima c'erano "RPS di produzione"
                e "riferimento backtest": esatti, ma muti per chi non sa cosa
                misurano. Ora: quante partite, quante indovinate, quante
                selezioni vinte — sempre "x su y", perche' una percentuale da
                sola nasconde quanto e' piccolo il campione. */}
            <div className="figures" style={{ marginTop: "var(--s7)" }}>
              <div className="figure">
                <span className="figure__label">partite previste</span>
                <span className="figure__value">
                  {season.loading ? <Skeleton width="3rem" /> : season.data?.matches_predicted}
                </span>
                <span className="figure__note">
                  {season.data ? `${season.data.matches_resolved} già giocate` : ""}
                </span>
              </div>
              <div className="figure">
                <span className="figure__label">risultato 1X2 indovinato</span>
                <span className="figure__value">
                  {season.loading ? <Skeleton width="4rem" /> : prob(season.data?.hit_rate ?? null)}
                </span>
                <span className="figure__note">
                  {season.data && season.data.hits_denominator > 0
                    ? `${season.data.hits} su ${season.data.hits_denominator} · nei test ${prob(INDOVINATE_TEST)}`
                    : `nei test ${prob(INDOVINATE_TEST)}`}
                </span>
              </div>
              <div className="figure">
                <span className="figure__label">selezioni vinte</span>
                <span className="figure__value">
                  {picks.loading ? (
                    <Skeleton width="4rem" />
                  ) : picks.data && picks.data.with_min_odds_resolved > 0 ? (
                    prob(picks.data.with_min_odds_won / picks.data.with_min_odds_resolved)
                  ) : (
                    "–"
                  )}
                </span>
                <span className="figure__note">
                  {picks.data && picks.data.with_min_odds_resolved > 0
                    ? `${picks.data.with_min_odds_won} su ${picks.data.with_min_odds_resolved} · quota da ${num(picks.data.min_odds, 2)}`
                    : ""}
                </span>
              </div>
            </div>
          </div>

          <div className="stage">
            <div className="device">
              <div className="device__brand">
                <img className="device__logo" src="/Logo_NoName.png" alt="" width={331} height={333} />
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
              ) : rounds.loading || matches.loading ? (
                <div className="device__teams">
                  <Skeleton width="11rem" />
                </div>
              ) : (
                // Caricato e vuoto non e' "sto caricando": uno scheletro che
                // non finisce mai sembra un sito rotto.
                <div className="device__when">Nessuna partita da mostrare.</div>
              )}
            </div>

            {/* Le schede laterali sono decorative rispetto al messaggio della
                pagina: se il loro dato manca, spariscono senza portarsi via
                il titolo e la chiamata all'azione. */}
            <ErrorBoundary label="schede laterali">
              {track.data && standings.data && (
                <Floaters
                  rounds={track.data.by_round}
                  table={standings.data.table}
                  scorers={players.data?.scorers ?? []}
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

           <div className="showcase__label" id="partite" aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
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
                      `finita ${match.goals_home}–${match.goals_away}${
                        match.status === "played" ? " · provvisorio" : ""}`
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

        {/* DUE LETTORI, UN SOLO PRODOTTO. Chi legge questa pagina o vuole
            numeri verificabili o vuole giocarci: sono due aspettative diverse
            sullo stesso dato, e nasconderne una fa perdere l'altro. Quello
            che NON cambia fra le due colonne e' il contenuto — probabilita',
            quota equa, errore misurato — perche' un prodotto che promette
            valore atteso a chi scommette mentirebbe sui propri numeri: su
            1140 partite fuori campione le giocate a valore positivo contro
            B365 sono zero su 3420. */}
        <section className="section" id="per-chi" style={{ paddingTop: 0 }}>
          <Eyebrow>per chi e'</Eyebrow>
          <h2>Due modi di leggerlo.</h2>

          <div className="audience">
            <article className="audience__card">
              <h3>Se ti interessano i numeri</h3>
              <p>
                Ogni previsione e' registrata prima del calcio d'inizio e non si
                riscrive mai. Trovi l'RPS giornata per giornata, il confronto
                con il mercato sulle stesse partite, e il metodo scritto per
                intero: de-vigging di Shin sulle quote di apertura, matrice dei
                risultati esatti, walk-forward per giornata.
              </p>
              <ul className="audience__list">
                <li>Track record pubblico, errori compresi</li>
                <li>Il riferimento e' il mercato, non una baseline comoda</li>
                <li>Quando il campione e' troppo piccolo, lo diciamo</li>
              </ul>
            </article>

            <article className="audience__card">
              <h3>Se ci giochi</h3>
              <p>
                Per ogni partita trovi la probabilita' di ogni mercato e la{" "}
                <strong>quota equa</strong>, cioe' il prezzo a cui la giocata
                andrebbe in pari. Serve a confrontarla con quella del tuo book:
                se paga meno, stai pagando margine.
              </p>
              <ul className="audience__list">
                <li>1X2, doppia chance, Over/Under, Goal/No Goal</li>
                <li>Quota equa accanto a ogni selezione</li>
                <li>
                  Nessun valore atteso, nessuno stake consigliato: misurato, non
                  c'e' vantaggio da vendere
                </li>
              </ul>
            </article>
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
                Le selezioni vincono spesso perché sono le più probabili, e
                quindi pagano poco: vincere spesso non vuol dire guadagnare.
              </p>
            </div>
            <div className="faq__item">
              <h3>Quante partite indovina?</h3>
              <p>
                Circa una su due, ed è normale: nel calcio il favorito perde o
                pareggia spesso. Nelle 1140 partite di prova il risultato più
                probabile è uscito il {prob(INDOVINATE_TEST)} delle volte
                {season.data && season.data.hits_denominator > 0
                  ? `; in questa stagione ${season.data.hits} su ${season.data.hits_denominator}.`
                  : "."}{" "}
                Conta di più che le probabilità siano giuste: se dice 70%, deve
                uscire circa sette volte su dieci.
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
