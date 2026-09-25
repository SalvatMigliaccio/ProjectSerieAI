import type { SeasonSummary, TrackRecord } from "../api/types";
import { num, ratio } from "../lib/format";
import { RpsChart } from "./RpsChart";

/**
 * Track record: quante previsioni, quante azzeccate, e l'RPS nel tempo.
 *
 * STA ACCANTO ALLA CLASSIFICA, in una colonna stretta: i tre numeri non sono
 * piu' affiancati ma impilati, ciascuno con l'etichetta e la sua postilla a
 * sinistra e la cifra a destra. Affiancati in 560px le postille andavano a capo
 * tre volte e il blocco diventava piu' alto di quando erano in colonna.
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
      <div className="tr">
        <div className="tr__stats">
          <div className="tr__stat">
            <span className="tr__label">rps di produzione</span>
            <span className="tr__value">{num(summary.rps_production, 4)}</span>
            <span className="tr__note">
              {delta === null
                ? "nessuna partita chiusa"
                : `${delta > 0 ? "+" : ""}${delta.toFixed(4)} rispetto al backtest (${num(
                    summary.rps_backtest_reference,
                    4,
                  )})`}
            </span>
          </div>

          <div className="tr__stat">
            <span className="tr__label">esiti indovinati</span>
            <span className="tr__value">{ratio(summary.hits, summary.hits_denominator)}</span>
            <span className="tr__note">
              {summary.sample_significant
                ? "campione sufficiente"
                : "campione troppo piccolo per essere indicativo"}
            </span>
          </div>

          <div className="tr__stat">
            <span className="tr__label">previsioni risolte</span>
            <span className="tr__value">{summary.matches_resolved}</span>
            <span className="tr__note">
              {closedRounds > 0
                ? `${closedRounds} ${closedRounds === 1 ? "giornata chiusa" : "giornate chiuse"}`
                : "nessuna giornata chiusa"}
              {summary.matches_invalid > 0
                ? ` · ${summary.matches_invalid} escluse perché scritte dopo il fischio`
                : ""}
            </span>
          </div>
        </div>

        <div className="tr__chart">
          <h3>Andamento dell'errore</h3>
          <p className="small muted">
            Media progressiva dell'RPS, una partita alla volta. La tratteggiata è
            il riferimento del backtest — <strong>più basso è meglio</strong>. I
            primi punti oscillano moltissimo: è aritmetica della media, non il
            modello che cambia idea.
          </p>
          {points.length > 0 ? (
            <RpsChart points={points} reference={record.rps_backtest_reference} />
          ) : (
            <p className="state">Il grafico compare dopo la prima giornata archiviata.</p>
          )}

          {record.calibration === null && record.calibration_unavailable_reason && (
            <p className="small faint" style={{ marginTop: "var(--s4)" }}>
              Curva di calibrazione non disponibile: {record.calibration_unavailable_reason}.
            </p>
          )}
        </div>

        {/* GIORNATA PER GIORNATA, CON LA COLONNA DEL MERCATO ACCANTO.
            L'RPS del modello da solo non dice se una giornata e' andata male
            per colpa sua: la 4a sta a 0.2122 e sembra un disastro, ma sulle
            stesse dieci partite le quote registrate fanno 0.2116 — era una
            giornata difficile per tutti, non un errore del modello. E' lo
            stesso confronto che `backtest_log` calcola sul registro, ed e' il
            motivo per cui le quote si salvano. */}
        {record.by_round.length > 0 && (
          <div className="tr__rounds">
            <h3>Giornata per giornata</h3>
            <table className="tr__table">
              <thead>
                <tr>
                  <th>Gio</th>
                  <th className="right" title="partite valide sul totale previsto">Partite</th>
                  <th className="right" title="errore del modello, piu' basso e' meglio">RPS</th>
                  <th className="right" title="le stesse partite, con le quote registrate">Mercato</th>
                  <th className="right" title="esito secco 1 X 2 previsto da M1, sulle sole partite valide">1X2</th>
                </tr>
              </thead>
              <tbody>
                {record.by_round.map((round) => {
                  const valide = round.matches_valid ?? 0;
                  const centri =
                    round.accuracy === null ? null : Math.round(round.accuracy * valide);
                  const meglio =
                    round.rps !== null && round.rps_market !== null && round.rps < round.rps_market;
                  return (
                    <tr key={round.matchday ?? "?"}>
                      <td>{round.matchday}</td>
                      <td className="right num muted">
                        {valide}
                        {round.matches !== null && round.matches !== valide && (
                          <span className="faint"> / {round.matches}</span>
                        )}
                      </td>
                      <td className={`right num${meglio ? " tr__meglio" : ""}`}>
                        {num(round.rps, 4)}
                      </td>
                      <td className="right num muted">{num(round.rps_market, 4)}</td>
                      <td className="right num muted">
                        {centri === null ? "—" : `${centri} su ${valide}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="small faint" style={{ marginTop: "var(--s3)" }}>
              <strong>Mercato</strong> è l'errore delle quote registrate sulle
              stesse partite: serve a distinguere una giornata difficile per
              tutti da un errore del modello. Le partite escluse sono quelle
              scritte dopo il fischio, che restano nel registro ma fuori dalle
              medie.
            </p>
            <p className="small faint">
              <strong>1X2</strong> è l'esito secco — casa, pari o trasferta — ed
              è l'unica cosa che entra nel track record. <em>Non</em> è il conto
              delle selezioni: quelle si scelgono per soglia di quota, cadono
              spesso su altri mercati (under 3.5, doppia chance, "segna") e si
              contano nella scheda a fianco. Una giornata può avere tutte le
              selezioni vinte e pochi 1X2 azzeccati.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
