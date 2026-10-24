/**
 * Il modello AI (M5) contro il classico (M1): come sta andando la seconda
 * opinione, misurata come la prima.
 *
 * STESSA METRICA, STESSE PARTITE. L'RPS di ogni partita giocata che entrambi
 * hanno previsto prima del fischio, e il confronto appaiato con bootstrap a
 * cluster sulla giornata — il metodo del progetto. Due medie prese su partite
 * diverse non direbbero niente.
 *
 * IL VERDETTO E' PRUDENTE PER COSTRUZIONE. Con poche giornate l'intervallo
 * del bootstrap poggia su pochissimi cluster e non e' affidabile, anche
 * quando sembra stretto: sotto le dieci giornate la pagina lo dice, invece di
 * proclamare un vincitore. Sul test set (1140 partite) i due sono
 * indistinguibili, ed e' quel numero il riferimento.
 *
 * IL GRAFICO (skill dataviz): una sola scala, due serie con colori validati
 * sul fondo scuro (arancio e blu categoriali, non il verde, che qui vuol dire
 * "esito"), legenda e etichetta diretta sull'ultimo punto, valori al
 * passaggio del mouse e alla tastiera, e la tabella dei dati per chi non
 * legge il grafico.
 */

import { useState } from "react";

import { api } from "../api/client";
import type { MLTrackRecord } from "../api/types";
import { useApi } from "../hooks/useApi";
import { ErrorState, Loading } from "./States";

/** Sotto questa soglia il bootstrap a cluster non regge: troppi pochi cluster. */
const GIORNATE_MINIME = 10;

