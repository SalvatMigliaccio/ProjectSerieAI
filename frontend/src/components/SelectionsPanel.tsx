import { useState } from "react";

import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { ratio } from "../lib/format";
import { SelectionTable } from "./SelectionTable";
import { ErrorState, Loading } from "./States";

/**
 * Esploratore: i mercati dentro una banda di quota scelta da chi guarda.
 *
 * E' LA LETTURA SECONDARIA, e va detto in pagina. Le selezioni principali sono
 * quelle della sezione sopra — la regola dichiarata dal progetto, la stessa che
 * `predict_round` stampa il venerdi'. Qui si sposta la banda per vedere cosa
 * c'e' a un altro prezzo, non per ridefinire cosa il modello consiglia.
 *
 * PERCHE' UNA BANDA E NON UN TETTO. Sotto 1.20 restano quasi solo
 * quasi-certezze che pagano troppo poco; la banda tiene insieme le due cose che
 * decidono davvero, quanto e' probabile e quanto paga.
 */
const BANDE = [
  { label: "1.20 – 1.30", min: 1.2, max: 1.3 },
  { label: "1.30 – 1.40", min: 1.3, max: 1.4 },
  { label: "1.40 – 1.50", min: 1.4, max: 1.5 },
  { label: "1.30 – 1.50", min: 1.3, max: 1.5 },
] as const;

const DEFAULT_BAND = 1;

export function SelectionsPanel({ matchday }: { matchday: number | null }) {
  const [bandIndex, setBandIndex] = useState<number>(DEFAULT_BAND);
  const [onlyBest, setOnlyBest] = useState(true);

  const band = BANDE[bandIndex] ?? BANDE[DEFAULT_BAND]!;
  const state = useApi(() => api.selections(band.min, band.max), [band.min, band.max]);

  const source = onlyBest
    ? (state.data?.best_per_match ?? [])
    : (state.data?.selections ?? []);
  const rows = matchday === null ? source : source.filter((s) => s.matchday === matchday);
  const scored = rows.filter((s) => s.won !== null && s.valid);
  const hits = scored.filter((s) => s.won).length;

  const seasonWon = onlyBest ? state.data?.best_won : state.data?.won;
  const seasonResolved = onlyBest ? state.data?.best_resolved : state.data?.resolved;

  return (
    <>
      <p className="small muted">
        Lettura secondaria: qui la soglia la scegli tu. Le selezioni del modello
        restano quelle della sezione sopra.
      </p>

      <div className="segmented" role="group" aria-label="Banda di quota">
        <span className="segmented__label">quota fra</span>
        {BANDE.map((option, index) => (
          <button
            key={option.label}
            type="button"
            className="segmented__option"
            aria-pressed={index === bandIndex}
            onClick={() => setBandIndex(index)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="segmented" role="group" aria-label="Vista">
        <span className="segmented__label">mostra</span>
        <button
          type="button"
          className="segmented__option"
          aria-pressed={onlyBest}
          onClick={() => setOnlyBest(true)}
        >
          una per partita
        </button>
        <button
          type="button"
          className="segmented__option"
          aria-pressed={!onlyBest}
          onClick={() => setOnlyBest(false)}
        >
          tutte
        </button>
      </div>

      {state.loading && <Loading what="cerco le selezioni…" />}
      {state.error && (
        <ErrorState error={state.error} what="Non riesco a caricare le selezioni." />
      )}

      {state.data && rows.length === 0 && (
        <p className="state">
          Nessun mercato fra {band.min.toFixed(2)} e {band.max.toFixed(2)}
          {matchday !== null ? ` nella giornata ${matchday}` : ""}. Prova un'altra
          banda, oppure aspetta le quote della prossima giornata.
        </p>
      )}

      {state.data && rows.length > 0 && (
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
            La quota equa è <strong>1/p</strong>, il prezzo a valore atteso zero:
            quella del tuo book sarà più bassa, ed è lì che sta il margine.
            Spostare la banda cambia la varianza e la vincita potenziale, non il
            margine.
          </p>

          {state.data.matches_covered < state.data.matches_total && (
            <p className="small faint">
              Questa banda copre {state.data.matches_covered} partite su{" "}
              {state.data.matches_total}: nelle altre nessun mercato paga fra{" "}
              {band.min.toFixed(2)} e {band.max.toFixed(2)}. Non sono partite
              senza previsione — sono partite senza una selezione a questo prezzo.
            </p>
          )}

          {state.data.excluded_markets.length > 0 && (
            <p className="small faint">
              Esclusi i mercati che nessun book paga abbastanza da essere una
              giocata: {state.data.excluded_markets.join(", ")}.
            </p>
          )}
        </>
      )}
    </>
  );
}
