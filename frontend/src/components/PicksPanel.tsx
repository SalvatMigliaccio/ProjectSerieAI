import { useState } from "react";

import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { num, ratio } from "../lib/format";
import { SelectionTable } from "./SelectionTable";
import { ErrorState, Loading } from "./States";

/**
 * Le selezioni principali: quelle di M1, con la soglia dichiarata dal progetto.
 *
 * PERCHE' NON HANNO CONTROLLI DI QUOTA. Sono la stessa regola che
 * `predict_round` stampa il venerdi' e che il report settimanale pubblica, con
 * `config.QUOTA_MINIMA_SELEZIONE` come soglia. Se un cursore in pagina potesse
 * ridefinirle, il track record misurerebbe una cosa e la dashboard ne
 * mostrerebbe un'altra. La banda regolabile sta nella sezione sotto, ed e'
 * dichiarata come lettura secondaria.
 *
 * DUE LETTURE, ENTRAMBE GIA' NEL PROGETTO
 *   "paga almeno 1.50"  il mercato piu' probabile fra quelli che pagano almeno
 *                       la soglia: il compromesso fra quanto e' probabile e
 *                       quanto rende. E' la vista predefinita.
 *   "piu' probabile"    il mercato piu' probabile in assoluto, qualunque cosa
 *                       paghi — quasi sempre una quasi-certezza a 1.07.
 */
export function PicksPanel({ matchday }: { matchday: number | null }) {
  const [payable, setPayable] = useState(true);
  const state = useApi(() => api.picks(), []);

  const data = state.data;
  const source = payable ? (data?.with_min_odds ?? []) : (data?.most_probable ?? []);
  const rows = matchday === null ? source : source.filter((s) => s.matchday === matchday);

  const scored = rows.filter((s) => s.won !== null && s.valid);
  const hits = scored.filter((s) => s.won).length;
  const seasonWon = payable ? data?.with_min_odds_won : data?.most_probable_won;
  const seasonResolved = payable
    ? data?.with_min_odds_resolved
    : data?.most_probable_resolved;

  return (
    <>
      <div className="segmented" role="group" aria-label="Criterio">
        <span className="segmented__label">criterio</span>
        <button
          type="button"
          className="segmented__option"
          aria-pressed={payable}
          onClick={() => setPayable(true)}
        >
          paga almeno {data ? num(data.min_odds, 2) : "1.50"}
        </button>
        <button
          type="button"
          className="segmented__option"
          aria-pressed={!payable}
          onClick={() => setPayable(false)}
        >
          più probabile
        </button>
      </div>

      {state.loading && <Loading what="carico le selezioni principali…" />}
      {state.error && (
        <ErrorState error={state.error} what="Non riesco a caricare le selezioni principali." />
      )}

      {data && rows.length === 0 && (
        <p className="state">
          Nessuna selezione
          {matchday !== null ? ` nella giornata ${matchday}` : ""}: servono le
          previsioni registrate, che arrivano il venerdì con le quote.
        </p>
      )}

      {data && rows.length > 0 && (
        <>
          <SelectionTable selections={rows} />
          <p className="small muted" style={{ marginTop: "var(--s4)" }}>
            {scored.length > 0 ? (
              <>
                Andate a segno: <strong>{ratio(hits, scored.length)}</strong>
                {matchday !== null ? " in questa giornata" : ""}. In stagione:{" "}
                {ratio(seasonWon, seasonResolved)}.{" "}
              </>
            ) : (
              <>Nessuna di queste è ancora stata giocata. </>
            )}
            Una per partita, scelta con la regola dichiarata dal progetto — la
            stessa che il venerdì stampa <code>predict_round</code>. Non cambia
            con i filtri della sezione qui sotto.
          </p>
        </>
      )}
    </>
  );
}
