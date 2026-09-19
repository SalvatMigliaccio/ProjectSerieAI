import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "./States";

/**
 * La classifica. E' l'unico blocco della pagina che non contiene previsioni:
 * sono risultati gia' avvenuti, contati.
 *
 * Sta accanto ai pronostici proprio perche' le due cose non vanno confuse —
 * una e' un fatto, l'altra una probabilita'.
 *
 * NIENTE ZONE COLORATE. Champions, Europa e retrocessione si dipingono su ogni
 * classifica, ma dipendono da cose che qui non ci sono (coppe, licenze,
 * penalizzazioni) e alla quarta giornata direbbero una cosa che non e' ancora
 * vera. L'unico accento e' la barra dei punti: e' aritmetica, non un giudizio.
 *
 * I PALLINI DELLA FORMA SONO COLORATI, E NON E' UNA CONTRADDIZIONE. Vittoria,
 * pareggio e sconfitta sono tre esiti distinti, non un bene e un male su una
 * scala: il colore li distingue come li distinguono gia' le schede della
 * landing, con la stessa convenzione. Le colonne di conteggio restano neutre.
 *
 * `limit` serve alla colonna della landing, dove entrano le prime cinque: li'
 * lo spazio e' un terzo, quindi niente forma e niente barra — le colonne di
 * numeri restano tutte.
 */

const ESITO: Record<string, string> = { W: "vittoria", D: "pareggio", L: "sconfitta" };
const LETTERA: Record<string, string> = { W: "V", D: "N", L: "P" };

export function StandingsPanel({ limit }: { limit?: number }) {
  const state = useApi(() => api.standings(), []);

  if (state.loading) return <Loading what="carico la classifica…" />;
  if (state.error) {
    return <ErrorState error={state.error} what="Non riesco a caricare la classifica." />;
  }
  if (!state.data) return null;

  const stretta = limit !== undefined;
  const rows = limit ? state.data.table.slice(0, limit) : state.data.table;
  // La barra e' relativa alla capolista, non ai punti massimi possibili: a
  // settembre nessuno ha 12 punti su 114 e tutte le barre sarebbero invisibili.
  const massimo = Math.max(1, ...state.data.table.map((row) => row.points));

  return (
    <>
      <div className={`std${stretta ? " std--stretta" : ""}`}>
        <table className="std__table">
          <colgroup>
            <col className="std__c-pos" />
            <col className="std__c-team" />
            <col span={4} className="std__c-num" />
            <col span={3} className="std__c-num std__c-gol" />
            {!stretta && <col className="std__c-form" />}
            <col className="std__c-pts" />
          </colgroup>

          <thead>
            <tr>
              <th className="right">#</th>
              <th>Squadra</th>
              <th className="right" title="partite giocate">G</th>
              <th className="right" title="vittorie">V</th>
              <th className="right" title="pareggi">N</th>
              <th className="right" title="sconfitte">P</th>
              <th className="right hide-narrow" title="gol fatti">GF</th>
              <th className="right hide-narrow" title="gol subiti">GS</th>
              <th className="right hide-narrow" title="differenza reti">DR</th>
              {!stretta && <th className="std__form" title="ultime cinque, dalla piu' vecchia">Forma</th>}
              <th className="right" title="punti">Pt</th>
            </tr>
          </thead>

          <tbody>
            {rows.map((row) => (
              <tr key={row.team}>
                <td className="right">
                  <span className="std__rank">{row.position}</span>
                </td>

                <td className="std__team">
                  <span className="std__name" title={row.team}>{row.team}</span>
                  {!stretta && (
                    <span className="std__track" aria-hidden="true">
                      <i style={{ width: `${(row.points / massimo) * 100}%` }} />
                    </span>
                  )}
                </td>

                <td className="right num muted">{row.played}</td>
                <td className="right num">{row.won}</td>
                <td className="right num muted">{row.drawn}</td>
                <td className="right num muted">{row.lost}</td>
                <td className="right num muted hide-narrow">{row.goals_for}</td>
                <td className="right num muted hide-narrow">{row.goals_against}</td>
                <td className="right num hide-narrow">
                  {row.goal_difference > 0 ? `+${row.goal_difference}` : row.goal_difference}
                </td>

                {!stretta && (
                  <td className="std__form">
                    {(row.form ?? []).map((esito, indice) => (
                      <span
                        key={`${row.team}-${indice}`}
                        className={`std__pip std__pip--${esito.toLowerCase()}`}
                        title={`${row.team}: ${ESITO[esito] ?? esito}`}
                      >
                        {LETTERA[esito] ?? esito}
                      </span>
                    ))}
                  </td>
                )}

                <td className="right std__pts">{row.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="small faint" style={{ marginTop: "var(--s3)" }}>
        {state.data.matches_played} partite giocate. A pari punti:{" "}
        {state.data.tie_break}.
        {!stretta && " La barra sotto il nome è la quota di punti della capolista."}
      </p>
    </>
  );
}
