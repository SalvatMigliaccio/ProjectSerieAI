/**
 * La testata di ogni schermata della dashboard: titolo, riga sotto, e a destra
 * quello che la schermata vuole mettere a portata di mano.
 *
 * UNA SOLA PER TUTTE E QUATTRO, ed e' il motivo per cui esiste. Ogni schermata
 * ne aveva una sua — la giornata una scheda con lo sfondo, le altre un titolo
 * nudo con margini diversi — e passando da una all'altra il contenuto
 * saltava, e nessuna partiva all'altezza della barra laterale.
 */

import type { ReactNode } from "react";

export function ScreenHeader({
  title,
  badge,
  subtitle,
  aside,
}: {
  title: ReactNode;
  badge?: ReactNode;
  subtitle?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="dash-banner screen-header">
      <div className="dash-banner__title">
        <div className="dash-banner__row">
          <h1>{title}</h1>
          {badge}
        </div>
        {subtitle && <span className="dash-banner__dates">{subtitle}</span>}
      </div>
      {aside && <div className="dash-chips">{aside}</div>}
    </section>
  );
}
