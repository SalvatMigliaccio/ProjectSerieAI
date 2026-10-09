import { type MouseEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { baseUrl } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { scorrimento } from "../lib/motion";

/**
 * Un collegamento a una sezione della landing, che funziona anche dalla
 * dashboard.
 *
 * PERCHE' NON BASTA `<a href="#come-funziona">`. Il router usa l'hash per le
 * rotte (`#/dashboard`): un'ancora scritta a mano sovrascrive quella rotta e
 * la pagina finisce sulla landing in cima, senza scorrere da nessuna parte —
 * dalla dashboard si perdeva anche la giornata selezionata. Qui si naviga
 * prima e si scorre dopo, quando il nodo esiste davvero: due `rAF` perche' al
 * primo la landing e' montata ma non ancora impaginata.
 */
function SectionLink({
  id,
  children,
  on = false,
}: {
  id: string;
  children: ReactNode;
  on?: boolean;
}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const vai = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    const scorri = () =>
      document.getElementById(id)?.scrollIntoView({ behavior: scorrimento(), block: "start" });

    if (pathname === "/") {
      scorri();
      return;
    }

    navigate("/");

    // Non basta aspettare un fotogramma: la landing si monta subito ma cresce
    // mentre arrivano i dati dall'API, e scorrere in quel momento porta nel
    // posto sbagliato — provato, si restava fermi in cima. Si aspetta che la
    // posizione della sezione smetta di muoversi, con un tetto di 2 secondi
    // per non restare appesi se l'API non risponde.
    const scadenza = performance.now() + 2000;
    let precedente = Number.NaN;
    const quandoFerma = () => {
      const nodo = document.getElementById(id);
      const posizione = nodo ? Math.round(nodo.getBoundingClientRect().top + window.scrollY) : Number.NaN;
      if (nodo && posizione === precedente) {
        scorri();
        return;
      }
      precedente = posizione;
      if (performance.now() < scadenza) requestAnimationFrame(quandoFerma);
      else scorri();
    };
    requestAnimationFrame(quandoFerma);
  };

  return (
    <a
      href={`#${id}`}
      onClick={vai}
      className={on ? "on" : undefined}
      aria-current={on ? "location" : undefined}
    >
      {children}
    </a>
  );
}

/**
 * Le sezioni della landing, nell'ordine in cui compaiono, e la voce di menu
 * che accendono.
 *
 * "Statistiche" e "Partite" puntano alla STESSA sezione (`#statistiche`
 * contiene la lista delle partite, `#partite` e' il suo titolo): dentro quella
 * sezione si accende "Partite", che e' il nome di cio' che vi si vede.
 */
const SEZIONI: ReadonlyArray<{ elemento: string; voce: string }> = [
  { elemento: "come-funziona", voce: "come-funziona" },
  { elemento: "statistiche", voce: "partite" },
  { elemento: "per-chi", voce: "per-chi" },
  { elemento: "faq", voce: "faq" },
];

/**
 * Quale sezione della landing si sta leggendo: "home" sopra la prima.
 *
 * LA LINEA E' IL BORDO BASSO DELLA TESTATA, letto da `scroll-padding-top`
 * invece che scritto qui: e' lo stesso punto in cui le ancore portano il
 * titolo di una sezione, quindi cliccare una voce accende proprio quella voce.
 * Gli 8 pixel di tolleranza coprono gli arrotondamenti dello scorrimento.
 *
 * IN FONDO ALLA PAGINA VINCE L'ULTIMA. Le FAQ sono piu' corte dello schermo e
 * il loro titolo non arriva mai alla linea: senza questa regola "FAQ" non si
 * accenderebbe mai, nemmeno dopo averla cliccata.
 *
 * Ricalcolo su scorrimento, ridimensionamento e cambio di altezza della
 * pagina: la landing cresce mentre arrivano i dati dall'API, e le sezioni si
 * spostano senza che nessuno scorra. Un fotogramma per volta, non un calcolo
 * per evento.
 */
