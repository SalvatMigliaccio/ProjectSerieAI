import { Fragment, useState } from "react";

import type { Match } from "../api/types";
import {
  OUTCOME_LABELS,
  localDateTime,
  localTime,
  num,
  odds,
  overround,
  prob,
  rps,
} from "../lib/format";

/**
 * 1X2: la proporzione sopra, le cifre sotto, l'esito piu' probabile in
 * inchiostro pieno.
 *
 * I tre toni sono CATEGORIALI — casa, pari, trasferta — e hanno lo stesso peso
 * visivo: non dicono "bene" o "male". Il verde e il rosso di questa pagina sono
 * riservati a una cosa sola, l'esito azzeccato o sbagliato, e mescolarli qui
 * farebbe leggere un pronostico come un consiglio.
 */
function OddsBar({ match }: { match: Match }) {
  const { p_home, p_draw, p_away } = match;
  if (p_home === null || p_draw === null || p_away === null) return null;

  const segments = [
    { cls: "is-1", code: "H", label: "1", value: p_home },
    { cls: "is-x", code: "D", label: "X", value: p_draw },
    { cls: "is-2", code: "A", label: "2", value: p_away },
  ] as const;

  return (
    <>
      <div className="odds-bar" aria-hidden="true">
        {segments.map((segment) => (
          <i
            key={segment.cls}
            className={segment.cls}
            style={{ width: `${(segment.value * 100).toFixed(1)}%` }}
          />
        ))}
      </div>
      <div className="odds-nums">
        {segments.map((segment) => {
          const text = `${segment.label} ${prob(segment.value)}`;
          return (
            <span key={segment.cls}>
              {segment.code === match.predicted_outcome ? <b>{text}</b> : text}
            </span>
          );
        })}
      </div>
    </>
  );
}

/** Risultato ed esito. Non giocata: nessun colore — il grigio e' assenza di
 *  informazione, non un giudizio. */
function Result({ match }: { match: Match }) {
  if (match.goals_home === null || match.goals_away === null) {
    return <span className="result__pending">da giocare</span>;
  }

  const sign = match.actual_outcome ? (OUTCOME_LABELS[match.actual_outcome] ?? "") : "";
  const pill =
    match.correct === true
      ? "pill pill--ok"
      : match.correct === false
        ? "pill pill--ko"
        : "pill pill--warn";
  const pillText = match.correct === null ? `${sign} · non valida` : sign;

  return (
    <span className="result">
      <span className="result__score">
        {match.goals_home}–{match.goals_away}
      </span>
      <span className={pill}>{pillText}</span>
    </span>
  );
}

function Detail({ match }: { match: Match }) {
  const fair = [match.fair_odds_home, match.fair_odds_draw, match.fair_odds_away];
  const rows: Array<[string, string]> = [
    ["quote registrate", [match.odds_home, match.odds_draw, match.odds_away].every((v) => v === null)
      ? ""
      : `${odds(match.odds_home)} · ${odds(match.odds_draw)} · ${odds(match.odds_away)}`],
    ["quote eque", fair.every((v) => v === null) ? "" : fair.map((v) => odds(v)).join(" · ")],
    ["overround del book", overround(match.overround)],
    ["rps della partita", rps(match.rps)],
    ["previsione scritta", localDateTime(match.timestamp_prediction)],
    ["fonte delle quote", match.odds_source ?? ""],
  ];

  return (
    <tr className="detail">
      <td colSpan={7}>
        <dl className="detail-grid">
          {rows.map(([term, value]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {match.valid === false && match.invalid_reason && (
          <p className="small" style={{ margin: "var(--s4) 0 0", color: "var(--warn)" }}>
            Previsione non valida: {match.invalid_reason}. Il risultato c'è, le
            metriche no — e restano fuori da ogni media.
          </p>
        )}
      </td>
    </tr>
  );
}

export function MatchTable({ matches }: { matches: Match[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const keyOf = (match: Match) => `${match.home_team}-${match.away_team}`;

  return (
    <>
      <div className="table-scroll">
        <table className="fixtures">
          <thead>
            <tr>
              <th>Partita</th>
              <th style={{ minWidth: "148px" }}>1 · X · 2</th>
              <th className="right hide-narrow">Gol attesi</th>
              <th className="right hide-narrow">Over 2.5</th>
              <th className="right hide-narrow">Gol-gol</th>
              <th className="right hide-narrow">Quota 1 · equa</th>
              <th className="right">Esito</th>
            </tr>
          </thead>
          <tbody>
            {matches.map((match) => {
              const key = keyOf(match);
              const expanded = open === key;
              const toggle = () => setOpen(expanded ? null : key);

              return (
                <Fragment key={key}>
                  <tr
                    className="fixture"
                    tabIndex={0}
                    aria-expanded={expanded}
                    onClick={toggle}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggle();
                      }
                    }}
                  >
                    <td>
                      <div className="fixture__teams">
                        <span className="fixture__home">{match.home_team}</span>
                        <span className="fixture__away">{match.away_team}</span>
                        <span className="fixture__kickoff">{localTime(match.kickoff_utc)}</span>
                      </div>
                    </td>
                    <td>
                      <OddsBar match={match} />
                    </td>
                    <td className="right num hide-narrow">
                      {num(match.lambda_home)}
                      {match.lambda_home !== null && match.lambda_away !== null ? " – " : ""}
                      {num(match.lambda_away)}
                    </td>
                    <td className="right num hide-narrow">{prob(match.p_over25)}</td>
                    <td className="right num hide-narrow">{prob(match.p_btts)}</td>
                    <td className="right num hide-narrow">
                      {odds(match.odds_home)}
                      {match.odds_home !== null && match.fair_odds_home !== null ? " · " : ""}
                      {odds(match.fair_odds_home)}
                    </td>
                    <td className="right">
                      <Result match={match} />
                    </td>
                  </tr>
                  {expanded && <Detail match={match} />}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="small faint" style={{ marginTop: "var(--s3)" }}>
        Tocca una riga per le quote registrate, l'overround e l'RPS di quella partita.
      </p>
    </>
  );
}
