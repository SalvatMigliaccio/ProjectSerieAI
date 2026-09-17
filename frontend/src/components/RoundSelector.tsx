import type { Round } from "../api/types";
import { ROUND_LABELS } from "../lib/format";

/**
 * Le 38 giornate come un modulo stampato: celle a filetti condivisi, numeri in
 * mono, e una barretta sotto ciascuna che ne dice lo stato.
 *
 * LO STATO E' FORMA, NON SOLO COLORE. La barretta c'e' sempre e cambia tinta:
 * chi non distingue verde e grigio legge comunque la posizione e la presenza.
 * La giornata scelta si INCHIOSTRA — nessuna tinta nuova, e si vede da lontano.
 */
function modifier(round: Round): string {
  if (round.closed) return "closed";
  if (round.status === "played") return "played";
  if (round.status === "future") return "future";
  return "live";
}

export function RoundSelector({
  rounds,
  selected,
  onSelect,
}: {
  rounds: Round[];
  selected: number | null;
  onSelect: (matchday: number) => void;
}) {
  return (
    <>
      <div className="coupon" role="group" aria-label="Giornate di campionato">
        {rounds.map((round) => {
          const label = round.status
            ? (ROUND_LABELS[round.status] ?? round.status)
            : "stato ignoto";
          return (
            <button
              key={round.matchday}
              type="button"
              className={`coupon__cell coupon__cell--${modifier(round)}`}
              aria-pressed={round.matchday === selected}
              title={`Giornata ${round.matchday} — ${label}`}
              onClick={() => onSelect(round.matchday)}
            >
              {round.matchday}
              <i className="coupon__state" aria-hidden="true" />
            </button>
          );
        })}
      </div>

      <div className="legend">
        <span>
          <i style={{ background: "var(--ok)" }} />
          chiusa
        </span>
        <span>
          <i style={{ background: "var(--idle)" }} />
          giocata
        </span>
        <span>
          <i style={{ background: "var(--ink)" }} />
          in corso
        </span>
        <span>
          <i style={{ background: "var(--rule-strong)" }} />
          futura
        </span>
      </div>
    </>
  );
}