function useSezioneAttiva(attivo: boolean): string | null {
  const [voce, setVoce] = useState<string | null>(attivo ? "home" : null);

  useEffect(() => {
    if (!attivo) {
      setVoce(null);
      return;
    }

    let fotogramma = 0;
    const calcola = () => {
      fotogramma = 0;
      const radice = document.documentElement;
      const linea = (parseFloat(getComputedStyle(radice).scrollPaddingTop) || 0) + 8;
      const inFondo = window.innerHeight + window.scrollY >= radice.scrollHeight - 2;

      let trovata = "home";
      for (const { elemento, voce: v } of SEZIONI) {
        const nodo = document.getElementById(elemento);
        if (!nodo) continue;
        if (nodo.getBoundingClientRect().top <= linea) trovata = v;
        if (inFondo) trovata = v;
      }
      setVoce(trovata);
    };
    const pianifica = () => {
      if (fotogramma === 0) fotogramma = requestAnimationFrame(calcola);
    };

    calcola();
    window.addEventListener("scroll", pianifica, { passive: true });
    window.addEventListener("resize", pianifica);
    const osservatore = new ResizeObserver(pianifica);
    osservatore.observe(document.body);

    return () => {
      window.removeEventListener("scroll", pianifica);
      window.removeEventListener("resize", pianifica);
      osservatore.disconnect();
      if (fotogramma !== 0) cancelAnimationFrame(fotogramma);
    };
  }, [attivo]);

  return voce;
}

/**
 * "Salta al contenuto": il primo elemento raggiungibile da tastiera.
 *
 * Invisibile finche' non riceve il fuoco, poi compare in alto a sinistra. Chi
 * naviga con la tastiera altrimenti attraversa marchio e menu a ogni pagina
 * prima di arrivare a qualcosa da leggere. Non e' un'ancora semplice per lo
 * stesso motivo di `SectionLink`: il router usa l'hash per le rotte, e
 * `href="#contenuto"` porterebbe alla landing invece che al contenuto.
 */
function SkipLink() {
  return (
    <a
      className="skip-link"
      href="#contenuto"
      onClick={(event) => {
        event.preventDefault();
        const nodo = document.getElementById("contenuto");
        nodo?.focus();
        nodo?.scrollIntoView({ behavior: scorrimento(), block: "start" });
      }}
    >
      Salta al contenuto
    </a>
  );
}

/**
 * Testata: marchio a sinistra, menu al centro, stato a destra.
 *
 * ACCEDI / ESCI, dal 25 settembre 2026. Fino alla fase 2 qui c'era scritto che
 * l'autenticazione non doveva esistere, perche' l'API era in sola lettura. Ora
 * i dati sono dietro una sessione; quello che NON e' cambiato e' il track
 * record, che resta scrivibile solo dai comandi locali — nessuna rotta HTTP lo
 * tocca, e `_assert_read_only_routes` lo verifica all'avvio.
 *
 * LE VOCI DEL MENU PUNTANO A SEZIONI CHE ESISTONO. "Come funziona",
 * "Statistiche", "Partite" e "FAQ" sono ancore della landing. "Partite" porta
 * alla lista della giornata nella landing e non alla dashboard: dal 9 ottobre
 * 2026 la dashboard chiede l'accesso, e la voce di menu non deve rimbalzare
 * chi non e' entrato sulla pagina di login. Alla dashboard porta il bottone a
 * destra.
 * Una voce che apre il vuoto e' una promessa rotta al primo clic.
 */
/**
 * Sign in, or sign out, depending on who is looking.
 *
 * Renders nothing while the session is still being checked. A control that
 * says "Accedi" for a moment and then flips to "Esci" is a flicker on every
 * reload, and it reads as the account dropping out by itself.
 */
