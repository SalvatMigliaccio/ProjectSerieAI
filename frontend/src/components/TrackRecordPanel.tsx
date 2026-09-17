import type { SeasonSummary, TrackRecord } from "../api/types";
import { num, ratio } from "../lib/format";
import { RpsChart } from "./RpsChart";

/**
 * Track record: quante previsioni, quante azzeccate, e l'RPS nel tempo.
 *
 * DUE REGOLE VISIBILI QUI. La quota di esiti indovinati porta sempre il suo
 * denominatore; e quando il campione non basta lo si scrive, senza girarci
 * intorno. Sedici partite non dicono niente su un modello, e una dashboard che
 * le presentasse come se dicessero qualcosa mentirebbe con educazione.
 */
export function TrackRecordPanel({
  record,
  summary,
}: {
  record: TrackRecord;
  summary: SeasonSummary;
}) {
  const closedRounds = record.by_round.length;
  const points = record.by_match.filter((point) => point.cumulative_rps !== null);
  const delta =
    summary.rps_production === null
      ? null
      : summary.rps_production - summary.rps_backtest_reference;

  return (
    <>
      <div className="figures">
        <div className="figure">
          <span className="figure__label">rps di produzione</span>
          <span className="figure__value">{num(summary.rps_production, 4)}</span>
          <span className="figure__note">
            {delta === null
              ? "nessuna partita chiusa"
              : `${delta > 0 ? "+" : ""}${delta.toFixed(4)} rispetto al backtest (${num(
                  summary.rps_backtest_reference,
                  4,
                )})`}
          </span>
        </div>

        <div className="figure">
          <span className="figure__label">esiti indovinati</span>
          <span className="figure__value">{ratio(summary.hits, summary.hits_denominator)}</span>
          <span className="figure__note">
            {summary.sample_significant
              ? "campione sufficiente"
              : "campione troppo piccolo per essere indicativo"}
          </span>
        </div>

        <div className="figure">
          <span className="figure__label">previsioni risolte</span>
          <span className="figure__value">{summary.matches_resolved}</span>
          <span className="figure__note">
            {closedRounds > 0
              ? `${closedRounds} ${closedRounds === 1 ? "giornata chiusa" : "giornate chiuse"}`
              : "nessuna giornata chiusa"}
            {summary.matches_invalid > 0
              ? ` · ${summary.matches_invalid} escluse perché scritte dopo il fischio`
              : ""}
          </span>
        </div>
      </div>

      <div style={{ marginTop: "var(--s6)" }}>
        <h3>Andamento dell'errore</h3>
        <p className="small muted">
          Media progressiva dell'RPS, una partita alla volta, in ordine di data.
          La tratteggiata è il riferimento del backtest — <strong>più basso è
          meglio</strong>. I primi punti oscillano moltissimo: è aritmetica della
          media, non il modello che cambia idea.
        </p>
        {points.length > 0 ? (
          <RpsChart points={points} reference={record.rps_backtest_reference} />
        ) : (
          <p className="state">
            Il grafico compare dopo la prima giornata archiviata.
          </p>
        )}
      </div>

      {record.calibration === null && record.calibration_unavailable_reason && (
        <p className="small faint" style={{ marginTop: "var(--s5)" }}>
          Curva di calibrazione non disponibile: {record.calibration_unavailable_reason}.
        </p>
      )}
    </>
  );
}
