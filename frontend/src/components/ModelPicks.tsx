import { api } from "../api/client";
import type { Selection } from "../api/types";
import { useApi } from "../hooks/useApi";
import { num, odds as fmtOdds, prob, ratio } from "../lib/format";
import { ErrorState, Loading } from "./States";

/**
 * Le selezioni del modello, in riga sotto la griglia della giornata.
 *
 * STA QUI E NON NELLA COLONNA LATERALE perche' e' la linea dichiarata del
 * progetto: una per partita, soglia `config.QUOTA_MINIMA_SELEZIONE`, la stessa
 * che `predict_round` stampa il venerdi' e che il track record misura. In
 * verticale, accanto a una lettura secondaria regolabile, sembrava la meno
 * importante delle due. A fianco resta la banda per soglia di quota, che e'
 * esplorazione e lo dice.
 *
 * NON E' REGOLABILE, E NON E' UNA DIMENTICANZA. Se una soglia scelta in pagina
 * potesse ridefinirla, il track record misurerebbe una cosa e la dashboard ne
 * mostrerebbe un'altra.
 */
export function ModelPicks({ matchday }: { matchday: number | null }) {
  const picks = useApi(() => api.picks(), []);

  const righe: Selection[] = (picks.data?.with_min_odds ?? []).filter(
    (r) => matchday === null || r.matchday === matchday,
  );

  return (
    <section className="picks">
      <div className="picks__head">
        <h3>
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3l2.6 5.6 6.4.8-4.7 4.3 1.2 6.3L12 17l-5.5 3 1.2-6.3L3 9.4l6.4-.8L12 3z" strokeLinejoin="round" />
          </svg>
          Selezioni del modello
        </h3>
        <p className="small faint">
          Una per partita, la più probabile fra quelle che pagano almeno{" "}
          {num(picks.data?.min_odds ?? 1.5, 2)}. Soglia dichiarata dal progetto,
          non modificabile da qui.
          {picks.data && picks.data.with_min_odds_resolved > 0 && (
            <>
              {" "}In stagione:{" "}
              <strong>
                {ratio(picks.data.with_min_odds_won, picks.data.with_min_odds_resolved)}
              </strong>
              .
            </>
          )}
        </p>
      </div>

      {picks.loading && <Loading what="carico le selezioni…" />}
      {picks.error && <ErrorState error={picks.error} what="Selezioni non disponibili." />}
      {picks.data && righe.length === 0 && (
        <p className="small faint">Nessuna selezione per questa giornata.</p>
      )}

      <div className="picks__row">
        {righe.map((r) => {
          const esito = r.won === null ? "" : r.won ? " pick-card--ok" : " pick-card--ko";
          return (
            <article className={`pick-card${esito}`} key={`${r.matchday}-${r.home_team}-${r.market}`}>
              <span className="pick-card__match">
                {r.home_team} – {r.away_team}
              </span>
              <span className="pick-card__market">{r.market_label}</span>
              <span className="pick-card__foot">
                <span className="pick-card__odds">{fmtOdds(r.fair_odds)}</span>
                <span className="pick-card__pct">{prob(r.probability)}</span>
              </span>
            </article>
          );
        })}
      </div>
    </section>
  );
}
