import { Fragment, useState } from "react";

import { api } from "../api/client";
import type { MLMarket, MLMatch } from "../api/types";
import { useApi } from "../hooks/useApi";
import { localTime, odds as fmtOdds, prob, score } from "../lib/format";
import { ErrorState, Loading } from "./States";

/**
 * Il modello ML (M5) sulla giornata scelta, accanto al mercato.
 *
 * E' UNA SECONDA OPINIONE, E LO DICE PRIMA DEI NUMERI. M5 parte dalle quote di
 * apertura e le corregge con la forma delle squadre; sul test set (1140
 * partite) e' indistinguibile dal mercato, quindi le previsioni ufficiali — e
 * il track record — restano quelle di M1. Mostrarlo senza dirlo farebbe
 * leggere uno scarto come un'occasione.
 *
 * UNA RIGA PER PARTITA, I TRE MERCATI CHE SI CERCANO: 1X2, gol/no gol,
 * under/over 2.5. Il resto (doppia chance, linee 1.5 e 3.5) si apre al clic:
 * diciotto numeri per partita in una tabella sola non li legge nessuno.
 *
 * I mercati arrivano gia' calcolati dall'API, dalla stessa matrice dei
 * risultati del report: qui non si ricalcola niente.
 */

const PRINCIPALI = ["1X2", "Gol / No gol", "Under / Over 2.5"];

function scelta(match: MLMatch, group: string): MLMarket | undefined {
  return match.markets.find((m) => m.group === group && m.pick);
}

function Esito({ won }: { won: boolean | null }) {
  if (won === null) return null;
  return won ? (
    <span className="ml__won" title="uscito">✓</span>
  ) : (
    <span className="ml__lost" title="non uscito">✗</span>
  );
}

/** Punti percentuali con segno, e zero senza segno: "-0.0" sarebbe un movimento che non c'e'. */
function scarto(diff: number): string {
  const pt = Math.round(diff * 1000) / 10;
  if (pt === 0) return "0.0";
  return `${pt > 0 ? "+" : ""}${pt.toFixed(1)}`;
}

