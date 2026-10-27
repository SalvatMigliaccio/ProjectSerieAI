import { api } from "../api/client";
import { useFavorite } from "../auth/Favorite";
import { useApi } from "../hooks/useApi";
import { num } from "../lib/format";
import { ErrorState, Loading } from "./States";

/**
 * Capocannonieri e assistman. Come la classifica, sono fatti e non
 * previsioni: gol e assist gia' segnati, contati da Understat.
 *
 * L'xG ACCANTO AI GOL dice se il conto viene dalla mira o dal volume: sei gol
 * su 3 di xG e sei gol su 6.4 sono due stagioni diverse, anche se in
 * classifica marcatori stanno sulla stessa riga. Per gli assist vale lo stesso
 * con l'xA, l'xG dei tiri serviti.
 *
 * L'ASSIST E' QUELLO DI UNDERSTAT: l'ultimo passaggio prima del gol. Puo'
 * differire di uno o due dal conto ufficiale della Lega, e la pagina lo dice
 * invece di presentarlo come il dato ufficiale.
 */

type Tipo = "gol" | "assist";

export function PlayersPanel({ tipo }: { tipo: Tipo }) {
  const state = useApi(() => api.players(10), []);
  const { team: preferita } = useFavorite();

  if (state.loading) return <Loading what="carico i giocatori…" />;
  if (state.error) {
    return <ErrorState error={state.error} what="Marcatori e assist non disponibili." />;
  }
  if (!state.data) return null;

  const gol = tipo === "gol";
  const righe = gol
    ? state.data.scorers.map((r) => ({
        ...r, valore: r.goals, atteso: r.xg,
        dettaglio: r.penalties > 0 ? `${r.penalties} rig.` : "",
      }))
    : state.data.assists.map((r) => ({
        ...r, valore: r.assists, atteso: r.xa, dettaglio: "",
      }));

  return (
    <>
      <div className="std">
        <table className="std__table players__table">
          <colgroup>
            <col className="players__c-pos" />
            <col className="players__c-player" />
            <col className="players__c-num" />
            <col className="players__c-num" />
          </colgroup>
          <thead>
            <tr>
              <th className="right">#</th>
              <th>Giocatore</th>
              <th className="right" title={gol ? "xG di tutti i tiri" : "xG dei tiri serviti"}>
                {gol ? "xG" : "xA"}
              </th>
              <th className="right">{gol ? "Gol" : "Assist"}</th>
            </tr>
          </thead>
          <tbody>
            {righe.map((r) => (
              <tr key={`${r.player}-${r.team}`} className={r.team === preferita ? "is-fav" : undefined}>
                <td className="right"><span className="std__rank">{r.position}</span></td>
                <td className="players__who">
                  <span className="std__name" title={r.player}>{r.player}</span>
                  <span className="players__team">
                    {r.team}{r.dettaglio && <> · {r.dettaglio}</>}
                  </span>
                </td>
                <td className="right num muted">{num(r.atteso, 1)}</td>
                <td className="right std__pts">{r.valore}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small faint" style={{ marginTop: "var(--s2)" }}>
        Fonte {state.data.source}, {state.data.matches} partite.{" "}
        {gol
          ? "Autogol esclusi."
          : "L'assist è l'ultimo passaggio prima del gol: può differire di uno o due dal conto ufficiale."}
      </p>
    </>
  );
}
