/**
 * "La tua squadra": la stagione della squadra preferita, vista dal modello.
 *
 * Tre cose e nessuna di piu': la prossima partita con le probabilita' dal
 * punto di vista della squadra (vittoria, pari, sconfitta — non 1/X/2, che
 * costringe a ricordarsi chi gioca in casa), gli ultimi quattro risultati, e
 * quante volte i modelli hanno indovinato l'esito delle sue partite. I due
 * modelli, classico e AI, stanno uno sotto l'altro sulle stesse colonne: il
 * confronto e' la cosa da vedere, non un dettaglio da andare a cercare. E' il caso d'uso da
 * cui il progetto e' partito: filtrare le previsioni su una squadra sola.
 */

import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { MLMatch, Match } from "../api/types";
import { useApi } from "../hooks/useApi";
import { chiavePartita, conModelloAi } from "../lib/ai";
import { localTime, prob, ratio } from "../lib/format";
import { ErrorState, Loading } from "./States";

function esito(m: Match, team: string): "V" | "N" | "P" | null {
  if (m.goals_home === null || m.goals_away === null) return null;
  const mine = m.home_team === team ? m.goals_home : m.goals_away;
  const theirs = m.home_team === team ? m.goals_away : m.goals_home;
  return mine > theirs ? "V" : mine === theirs ? "N" : "P";
}

const NOMI = { V: "vittoria", N: "pareggio", P: "sconfitta" } as const;

/** Vittoria / pari / sconfitta dal punto di vista della squadra. */
function puntoDiVista(m: Match, team: string): [number | null, number | null, number | null] {
  const casa = m.home_team === team;
  return [casa ? m.p_home : m.p_away, m.p_draw, casa ? m.p_away : m.p_home];
}

function record(partite: Match[]): { giuste: number; totali: number } {
  const valutate = partite.filter((m) => m.correct !== null);
  return { giuste: valutate.filter((m) => m.correct).length, totali: valutate.length };
}

export function FavoritePanel({ team }: { team: string }) {
  const state = useApi(() => api.teamMatches(team), [team]);
  const ai = useApi<MLMatch[]>(() => api.mlTeam(team).catch(() => []), [team]);

  if (state.loading) return <Loading what="carico la tua squadra…" />;
  if (state.error) return <ErrorState error={state.error} what="Squadra non disponibile." />;
  const partite = [...(state.data ?? [])].sort((a, b) =>
    (a.kickoff_utc ?? a.match_date ?? "").localeCompare(b.kickoff_utc ?? b.match_date ?? ""),
  );
  const perPartita = new Map((ai.data ?? []).map((m) => [chiavePartita(m), m]));
  const conAi = partite
    .filter((m) => perPartita.has(chiavePartita(m)))
    .map((m) => conModelloAi(m, perPartita.get(chiavePartita(m))));

  const giocate = partite.filter((m) => esito(m, team) !== null);
  const prossima = partite.find((m) => esito(m, team) === null);
  const prossimaAi = prossima && perPartita.has(chiavePartita(prossima))
    ? conModelloAi(prossima, perPartita.get(chiavePartita(prossima)))
    : null;
  const ultime = giocate.slice(-4);
  const classico = record(partite);
  const modelloAi = record(conAi);

  const inCasa = prossima?.home_team === team;
  const righe: Array<[string, Match]> = prossima
    ? [["Classico", prossima], ...(prossimaAi ? [["AI", prossimaAi] as [string, Match]] : [])]
    : [];

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
          <table className="fav-panel__probs">
            <caption className="visually-hidden">
              Probabilità della prossima partita dal punto di vista della squadra, per modello
            </caption>
            <thead>
              <tr>
                <th scope="col"><span className="visually-hidden">Modello</span></th>
                <th scope="col">Vittoria</th>
                <th scope="col">Pari</th>
                <th scope="col">Sconfitta</th>
              </tr>
            </thead>
            <tbody>
              {righe.map(([nome, m]) => {
                const [v, n, sc] = puntoDiVista(m, team);
                return (
                  <tr key={nome}>
                    <th scope="row">{nome}</th>
                    <td>{prob(v)}</td>
                    <td>{prob(n)}</td>
                    <td>{prob(sc)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!prossimaAi && (
            <p className="fav-panel__hint">Il modello AI non ha ancora girato su questa partita.</p>
          )}
        </div>
      ) : (
        <p className="fav-panel__label">Nessuna partita in programma con una previsione.</p>
      )}

      {ultime.length > 0 && (
        <div className="fav-panel__form">
          <p className="fav-panel__label">Ultime quattro</p>
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

      {(classico.totali > 0 || modelloAi.totali > 0) && (
        <div>
          <p className="fav-panel__label">Esito indovinato in stagione</p>
          <dl className="fav-panel__record">
            <div>
              <dt>Classico</dt>
              <dd>{ratio(classico.giuste, classico.totali)}</dd>
            </div>
            <div>
              <dt>AI</dt>
              <dd>{modelloAi.totali ? ratio(modelloAi.giuste, modelloAi.totali) : "–"}</dd>
            </div>
          </dl>
        </div>
      )}

      <Link className="btn btn--ghost btn--block fav-panel__cta" to="/dashboard/pronostici">
        Fai i tuoi pronostici
      </Link>
    </section>
  );
}
