import type { Selection, Selections } from "../api/types";
import type { AsyncState } from "../hooks/useApi";
import { odds as fmtOdds, prob, ratio } from "../lib/format";
import { ErrorState, Loading } from "./States";

/**
 * La colonna laterale: l'esploratore per soglia di quota, e nient'altro.
 *
 * LE DUE LETTURE NON STANNO PIU' UNA SOPRA L'ALTRA. La linea dichiarata dal
 * progetto — `ModelPicks`, sotto la griglia — e questa banda regolabile
 * dicevano cose diverse con lo stesso aspetto, incolonnate nella stessa
 * striscia: la principale sembrava la meno importante delle due. Qui resta
 * l'esplorazione, che e' dichiarata tale in pagina.
 *
 * LA SOGLIA NON E' STATO DI QUESTO COMPONENTE, ED E' IL PUNTO. Sceglierla
 * cambia anche le celle accese nella griglia della giornata, quindi vive nella
 * pagina e arriva qui come proprieta'. Tenerla qui dentro significherebbe che
 * la scheda decide per se' e la griglia mostra un'altra cosa.
 *
 * NESSUNA "SCOMMESSA CONSIGLIATA". Quota equa e probabilita' descrivono il
 * prezzo; un consiglio di giocata implicherebbe un vantaggio che su 1140
 * partite fuori campione non esiste.
 */

export const SOGLIE = [1.2, 1.5, 1.8, 2.0] as const;

function Riga({ selection }: { selection: Selection }) {
  return (
    <div className="pick-row">
      <span>
        <span className="pick-row__match">
          {selection.home_team} – {selection.away_team}
        </span>
        <br />
        <span className="pick-row__market">{selection.market_label}</span>
      </span>
      <span className="pick-row__odds">{fmtOdds(selection.fair_odds)}</span>
      <span
        className="pick-row__pct"
        style={
          selection.won === false
            ? { background: "var(--ko-wash)", color: "var(--ko)" }
            : undefined
        }
      >
        {prob(selection.probability)}
      </span>
    </div>
  );
}

export function AsideSelections({
  matchday,
  soglia,
  onSoglia,
  banda,
}: {
  matchday: number | null;
  soglia: number;
  onSoglia: (valore: number) => void;
  /** Gia' caricata dalla pagina: la stessa risposta accende le celle della
   *  griglia, quindi si chiede una volta sola. */
  banda: AsyncState<Selections>;
}) {
  const perGiornata = (righe: Selection[] | undefined) =>
    (righe ?? []).filter((r) => matchday === null || r.matchday === matchday);

  const esplorate = perGiornata(banda.data?.best_per_match);

  return (
      <div className="aside-card">
        <div className="aside-card__head">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 19V5M4 19h16" strokeLinecap="round" />
            <path d="M8 15l3.5-4 3 2.5L20 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <h3>Selezioni per soglia di quota</h3>
        </div>
        <p className="aside-card__note">
          Lettura secondaria: scegli la soglia e la giornata si accende sulla
          selezione corrispondente. Spostarla cambia la varianza, non il margine.
        </p>

        <div className="thresholds">
          {SOGLIE.map((valore) => (
            <button
              key={valore}
              type="button"
              className="threshold"
              aria-pressed={valore === soglia}
              onClick={() => onSoglia(valore)}
            >
              {valore.toFixed(2)}+
            </button>
          ))}
        </div>

        {banda.loading && <Loading what="carico…" />}
        {banda.error && <ErrorState error={banda.error} what="Selezioni non disponibili." />}

        {banda.data && esplorate.length === 0 && (
          <p className="small faint">
            Nessun mercato sopra {soglia.toFixed(2)} in questa giornata.
          </p>
        )}

        {esplorate.map((selection) => (
          <Riga
            key={`${selection.matchday}-${selection.home_team}-${selection.market}`}
            selection={selection}
          />
        ))}

        {esplorate.length > 0 && (
          <p className="small faint" style={{ marginTop: "var(--s3)" }}>
            {esplorate.length} partite di questa giornata hanno una selezione
            sopra {soglia.toFixed(2)}, e sono accese nella griglia. In stagione:{" "}
            {ratio(banda.data?.best_won ?? 0, banda.data?.best_resolved ?? 0)} a
            questa soglia.
          </p>
        )}
    </div>
  );
}
