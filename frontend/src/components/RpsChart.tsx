import type { MatchPoint } from "../api/types";

/**
 * RPS cumulativo. SVG scritto a mano: e' una spezzata, un'area e una
 * tratteggiata — una libreria di grafici da 200 kB per questo sarebbe la
 * dipendenza piu' pesante del frontend.
 *
 * LETTURA. Una sola scala colloca punti, tacche ed etichette; ogni etichetta
 * nomina un valore che il grafico raggiunge davvero. L'ultimo punto e'
 * marcato, perche' e' l'unico numero che qualcuno cerchera' davvero: dove sta
 * il track record adesso. Le etichette stanno dentro il viewBox, e il margine
 * destro e' calcolato per ospitare quella dell'ultimo punto.
 */
export function RpsChart({
  points,
  reference,
}: {
  points: MatchPoint[];
  reference: number;
}) {
  const values = points
    .map((point) => point.cumulative_rps)
    .filter((value): value is number => value !== null);

  const last = values.at(-1);
  if (values.length === 0 || last === undefined) return null;

  const W = 760;
  const H = 260;
  const ML = 52;
  const MR = 66;
  const MT = 22;
  const MB = 34;

  const span = Math.max(...values, reference) - Math.min(...values, reference);
  const pad = Math.max(span * 0.25, 0.004);
  const lo = Math.min(...values, reference) - pad;
  const hi = Math.max(...values, reference) + pad;

  const x = (i: number) => ML + (i * (W - ML - MR)) / Math.max(values.length - 1, 1);
  const y = (v: number) => MT + ((hi - v) * (H - MT - MB)) / (hi - lo);

  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${ML},${(H - MB).toFixed(1)} ${line} ${x(values.length - 1).toFixed(1)},${(H - MB).toFixed(1)}`;
  const ticks = [0, 0.5, 1].map((fraction) => lo + fraction * (hi - lo));
  const refY = y(reference);
  const lastX = x(values.length - 1);
  const lastY = y(last);

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`RPS cumulativo su ${values.length} previsioni: ${last.toFixed(4)}, riferimento del backtest ${reference.toFixed(4)}`}
    >
      {ticks.map((value) => (
        <g key={value}>
          <line className="chart__grid" x1={ML} y1={y(value)} x2={W - MR} y2={y(value)} />
          <text className="chart__tick" x={ML - 10} y={y(value) + 3} textAnchor="end">
            {value.toFixed(3)}
          </text>
        </g>
      ))}

      <line className="chart__ref" x1={ML} y1={refY} x2={W - MR} y2={refY} />
      <text className="chart__caption" x={ML + 4} y={refY - 7}>
        backtest {reference.toFixed(4)}
      </text>

      <polygon className="chart__area" points={area} />
      <polyline className="chart__line" points={line} />

      <circle className="chart__last" cx={lastX} cy={lastY} r={4} />
      <text className="chart__caption" x={lastX + 9} y={lastY + 3} textAnchor="start">
        {last.toFixed(4)}
      </text>

      <text className="chart__tick" x={ML} y={H - 12}>
        1
      </text>
      <text className="chart__tick" x={W - MR} y={H - 12} textAnchor="end">
        {values.length} partite
      </text>
    </svg>
  );
}