function AccountLink() {
  const { phase, user } = useAuth();

  if (phase === "checking") return null;
  if (phase === "anonymous") return <Link to="/sign-in">Accedi</Link>;

  // PORTA ALL'AREA ACCOUNT, non esegue l'uscita. Un solo elemento in testata
  // che disconnette al primo clic e' un'azione distruttiva senza conferma nel
  // posto dove si clicca per sbaglio; e non lasciava nessun punto in cui
  // cambiare la password o vedere le sessioni aperte.
  return (
    <Link className="nav__account" to="/account" title={user?.email}>
      Account
    </Link>
  );
}

export function Masthead({ current }: { current: "hero" | "dashboard" }) {
  // Si guarda il percorso e non `current`: account e accesso passano "hero"
  // pur non essendo la landing, e li' nessuna sezione e' "quella che leggi".
  const { pathname } = useLocation();
  const attiva = useSezioneAttiva(pathname === "/");

  // SU TELEFONO IL MENU SCORRE DI LATO, e la voce accesa puo' stare fuori
  // dallo schermo: la si porta in vista. Con `scrollTo` sul menu e non con
  // `scrollIntoView`, che sposterebbe anche la pagina in verticale — cioe'
  // proprio lo scorrimento che ha acceso la voce.
  const menu = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = menu.current;
    const voce = nav?.querySelector<HTMLElement>("a.on");
    if (!nav || !voce || nav.scrollWidth <= nav.clientWidth) return;
    const sinistra = voce.offsetLeft - nav.offsetLeft;
    const fuori = sinistra < nav.scrollLeft || sinistra + voce.offsetWidth > nav.scrollLeft + nav.clientWidth;
    if (fuori) {
      nav.scrollTo({
        left: sinistra - (nav.clientWidth - voce.offsetWidth) / 2,
        behavior: scorrimento(),
      });
    }
  }, [attiva]);

  return (
    <header className="masthead">
      <SkipLink />
      <div className="wrap masthead__inner">
        <Link className="brand" to="/">
          {/* I file di `public/` sono serviti ALLA RADICE: il percorso e'
              `/Logo_NoName.png`, non `public/...`. Con il prefisso si
              risolverebbe relativo all'URL corrente e si romperebbe appena
              cambia rotta o in build. */}
          <img className="brand__logo" src="/Logo_NoName.png" alt="" width={331} height={333} />
          <span>
            <span className="brand__name">
              Match<em>Point</em>
            </span>
            <span className="brand__tag">ogni partita conta</span>
          </span>
        </Link>

        <nav className="nav" ref={menu}>
          <Link to="/" className={attiva === "home" ? "on" : undefined}
            aria-current={pathname === "/" ? "page" : undefined}>
            Home
          </Link>
          <SectionLink id="come-funziona" on={attiva === "come-funziona"}>Come funziona</SectionLink>
          <SectionLink id="statistiche">Statistiche</SectionLink>
          <SectionLink id="partite" on={attiva === "partite"}>Partite</SectionLink>
          <SectionLink id="per-chi" on={attiva === "per-chi"}>Per chi e'</SectionLink>
          <SectionLink id="faq" on={attiva === "faq"}>FAQ</SectionLink>
          <AccountLink />
        </nav>

        {/* L'angolo in alto a destra e' il posto dell'azione, non di un dato:
            una data li' e' informazione che nessuno cerca in quel punto, e la
            stessa informazione e' gia' nel piede della pagina. */}
        <Link className="nav-cta" to="/dashboard"
          aria-current={current === "dashboard" ? "page" : undefined}>
          <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
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

const REPOSITORY = "https://github.com/SalvatMigliaccio/ProjectSerieAI";
const CONTATTO = "salvatore.migliaccio998@gmail.com";

/**
 * Il piede della pagina.
 *
 * LA VERSIONE DEL MODELLO ARRIVA DA `/api/season`: `/api/health` nel contratto
 * non la espone, e prenderla da li' avrebbe voluto dire inventare un campo.
 *
 * LO STATO DEL SISTEMA STA QUI E NON IN TESTATA. Il modello e l'indirizzo
 * dell'API sono informazioni che si cercano quando si vuole verificare
 * qualcosa, non mentre si legge: il posto giusto e' la fine.
 *
 * L'AVVERTENZA NON E' UN ADEMPIMENTO, E' LA STESSA COSA CHE DICE IL MODELLO.
 * Su 1140 partite fuori campione il valore atteso e' -5.2% su ogni riga: dire
 * che sono stime e non consigli di gioco e' la lettura corretta dei numeri che
 * la pagina mostra, non una formula messa li' per prudenza.
 */
export function Colophon({
  modelVersion,
  showApi = false,
}: {
  modelVersion: string | null;
  showApi?: boolean;
}) {
  return (
    <footer className="colophon">
      <div className="wrap colophon__grid">
        <div className="colophon__brand">
          <span className="brand">
            <img className="brand__logo" src="/Logo_NoName.png" alt="" width={331} height={333} />
            <span>
              <span className="brand__name">
                Match<em>Point</em>
              </span>
              <span className="brand__tag">ogni partita conta</span>
            </span>
          </span>
          <p className="colophon__pitch">
            Previsioni sui gol della Serie A a partire dalle quote di apertura,
            registrate prima del calcio d'inizio e misurate una giornata alla
            volta.
          </p>
        </div>

        <nav className="colophon__col" aria-label="pagine">
          <h3>Pagine</h3>
          <Link to="/">Home</Link>
          <SectionLink id="partite">Partite</SectionLink>
          <SectionLink id="come-funziona">Come funziona</SectionLink>
          <SectionLink id="statistiche">Statistiche</SectionLink>
          <SectionLink id="per-chi">Per chi e'</SectionLink>
          <SectionLink id="faq">FAQ</SectionLink>
        </nav>

        <div className="colophon__col">
          <h3>Progetto</h3>
          <a href={REPOSITORY} target="_blank" rel="noreferrer noopener">
            Codice su GitHub
          </a>
          {/* `mailto:` e non un modulo: non c'e' un server che possa ricevere
              un invio, e un modulo che non spedisce e' peggio di nessun modulo. */}
          <a href={`mailto:${CONTATTO}`}>{CONTATTO}</a>
        </div>

        <div className="colophon__col colophon__col--stato">
          <h3>Sistema</h3>

          {/* DUE MODELLI, E LA RIGA PICCOLA DICE QUALE DEI DUE PARLA. I numeri
              che si vedono in pagina escono tutti da M1: il LightGBM esiste,
              e' misurato, ma sul test set e' indistinguibile dal mercato e per
              questo non e' promosso. Elencarli senza distinguerli lascerebbe
              credere che le previsioni mostrate vengano da entrambi. */}
          <span className="colophon__modello">
            Modello M1 Market-Only
            <em>{modelVersion ?? "quote di apertura de-viggate"} · in produzione</em>
          </span>
          <span className="colophon__modello">
            Modello Machine Learning LightGBM
            <em>M5 ancorato al mercato · in valutazione</em>
          </span>

          {/* `baseUrl()` e' vuota quando l'API sta sulla stessa origine della
              pagina, che dalla fase 4 e' il caso normale: senza questo il
              piede mostrava "API" e basta. L'indirizzo che serve a chi
              verifica e' comunque quello da cui parte la richiesta. */}
          {showApi && (
            <span className="colophon__api">API {baseUrl() || window.location.origin}</span>
          )}
        </div>
      </div>

      <div className="wrap colophon__rule" />

      <div className="wrap colophon__bottom">
        <p>
          Le probabilità sono <strong>stime statistiche</strong>, non consigli di
          gioco: nessuna garanzia di vincita. Gioca responsabilmente,{" "}
          <strong>18+</strong>.
        </p>
        <p className="colophon__meta">
          MatchPoint · Serie A {new Date().getFullYear()} · dati football-data.co.uk,
          FBref e Understat
        </p>
      </div>
    </footer>
  );
}
