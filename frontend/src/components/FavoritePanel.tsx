/**
 * "La tua squadra": la stagione della squadra preferita, vista dal modello.
 *
 * Tre cose e nessuna di piu': la prossima partita con le probabilita' dal
 * punto di vista della squadra (vittoria, pari, sconfitta — non 1/X/2, che
 * costringe a ricordarsi chi gioca in casa), gli ultimi risultati, e quante
 * volte il modello ha indovinato l'esito delle sue partite. E' il caso d'uso da
 * cui il progetto e' partito: filtrare le previsioni su una squadra sola.
 */

import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Match } from "../api/types";
import { useApi } from "../hooks/useApi";
import { localTime, prob, ratio } from "../lib/format";
import { ErrorState, Loading } from "./States";

function esito(m: Match, team: string): "V" | "N" | "P" | null {
  if (m.goals_home === null || m.goals_away === null) return null;
  const mine = m.home_team === team ? m.goals_home : m.goals_away;
  const theirs = m.home_team === team ? m.goals_away : m.goals_home;
  return mine > theirs ? "V" : mine === theirs ? "N" : "P";
}

const NOMI = { V: "vittoria", N: "pareggio", P: "sconfitta" } as const;

export function FavoritePanel({ team }: { team: string }) {
  const state = useApi(() => api.teamMatches(team), [team]);

  if (state.loading) return <Loading what="carico la tua squadra…" />;
  if (state.error) return <ErrorState error={state.error} what="Squadra non disponibile." />;
  const partite = [...(state.data ?? [])].sort((a, b) =>
    (a.kickoff_utc ?? a.match_date ?? "").localeCompare(b.kickoff_utc ?? b.match_date ?? ""),
  );

  const giocate = partite.filter((m) => esito(m, team) !== null);
  const prossima = partite.find((m) => esito(m, team) === null);
  const valutate = giocate.filter((m) => m.correct !== null);
  const indovinate = valutate.filter((m) => m.correct).length;
  const ultime = giocate.slice(-5);

  const inCasa = prossima?.home_team === team;
  const pV = prossima ? (inCasa ? prossima.p_home : prossima.p_away) : null;
  const pP = prossima ? (inCasa ? prossima.p_away : prossima.p_home) : null;

  return (
    <section className="fav-panel" aria-labelledby="fav-titolo">
      <header className="fav-panel__head">
        <span className="fav-panel__star" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 3l2.6 5.6 6.4.8-4.7 4.3 1.2 6.3L12 17l-5.5 3 1.2-6.3L3 9.4l6.4-.8L12 3z" />
          </svg>
        </span>
        <h2 id="fav-titolo">{team}</h2>
      </header>

      {prossima ? (
        <div className="fav-panel__next">
          <p className="fav-panel__label">Prossima partita</p>
          <p className="fav-panel__match">
            {inCasa ? "in casa con " : "in trasferta a "}
            <strong>{inCasa ? prossima.away_team : prossima.home_team}</strong>
            <span className="faint"> · {localTime(prossima.kickoff_utc)}</span>
          </p>
          {pV !== null && (
            <dl className="fav-panel__probs">
              <div><dt>Vittoria</dt><dd>{prob(pV)}</dd></div>
              <div><dt>Pareggio</dt><dd>{prob(prossima.p_draw)}</dd></div>
              <div><dt>Sconfitta</dt><dd>{prob(pP)}</dd></div>
            </dl>
          )}
        </div>
      ) : (
        <p className="fav-panel__label">Nessuna partita in programma con una previsione.</p>
      )}

      {ultime.length > 0 && (
        <div className="fav-panel__form">
          <p className="fav-panel__label">Ultimi risultati</p>
          <ol className="fav-panel__chips">
            {ultime.map((m) => {
              const e = esito(m, team)!;
              const avversaria = m.home_team === team ? m.away_team : m.home_team;
              return (
                <li key={`${m.home_team}-${m.away_team}`}
                  className={`fav-chip fav-chip--${e}`}
                  title={`${NOMI[e]} con ${avversaria}, ${m.goals_home}-${m.goals_away}`}>
                  <span aria-hidden="true">{e}</span>
                  <span className="visually-hidden">{NOMI[e]} con {avversaria}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {valutate.length > 0 && (
        <p className="fav-panel__record">
          In stagione il modello ha indovinato l'esito nel{" "}
          <strong>{ratio(indovinate, valutate.length)}</strong> delle sue partite.
        </p>
      )}

      <Link className="btn btn--ghost btn--block fav-panel__cta" to="/pronostici">
        Fai i tuoi pronostici
      </Link>
    </section>
  );
}
