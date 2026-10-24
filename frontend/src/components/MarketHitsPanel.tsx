import type { MarketHits } from "../api/types";
import { prob } from "../lib/format";

/**
 * Quante ne indovina, mercato per mercato.
 *
 * L'1X2 DA SOLO FA SEMBRARE IL MODELLO UN LANCIO DI MONETA. Ha tre esiti, e il
 * pareggio capita una volta su quattro senza essere quasi mai il piu'
 * probabile. Over/under, goal/no goal e "segna la squadra" hanno due esiti:
 * messi accanto, raccontano il modello per intero.
 *
 * OGNI NUMERO HA ACCANTO IL SUO METRO. "Segna la squadra di casa" esce in
 * oltre otto partite su dieci comunque: un 82% li' e' meno di quanto si
 * otterrebbe dicendo sempre "si'". Mostrare il tasso senza la frequenza di
 * base farebbe leggere bravura dove c'e' solo il calcio com'e' fatto — ed e'
 * esattamente l'impressione che il resto della pagina si rifiuta di dare.
 */
export function MarketHitsPanel({ data }: { data: MarketHits }) {
  return (
    <div className="hits">
      {data.markets.map((m) => {
        const tasso = m.resolved > 0 ? m.hits / m.resolved : null;
        return (
          <article className="hits__card" key={m.key}>
            <h3 className="hits__label">{m.label}</h3>
            <span className="hits__value">{prob(tasso)}</span>
            <span className="hits__count">
              {m.hits} su {m.resolved}
            </span>
            <span className="hits__base">
              {m.base_rate === null
                ? "tre esiti: il più difficile"
                : `il «sì» esce comunque nel ${prob(m.base_rate)}`}
            </span>
          </article>
        );
      })}
    </div>
  );
}
