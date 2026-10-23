/**
 * La cornice della dashboard: testata, barra laterale, schermata, piede.
 *
 * QUATTRO SCHERMATE CON UN INDIRIZZO CIASCUNA (`/dashboard`,
 * `/dashboard/pronostici`, `/dashboard/squadra`, `/dashboard/stagione`), non
 * schede in memoria: un link porta dritto alla schermata, e il tasto indietro
 * del browser torna a quella di prima invece di uscire dalla dashboard.
 *
 * BARRA LATERALE SOPRA I 1024px, SCHEDE IN BASSO SOTTO. Una colonna di 15rem
 * su un telefono mangerebbe meta' schermo; in basso, con icona ed etichetta,
 * le quattro voci restano a portata di pollice. E' la stessa lista di link,
 * disegnata due volte dal CSS: un solo `<nav>`, una sola fonte delle voci.
 *
 * La griglia della giornata e le tabelle sono larghe: per questo la barra e'
 * stretta e il contenuto prende tutto il resto (`page--wide`).
 */

import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../../api/client";
import { useFavorite } from "../../auth/Favorite";
import { Colophon, Masthead } from "../../components/Layout";
import { useApi } from "../../hooks/useApi";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

const VOCI: { to: string; end?: boolean; label: string; breve: string; icon: ReactNode }[] = [
  {
    to: "/dashboard",
    end: true,
    label: "Giornata",
    breve: "Giornata",
    icon: (
      <Icon>
        <rect x="3" y="5" width="18" height="16" rx="2" />
        <path d="M8 3v4M16 3v4M3 11h18" />
      </Icon>
    ),
  },
  {
    to: "/dashboard/pronostici",
    label: "I miei pronostici",
    breve: "Pronostici",
    icon: (
      <Icon>
        <path d="M9 11l3 3 8-8" />
        <path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
      </Icon>
    ),
  },
  {
    to: "/dashboard/squadra",
    label: "La mia squadra",
    breve: "Squadra",
    icon: (
      <Icon>
        <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z" />
      </Icon>
    ),
  },
  {
    to: "/dashboard/stagione",
    label: "Stagione",
    breve: "Stagione",
    icon: (
      <Icon>
        <path d="M4 20h16M7 16v-5M12 16V7M17 16v-8" />
      </Icon>
    ),
  },
];

export function DashboardShell() {
  const { team } = useFavorite();
  // Solo per il piede: la versione del modello. Una chiamata leggera, gia'
  // fatta anche dalla schermata Stagione; la cache del browser non serve, la
  // risposta e' piccola.
  const season = useApi(() => api.season(), []);

  return (
    <div className="page--wide">
      <Masthead current="dashboard" />

      <div className="wrap dash-shell">
        <nav className="dash-nav" aria-label="Sezioni della dashboard">
          <p className="dash-nav__title">Dashboard</p>
          <ul>
            {VOCI.map((v) => (
              <li key={v.to}>
                <NavLink to={v.to} end={v.end} className="dash-nav__link">
                  {v.icon}
                  <span className="dash-nav__label">{v.label}</span>
                  <span className="dash-nav__short" aria-hidden="true">{v.breve}</span>
                  {v.to === "/dashboard/squadra" && team && (
                    <span className="dash-nav__team">{team}</span>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <main className="dash-main" id="contenuto" tabIndex={-1}>
          <Outlet />
        </main>
      </div>

      <Colophon modelVersion={season.data?.model_version ?? null} showApi />
    </div>
  );
}