const f4 = (v: number | null) => (v === null ? "–" : v.toFixed(4));
const segno = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(4)}`;

function Grafico({ dati }: { dati: MLTrackRecord }) {
  const [attivo, setAttivo] = useState<number | null>(null);
  const giornate = dati.rounds;
  if (giornate.length === 0) return null;

  const W = 640;
  const H = 240;
  const ML = 56;
  const MR = 96;
  const MT = 18;
  const MB = 36;

  const valori = giornate.flatMap((g) => [g.rps_classic, g.rps_ai]);
  const span = Math.max(...valori) - Math.min(...valori);
  const pad = Math.max(span * 0.15, 0.005);
  const lo = Math.min(...valori) - pad;
  const hi = Math.max(...valori) + pad;
  const x = (i: number) =>
    giornate.length === 1 ? (ML + W - MR) / 2 : ML + (i * (W - ML - MR)) / (giornate.length - 1);
  const y = (v: number) => MT + ((hi - v) * (H - MT - MB)) / (hi - lo);
  const tacche = [0, 0.5, 1].map((t) => lo + t * (hi - lo));

  const serie = [
    { chiave: "rps_classic" as const, nome: "Classico", classe: "aitr__classic" },
    { chiave: "rps_ai" as const, nome: "AI", classe: "aitr__ai" },
  ];
  const ultimo = giornate.length - 1;
  const g = attivo === null ? null : giornate[attivo];

  return (
    <div className="aitr__chart">
      <ul className="aitr__legend" aria-hidden="true">
        {serie.map((s) => (
          <li key={s.chiave}><span className={`aitr__swatch ${s.classe}`} />{s.nome}</li>
        ))}
      </ul>
      <svg
        className="chart"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`RPS per giornata, classico contro AI, giornate ${giornate[0]?.matchday}–${giornate[ultimo]?.matchday}. I valori sono nella tabella sotto.`}
        onMouseLeave={() => setAttivo(null)}
      >
        {tacche.map((v) => (
          <g key={v}>
            <line className="chart__grid" x1={ML} y1={y(v)} x2={W - MR} y2={y(v)} />
            <text className="chart__tick" x={ML - 10} y={y(v) + 4} textAnchor="end">{v.toFixed(3)}</text>
          </g>
        ))}
        <text className="chart__caption" x={ML} y={H - 4}>RPS per giornata · più basso è meglio</text>

        {attivo !== null && (
          <line className="aitr__cross" x1={x(attivo)} y1={MT} x2={x(attivo)} y2={H - MB} />
        )}

        {serie.map((s) => (
          <g key={s.chiave}>
            <polyline
              className={`aitr__line ${s.classe}`}
              points={giornate.map((gg, i) => `${x(i).toFixed(1)},${y(gg[s.chiave]).toFixed(1)}`).join(" ")}
            />
            {giornate.map((gg, i) => (
              <circle key={gg.matchday} className={`aitr__dot ${s.classe}`}
                cx={x(i)} cy={y(gg[s.chiave])} r={attivo === i ? 5 : 4} />
            ))}
            {/* Etichetta diretta sull'ultimo punto: l'identita' non sta solo nel colore. */}
            <text className="aitr__label" x={x(ultimo) + 10}
              y={y(giornate[ultimo]![s.chiave]) + (s.chiave === "rps_ai" ? 14 : -6)}>
              {s.nome}
            </text>
          </g>
        ))}

        {giornate.map((gg, i) => (
          <g key={gg.matchday}>
            <text className="chart__tick" x={x(i)} y={H - MB + 18} textAnchor="middle">G{gg.matchday}</text>
            {/* Bersaglio largo quanto la colonna, piu' grande dei punti. */}
            <rect
              className="aitr__hit"
              x={x(i) - (W - ML - MR) / Math.max(giornate.length - 1, 1) / 2}
              y={MT}
              width={(W - ML - MR) / Math.max(giornate.length - 1, 1)}
              height={H - MT - MB}
              tabIndex={0}
              aria-label={`Giornata ${gg.matchday}: classico ${gg.rps_classic.toFixed(4)}, AI ${gg.rps_ai.toFixed(4)}`}
              onMouseEnter={() => setAttivo(i)}
              onFocus={() => setAttivo(i)}
              onBlur={() => setAttivo(null)}
            />
          </g>
        ))}
      </svg>
      {g && (
        <div className="aitr__tooltip" role="status">
          <strong>Giornata {g.matchday}</strong> · {g.matches} partite
          <span><i className="aitr__swatch aitr__classic" />Classico {g.rps_classic.toFixed(4)}</span>
          <span><i className="aitr__swatch aitr__ai" />AI {g.rps_ai.toFixed(4)}</span>
        </div>
      )}
    </div>
  );
}

export function AiTrackRecord() {
  const state = useApi(() => api.mlTrackRecord(), []);
  if (state.loading) return <Loading what="carico il confronto…" />;
  if (state.error) return <ErrorState error={state.error} what="Confronto non disponibile." />;
  const d = state.data;
  if (!d || d.matches === 0) {
    return <p className="profilo__hint">Il modello AI non ha ancora previsioni su partite giocate.</p>;
  }

  const pari = d.matches - d.ai_better - d.classic_better;
  const presto = d.n_rounds < GIORNATE_MINIME;
  const verdetto = presto
    ? `Con ${d.n_rounds} giornate (${d.matches} partite) è troppo presto per dire chi è migliore: l'intervallo statistico poggia su troppe poche giornate per essere affidabile. Sul test set di 1140 partite i due modelli sono indistinguibili.`
    : d.ci_low !== null && d.ci_high !== null && d.ci_high < 0
      ? "L'AI ha fatto meglio del classico, con un intervallo che non tocca lo zero."
      : d.ci_low !== null && d.ci_low > 0
        ? "L'AI ha fatto peggio del classico, con un intervallo che non tocca lo zero."
        : "I due modelli sono indistinguibili: l'intervallo della differenza contiene lo zero.";

  return (
    <section className="aitr" aria-labelledby="aitr-titolo">
      <h2 id="aitr-titolo" className="aitr__title">Modello AI contro classico</h2>
      <dl className="aitr__stats">
        <div><dt>RPS classico</dt><dd>{f4(d.rps_classic)}</dd></div>
        <div><dt>RPS AI</dt><dd>{f4(d.rps_ai)}</dd></div>
        <div>
          <dt>Differenza AI − classico</dt>
          <dd>{d.difference === null ? "–" : segno(d.difference)}</dd>
          {d.ci_low !== null && d.ci_high !== null && (
            <span className="aitr__ci">IC 95% [{segno(d.ci_low)}, {segno(d.ci_high)}]</span>
          )}
        </div>
        <div>
          <dt>Partita per partita</dt>
          <dd>AI {d.ai_better} · classico {d.classic_better}</dd>
          <span className="aitr__ci">{pari} pari, su {d.matches} partite</span>
        </div>
      </dl>
      <p className={`aitr__verdict${presto ? " is-early" : ""}`}>{verdetto}</p>

      <Grafico dati={d} />

      <details className="aitr__data">
        <summary>Vedi i dati per giornata</summary>
        <div className="std">
          <table className="std__table">
            <thead>
              <tr>
                <th>Giornata</th>
                <th className="right">Partite</th>
                <th className="right">RPS classico</th>
                <th className="right">RPS AI</th>
                <th className="right">Differenza</th>
              </tr>
            </thead>
            <tbody>
              {d.rounds.map((g) => (
                <tr key={g.matchday}>
                  <td>{g.matchday}</td>
                  <td className="right">{g.matches}</td>
                  <td className="right">{g.rps_classic.toFixed(4)}</td>
                  <td className="right">{g.rps_ai.toFixed(4)}</td>
                  <td className="right">{segno(g.rps_ai - g.rps_classic)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
