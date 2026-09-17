import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "./States";

/**
 * La classifica. E' l'unico blocco della pagina che non contiene previsioni:
 * sono risultati gia' avvenuti, contati.
 *
 * Sta accanto ai pronostici proprio perche' le due cose non vanno confuse —
 * una e' un fatto, l'altra una probabilita' — e per questo non prende nessun
 * colore semantico: verde e rosso qui direbbero "bene" e "male" su un dato che
 * non ha ne' l'uno ne' l'altro.
 *
 * `limit` serve alla colonna della landing, dove entrano le prime cinque.
 */
export function StandingsPanel({ limit }: { limit?: number }) {
  const state = useApi(() => api.standings(), []);

  if (state.loading) return <Loading what="carico la classifica…" />;
  if (state.error) {
    return <ErrorState error={state.error} what="Non riesco a caricare la classifica." />;
  }
  if (!state.data) return null;

  const rows = limit ? state.data.table.slice(0, limit) : state.data.table;

  return (
    <>
      <div className="table-scroll">
        <table className="fixtures standings">
          <thead>
            <tr>
              <th className="right">#</th>
              <th>Squadra</th>
              <th className="right">G</th>
              <th className="right">V</th>
              <th className="right">N</th>
              <th className="right">P</th>
              <th className="right hide-narrow">GF</th>
              <th className="right hide-narrow">GS</th>
              <th className="right hide-narrow">DR</th>
              <th className="right">Pt</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.team}>
                <td className="right num faint">{row.position}</td>
                <td style={{ fontWeight: 500 }}>{row.team}</td>
                <td className="right num">{row.played}</td>
                <td className="right num">{row.won}</td>
                <td className="right num">{row.drawn}</td>
                <td className="right num">{row.lost}</td>
                <td className="right num hide-narrow">{row.goals_for}</td>
                <td className="right num hide-narrow">{row.goals_against}</td>
                <td className="right num hide-narrow">
                  {row.goal_difference > 0 ? `+${row.goal_difference}` : row.goal_difference}
                </td>
                <td className="right num" style={{ fontWeight: 600 }}>
                  {row.points}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="small faint" style={{ marginTop: "var(--s3)" }}>
        {state.data.matches_played} partite giocate. A pari punti:{" "}
        {state.data.tie_break}.
      </p>
    </>
  );
}
