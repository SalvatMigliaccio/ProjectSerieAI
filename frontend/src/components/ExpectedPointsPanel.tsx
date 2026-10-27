import { api } from "../api/client";
import { useFavorite } from "../auth/Favorite";
import { useApi } from "../hooks/useApi";
import { num } from "../lib/format";
import { ErrorState, Loading } from "./States";

/**
 * I punti attesi: quanti punti a partita il modello dava a ogni squadra,
 * accanto a quanti ne ha fatti davvero, e dove porta quel ritmo a fine
 * stagione.
 *
 * QUESTA E' UNA PREVISIONE, E STA ACCANTO A DUE FATTI. Classifica e marcatori
 * sono aritmetica; questa tabella e' il modello. Per questo il titolo lo dice e
 * le colonne del modello sono separate da quelle dei risultati.
 *
 * SOLO SULLE PARTITE GIA' GIOCATE con una previsione nel registro: le prime
 * giornate non ne hanno, e la colonna "su" dice su quante partite e' fatta la
 * media. La proiezione e' un'estrapolazione del ritmo atteso sulle partite che
 * restano, senza guardare contro chi: non e' una classifica finale prevista.
 */

function scarto(fatti: number | null, attesi: number | null): string {
  if (fatti === null || attesi === null) return "–";
  const d = fatti - attesi;
  return `${d > 0 ? "+" : ""}${num(d, 1)}`;
}

export function ExpectedPointsPanel() {
  const state = useApi(() => api.expectedPoints(), []);
  const { team: preferita } = useFavorite();

  if (state.loading) return <Loading what="carico i punti attesi…" />;
  if (state.error) {
    return <ErrorState error={state.error} what="Punti attesi non disponibili." />;
  }
  if (!state.data) return null;

  return (
    <>
      <div className="std">
        <table className="std__table xpts__table">
          <colgroup>
            <col className="xpts__c-pos" />
            <col className="xpts__c-team" />
            <col span={6} className="xpts__c-num" />
          </colgroup>
          <thead>
            <tr>
              <th className="right" title="posizione in classifica oggi">#</th>
              <th>Squadra</th>
              <th className="right" title="punti a partita, in classifica">Pt/G</th>
              <th className="right" title="punti a partita attesi dal modello">Attesi/G</th>
              <th className="right hide-narrow" title="partite giocate con una previsione">Su</th>
              <th className="right hide-narrow" title="punti fatti meno punti attesi, sulle stesse partite">Scarto</th>
              <th className="right" title="punti in classifica oggi">Pt</th>
              <th className="right" title={`punti oggi + attesi a partita × partite rimaste, su ${state.data.total_games}`}>
                Proiez.
              </th>
            </tr>
          </thead>
          <tbody>
            {state.data.table.map((r) => (
              <tr key={r.team} className={r.team === preferita ? "is-fav" : undefined}>
                <td className="right"><span className="std__rank">{r.position}</span></td>
                <td><span className="std__name" title={r.team}>{r.team}</span></td>
                <td className="right num muted">{num(r.points_per_game, 2)}</td>
                <td className="right num">{num(r.expected_points_per_game, 2)}</td>
                <td className="right num muted hide-narrow">{r.predicted_matches}</td>
                <td className="right num muted hide-narrow">
                  {scarto(r.points_in_predicted, r.expected_points)}
                </td>
                <td className="right num">{r.points}</td>
                <td className="right std__pts">{num(r.projected_points, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small faint" style={{ marginTop: "var(--s2)" }}>
        Punti attesi a partita = 3 × probabilità di vittoria + probabilità di pareggio,
        secondo il modello classico, sulle sole partite già giocate che hanno una
        previsione (colonna «Su»). Lo <strong>scarto</strong> è punti fatti meno attesi
        nelle stesse partite: sopra zero, la squadra sta raccogliendo più di quanto le
        quote dicevano. La <strong>proiezione</strong> prolunga quel ritmo sulle partite
        che restano, senza guardare gli avversari: è un'estrapolazione, non una
        classifica finale prevista.
      </p>
    </>
  );
}
