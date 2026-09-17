import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { baseUrl } from "../api/client";
import { localDateTime } from "../lib/format";

/**
 * Testata: marchio a sinistra, menu al centro, stato a destra.
 *
 * NIENTE "ACCEDI". Non esiste autenticazione e non deve esistere: l'API e' in
 * sola lettura e il track record si scrive solo da processi locali. Al suo
 * posto, nello stesso angolo, una pastiglia che dice quando i dati sono stati
 * aggiornati — informazione vera al posto di un bottone che non porta da
 * nessuna parte.
 *
 * LE VOCI DEL MENU PUNTANO A SEZIONI CHE ESISTONO. "Come funziona",
 * "Statistiche" e "FAQ" sono ancore della landing; "Partite" e' la dashboard.
 * Una voce che apre il vuoto e' una promessa rotta al primo clic.
 */
export function Masthead({
  current,
  updatedAt = null,
}: {
  current: "hero" | "dashboard";
  updatedAt?: string | null;
}) {
  return (
    <header className="masthead">
      <div className="wrap masthead__inner">
        <Link className="brand" to="/">
          <span className="brand__mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
              <path
                d="M12 7.5l3.2 2.3-1.2 3.7h-4l-1.2-3.7L12 7.5z"
                fill="currentColor"
              />
            </svg>
          </span>
          <span>
            <span className="brand__name">
              Match<em>Point</em>
            </span>
            <span className="brand__tag">ogni partita conta</span>
          </span>
        </Link>

        <nav className="nav">
          <Link to="/" className={current === "hero" ? "on" : undefined}>
            Home
          </Link>
          <a href="#come-funziona">Come funziona</a>
          <a href="#statistiche">Statistiche</a>
          <Link to="/dashboard" className={current === "dashboard" ? "on" : undefined}>
            Partite
          </Link>
          <a href="#faq">FAQ</a>
        </nav>

        <span className="status__signal">
          <i className="dot dot--ok" aria-hidden="true" />
          <span className="small muted">
            {updatedAt ? localDateTime(updatedAt).slice(0, 10) : "dati locali"}
          </span>
        </span>
      </div>
    </header>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="eyebrow">{children}</p>;
}

/**
 * Colophon. La versione del modello arriva da `/api/season`: `/api/health` nel
 * contratto non la espone, e prenderla da li' avrebbe voluto dire inventare un
 * campo.
 */
export function Colophon({
  modelVersion,
  updatedAt,
  showApi = false,
}: {
  modelVersion: string | null;
  updatedAt: string | null;
  showApi?: boolean;
}) {
  return (
    <footer className="colophon">
      <div className="wrap colophon__inner">
        {modelVersion && <span>modello {modelVersion}</span>}
        {updatedAt && <span>aggiornato {localDateTime(updatedAt)}</span>}
        {showApi && <span>api {baseUrl()}</span>}
      </div>
    </footer>
  );
}
