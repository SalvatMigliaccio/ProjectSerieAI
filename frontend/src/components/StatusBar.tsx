import type { Status } from "../api/types";
import { ROUND_LABELS, ago, localDateTime } from "../lib/format";

/**
 * Barra di stato: sola lettura, nessun pulsante.
 *
 * E' il punto in cui il frontend mostra che il sistema gira senza poterlo
 * toccare. Un bottone qui sarebbe il modo piu' rapido di perdere la garanzia
 * che ogni previsione sia stata scritta prima del fischio d'inizio.
 */
export function StatusBar({ status }: { status: Status }) {
  const runs = status.last_runs ?? [];
  const predict = runs.find((run) => run.command === "predict_round");
  const close = runs.find((run) => run.command === "close_round");
  const failed = runs.some((run) => run.ok === false);
  const notes = status.warnings ?? [];

  const tone = failed ? "ko" : notes.length > 0 ? "warn" : "ok";
  const toneText = failed
    ? "ultima esecuzione fallita"
    : notes.length > 0
      ? "attivo, con avvisi"
      : "tutto regolare";

  const items: Array<[string, string]> = [
    [
      "ultima previsione",
      predict?.finished_at
        ? `${localDateTime(predict.finished_at)} · ${ago(predict.finished_at)}`
        : "mai eseguita",
    ],
    [
      "ultima chiusura",
      close?.finished_at
        ? `${localDateTime(close.finished_at)} · ${ago(close.finished_at)}`
        : "mai eseguita",
    ],
    [
      "giornata in corso",
      status.current_round
        ? `${status.current_round}${
            status.current_round_status
              ? ` · ${ROUND_LABELS[status.current_round_status] ?? ""}`
              : ""
          }`
        : "—",
    ],
  ];

  return (
    <>
      <div className="status">
        <span className="status__signal">
          <i className={`dot dot--${tone}`} aria-hidden="true" />
          {toneText}
        </span>

        {items.map(([key, value]) => (
          <span className="status__item" key={key}>
            <span className="status__key">{key}</span>
            <span className="status__value">{value}</span>
          </span>
        ))}
      </div>

      {notes.length > 0 && (
        <ul className="notes">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </>
  );
}
