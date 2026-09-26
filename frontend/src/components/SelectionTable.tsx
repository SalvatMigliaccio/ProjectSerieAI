import type { Selection } from "../api/types";
import { localTime, odds, prob } from "../lib/format";

/**
 * La tabella delle selezioni, condivisa fra le principali e l'esploratore.
 *
 * Una sola implementazione perche' le due viste devono presentare le stesse
 * colonne con le stesse regole: se divergessero, la differenza fra "principale"
 * ed "esplorativa" sembrerebbe una differenza di sostanza invece che di
 * criterio di scelta.
 */
function Row({ selection }: { selection: Selection }) {
  const played = selection.won !== null;
  const pill = !played ? "pill" : selection.won ? "pill pill--ok" : "pill pill--ko";
  const pillText = !played ? "da giocare" : selection.won ? "vinta" : "persa";

  return (
    <tr>
      <td>
        <div className="fixture__teams">
          <span className="fixture__home">{selection.home_team}</span>
          <span className="fixture__away">{selection.away_team}</span>
          <span className="fixture__kickoff">
            g{selection.matchday} · {localTime(selection.kickoff_utc)}
          </span>
        </div>
      </td>
      <td>
        <strong style={{ fontWeight: 500 }}>{selection.market_label}</strong>
      </td>
      <td className="right num">{prob(selection.probability)}</td>
      <td className="right num">{odds(selection.fair_odds)}</td>
      <td className="right num">
        {selection.book_odds === null ? (
          <span className="faint">dal tuo book</span>
        ) : (
          odds(selection.book_odds)
        )}
      </td>
      <td className="right">
        <span className={pill}>{pillText}</span>
      </td>
    </tr>
  );
}

export function SelectionTable({ selections }: { selections: Selection[] }) {
  return (
    <div className="table-scroll">
      <table className="fixtures">
        <thead>
          <tr>
            <th>Partita</th>
            <th>Mercato</th>
            <th className="right">Probabilità</th>
            <th className="right">Quota equa</th>
            <th className="right">Quota book</th>
            <th className="right">Esito</th>
          </tr>
        </thead>
        <tbody>
          {selections.map((selection) => (
            <Row
              key={`${selection.matchday}-${selection.home_team}-${selection.market}`}
              selection={selection}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