function Dettaglio({ match }: { match: MLMatch }) {
  const gruppi = [...new Set(match.markets.map((m) => m.group))];
  const giocata = match.goals_home !== null && match.goals_away !== null;
  return (
    <div className="ml__detail">
      <p className="small faint">
        Gol attesi secondo M5 <strong>{match.lambda_home_ml.toFixed(2)} – {match.lambda_away_ml.toFixed(2)}</strong>
        {" "}(mercato {match.lambda_home_market.toFixed(2)} – {match.lambda_away_market.toFixed(2)}).
        In evidenza la più probabile di ogni gruppo secondo M5.
      </p>
      <table className="std__table ml__markets">
        <thead>
          <tr>
            <th>Mercato</th>
            <th className="right">M5</th>
            <th className="right">Mercato</th>
            <th className="right" title="punti percentuali, M5 meno mercato">Scarto</th>
            <th className="right hide-narrow" title="1/p, il prezzo a valore atteso zero">Quota equa</th>
            {giocata && <th className="right">Esito</th>}
          </tr>
        </thead>
        <tbody>
          {gruppi.map((gruppo) => (
            <Fragment key={gruppo}>
              <tr className="ml__group">
                <td colSpan={giocata ? 6 : 5}>{gruppo}</td>
              </tr>
              {match.markets
                .filter((m) => m.group === gruppo)
                .map((m) => (
                  <tr key={m.market} className={m.pick ? "ml__row--pick" : undefined}>
                    <td>{m.label}</td>
                    <td className="right num">{prob(m.p_ml)}</td>
                    <td className="right num muted">{prob(m.p_market)}</td>
                    <td className={`right num ${m.difference > 0.0005 ? "ml__up" : m.difference < -0.0005 ? "ml__down" : "faint"}`}>
                      {scarto(m.difference)}
                    </td>
                    <td className="right num hide-narrow">{fmtOdds(m.fair_odds_ml)}</td>
                    {giocata && (
                      <td className="right"><Esito won={m.won} /></td>
                    )}
                  </tr>
                ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MLPanel({ matchday }: { matchday: number | null }) {
  const ml = useApi(
    () => (matchday === null ? Promise.resolve(null) : api.ml(matchday)),
    [matchday],
  );
  const [aperta, setAperta] = useState<string | null>(null);

  return (
    <section className="picks ml">
      <div className="picks__head">
        <h3>
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="6" cy="6" r="2.2" />
            <circle cx="18" cy="6" r="2.2" />
            <circle cx="12" cy="18" r="2.2" />
            <path d="M7.6 7.6l3.2 8.4M16.4 7.6l-3.2 8.4M8.2 6h7.6" strokeLinecap="round" />
          </svg>
          Modello ML · LightGBM
          <span className="ml__badge">seconda opinione</span>
        </h3>
        <p className="small faint">
          Parte dalle quote di apertura e le corregge con la forma delle squadre. Sul test set
          (1140 partite) è indistinguibile dal mercato: le previsioni ufficiali restano quelle
          qui sopra. Uno scarto grande è una partita da capire, non un'occasione.
        </p>
      </div>

      {ml.loading && <Loading what="carico il modello ML…" />}
      {ml.error && <ErrorState error={ml.error} what="Modello ML non disponibile." />}
      {!ml.loading && !ml.error && ml.data === null && matchday !== null && (
        <p className="small faint">Il modello ML non è stato calcolato su questa giornata.</p>
      )}
      {ml.data && ml.data.matches.length === 0 && (
        <p className="small faint">
          Le previsioni del modello ML sulle partite ancora da giocare sono riservate agli abbonati.
        </p>
      )}

      {ml.data && ml.data.matches.length > 0 && (
        <div className="std">
          <table className="std__table ml__table">
            <colgroup>
              <col className="ml__c-match" />
              <col className="ml__c-pick" />
              <col className="ml__c-pick" />
              <col className="ml__c-pick" />
              <col className="ml__c-num" />
              <col className="ml__c-open" />
            </colgroup>
            <thead>
              <tr>
                <th>Partita</th>
                <th>1X2</th>
                <th>Gol / No gol</th>
                <th>Under / Over 2.5</th>
                <th className="right hide-narrow" title="scarto massimo da mercato sull'1X2, in punti percentuali">Scarto</th>
                <th aria-label="dettaglio" />
              </tr>
            </thead>
            <tbody>
              {ml.data.matches.map((match) => {
                const chiave = `${match.home_team}-${match.away_team}`;
                const espansa = aperta === chiave;
                return (
                  <Fragment key={chiave}>
                    <tr className={espansa ? "ml__row--open" : undefined}>
                      <td className="picks__match" title={`${match.home_team} – ${match.away_team}`}>
                        {match.home_team} <span className="faint">–</span> {match.away_team}
                        <span className="ml__when">
                          {match.goals_home !== null && match.goals_away !== null
                            ? `finita ${score(match.goals_home, match.goals_away)}${match.status === "played" ? " · provvisorio" : ""}`
                            : localTime(match.kickoff_utc)}
                        </span>
                      </td>
                      {PRINCIPALI.map((gruppo) => {
                        const s = scelta(match, gruppo);
                        return (
                          <td key={gruppo}>
                            {s && (
                              <span className="ml__pick">
                                <strong>{s.label}</strong>
                                <span className="ml__p">{prob(s.p_ml)}</span>
                                <Esito won={s.won} />
                              </span>
                            )}
                          </td>
                        );
                      })}
                      <td className="right num hide-narrow">
                        {(match.max_divergence_1x2 * 100).toFixed(1)} pt
                      </td>
                      <td className="right">
                        <button
                          type="button"
                          className="ml__toggle"
                          aria-expanded={espansa}
                          aria-label={`${espansa ? "chiudi" : "apri"} tutti i mercati di ${match.home_team} – ${match.away_team}`}
                          onClick={() => setAperta(espansa ? null : chiave)}
                        >
                          {espansa ? "−" : "+"}
                        </button>
                      </td>
                    </tr>
                    {espansa && (
                      <tr className="ml__detail-row">
                        <td colSpan={6}>
                          <Dettaglio match={match} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
