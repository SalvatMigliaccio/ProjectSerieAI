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
export function Masthead({ current }: { current: "hero" | "dashboard" }) {
  return (
    <header className="masthead">
      <div className="wrap masthead__inner">
        <Link className="brand" to="/">
          {/* I file di `public/` sono serviti ALLA RADICE: il percorso e'
              `/Logo_NoName.png`, non `public/...`. Con il prefisso si
              risolverebbe relativo all'URL corrente e si romperebbe appena
              cambia rotta o in build. */}
          <img className="brand__logo" src="/Logo_NoName.png" alt="" />
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

        {/* L'angolo in alto a destra e' il posto dell'azione, non di un dato:
            una data li' e' informazione che nessuno cerca in quel punto, e la
            stessa informazione e' gia' nel piede della pagina. */}
        <Link className="nav-cta" to="/dashboard">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
            <rect x="3" y="3" width="7" height="9" rx="1.5" />
            <rect x="14" y="3" width="7" height="5" rx="1.5" />
            <rect x="14" y="12" width="7" height="9" rx="1.5" />
            <rect x="3" y="16" width="7" height="5" rx="1.5" />
          </svg>
          Dashboard
        </Link>
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
