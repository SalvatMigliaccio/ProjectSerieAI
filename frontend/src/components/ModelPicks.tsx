import { useState } from "react";

import { api } from "../api/client";
import type { Selection } from "../api/types";
import { useApi } from "../hooks/useApi";
import { num, odds as fmtOdds, prob, ratio, score } from "../lib/format";
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
 *
 * DUE MODELLI, STESSA REGOLA. Il pulsante "Modello AI" mostra le selezioni
 * fatte con la stessa soglia e lo stesso criterio, ma dai gol attesi di M5
 * (LightGBM): le due liste differiscono solo per il modello. Si parte dal
 * classico perche' e' quello del track record; l'AI e' una seconda opinione,
 * indistinguibile dal mercato sul test set, e lo dice.
 */

type Modello = "classico" | "ai";

const MODELLI: Array<{ chiave: Modello; etichetta: string }> = [
  { chiave: "classico", etichetta: "Modello classico" },
  { chiave: "ai", etichetta: "Modello AI" },
];
export function ModelPicks({ matchday }: { matchday: number | null }) {
  const [modello, setModello] = useState<Modello>("classico");
  const picks = useApi(
    () => (modello === "ai" ? api.picksMl() : api.picks()),
    [modello],
  );

  const tutte = picks.data?.with_min_odds ?? [];
  const righe: Selection[] = tutte.filter(
    (r) => matchday === null || r.matchday === matchday,
  );
  // DUE VUOTI DIVERSI. Una giornata senza M5 e' normale (gira dalla 4); una
  // stagione intera senza M5 vuol dire che l'API non trova i file — ed era
  // proprio questo che il vecchio messaggio "viene calcolato dalla giornata 4"
  // nascondeva, facendo cercare il problema nella giornata.
  const vuotoAi =
    modello === "ai" && tutte.length === 0
      ? "Le previsioni del modello AI non sono disponibili su questo server."
      : `Nessuna selezione del modello AI per questa giornata: il modello è disponibile per le giornate ${[
          ...new Set(tutte.map((r) => r.matchday)),
        ]
          .sort((a, b) => a - b)
          .join(", ")}.`;

  return (
    <section className="picks">
      <div className="picks__head">
        <h3>
          <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3l2.6 5.6 6.4.8-4.7 4.3 1.2 6.3L12 17l-5.5 3 1.2-6.3L3 9.4l6.4-.8L12 3z" strokeLinejoin="round" />
          </svg>
          Selezioni del modello
        </h3>
        <div className="filters picks__models" role="group" aria-label="Modello">
          {MODELLI.map(({ chiave, etichetta }) => (
            <button
              key={chiave}
              type="button"
              className="filter"
              aria-pressed={modello === chiave}
              onClick={() => setModello(chiave)}
            >
              {etichetta}
            </button>
          ))}
        </div>
        <p className="small faint">
          Una per partita, la più probabile fra quelle che pagano almeno{" "}
          {picks.data ? num(picks.data.min_odds, 2) : "…"}. Soglia dichiarata dal progetto,
          non modificabile da qui.
          {modello === "classico"
            ? " Modello classico: le quote di apertura senza il margine del book, quello del track record."
            : " Modello AI (LightGBM): parte dalle stesse quote e le corregge con la forma delle squadre. Seconda opinione: sul test set è indistinguibile dal mercato."}
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
        <p className="small faint">
          {modello === "ai"
            ? vuotoAi
            : "Nessuna selezione per questa giornata."}
        </p>
      )}

      <div className="std">
        <table className="std__table picks__table">
          <colgroup>
            <col className="picks__c-match" />
            <col className="picks__c-market" />
            <col className="picks__c-num" />
            <col className="picks__c-num" />
            <col className="picks__c-esito" />
          </colgroup>
          <thead>
            <tr>
              <th>Partita</th>
              <th>Selezione</th>
              <th className="right hide-narrow" title="probabilita' secondo il modello">Prob.</th>
              <th className="right" title="1/p, il prezzo a valore atteso zero">
                Quota<span className="hide-narrow"> equa</span>
              </th>
              <th className="right">Esito</th>
            </tr>
          </thead>
          <tbody>
            {righe.map((r) => (
              <tr key={`${r.matchday}-${r.home_team}-${r.market}`}>
                <td className="picks__match" title={`${r.home_team} – ${r.away_team}`}>
                  {r.home_team} <span className="faint">–</span> {r.away_team}
                </td>
                <td className="picks__market">{r.market_label}</td>
                <td className="right num muted hide-narrow">{prob(r.probability)}</td>
                <td className="right picks__odds">{fmtOdds(r.fair_odds)}</td>
                <td className="right">
                  {r.won === null ? (
                    <span className="faint">–</span>
                  ) : (
                    <span className={`score-chip score-chip--${r.won ? "ok" : "ko"}`}>
                      {score(r.goals_home, r.goals_away)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

    </section>
  );
}
