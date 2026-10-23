/**
 * Il calendario di una giornata, prima che il modello la prezzi.
 *
 * Fra la chiusura della giornata precedente e le quote del venerdi' una
 * giornata ha partite e orari ma nessuna probabilita'. Mostrarla vuota ("nessuna
 * previsione") nascondeva proprio quello che a quel punto serve: chi gioca,
 * quando, e che i pronostici sono gia' aperti.
 */

import { Link } from "react-router-dom";

import type { Fixture } from "../api/types";
import { localTime, score } from "../lib/format";

export function FixturesList({
  fixtures,
  favorite,
  title,
  pronostici = true,
}: {
  fixtures: Fixture[];
  favorite?: string | null;
  title?: string;
  /** Mostra l'invito ai pronostici: solo se la giornata non e' gia' iniziata. */
  pronostici?: boolean;
}) {
  const ordinate = [...fixtures].sort((a, b) =>
    (a.kickoff_utc ?? "").localeCompare(b.kickoff_utc ?? ""),
  );
  const daGiocare = ordinate.some((f) => f.goals_home === null);

  return (
    <section className="fixtures" aria-label={title ?? "Calendario della giornata"}>
      {title && <h2 className="fixtures__title">{title}</h2>}
      <ul className="fixtures__list">
        {ordinate.map((f) => {
          const fav = !!favorite && (f.home_team === favorite || f.away_team === favorite);
          const giocata = f.goals_home !== null && f.goals_away !== null;
          return (
            <li key={`${f.home_team}-${f.away_team}`} className={`fixture${fav ? " is-fav" : ""}`}>
              <span className="fixture__time">{localTime(f.kickoff_utc)}</span>
              <span className="fixture__teams">
                {fav && <span className="visually-hidden">La tua squadra: </span>}
                <span>{f.home_team}</span>
                <span className="faint"> – </span>
                <span>{f.away_team}</span>
              </span>
              <span className="fixture__state">
                {giocata ? (
                  <span className="score-chip">{score(f.goals_home, f.goals_away)}</span>
                ) : f.predicted ? (
                  <span className="badge badge--quiet">prevista</span>
                ) : (
                  <span className="fixture__pending">quote in arrivo</span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {pronostici && daGiocare && (
        <div className="fixtures__cta">
          <p>
            Le previsioni del modello arrivano con le quote, di solito il venerdì.
            I tuoi pronostici puoi farli già adesso.
          </p>
          <Link className="btn" to="/dashboard/pronostici">Fai i tuoi pronostici</Link>
        </div>
      )}
    </section>
  );
}
