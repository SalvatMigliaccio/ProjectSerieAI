import { useEffect, useRef } from "react";
import { HashRouter, Route, Routes, useLocation } from "react-router-dom";

import { Dashboard } from "./pages/Dashboard";
import { Hero } from "./pages/Hero";

const TITOLI: Record<string, string> = {
  "/": "MatchPoint — previsioni Serie A",
  "/dashboard": "Dashboard · MatchPoint",
};

/**
 * Cosa succede quando cambia pagina.
 *
 * FUOCO. In un'applicazione a pagina singola il browser non sa che la pagina
 * e' cambiata: il fuoco resta sul link appena premuto, che nella pagina nuova
 * non esiste piu', e chi usa uno screen reader non sente niente. Qui il fuoco
 * va al contenuto principale (`#contenuto`) e il titolo della scheda cambia,
 * che e' la prima cosa che uno screen reader annuncia.
 *
 * IN CIMA. Arrivando dalla landing scorsa a meta', la dashboard si apriva
 * alla stessa altezza — in mezzo alla classifica invece che sulla giornata.
 *
 * Solo sul cambio di percorso, non sui parametri: cambiare giornata nella
 * dashboard (`?g=5`) non e' una pagina nuova, e riportare in cima a ogni clic
 * sarebbe il contrario di cio' che serve. Al primo caricamento non tocca
 * niente: il browser sta gia' facendo la cosa giusta.
 */
function CambioPagina() {
  const { pathname } = useLocation();
  // Si confronta il percorso, non si contano le esecuzioni. La prima versione
  // teneva un segnaposto "primo caricamento": ma in sviluppo `StrictMode`
  // esegue ogni effetto due volte, la seconda trovava il segnaposto gia'
  // consumato e portava il fuoco sul contenuto appena aperta la pagina — il
  // primo Tab saltava marchio, menu e proprio il link "salta al contenuto".
  const ultimo = useRef(pathname);

  useEffect(() => {
    document.title = TITOLI[pathname] ?? TITOLI["/"] ?? "MatchPoint";
    if (ultimo.current === pathname) return;
    ultimo.current = pathname;
    window.scrollTo({ top: 0 });
    document.getElementById("contenuto")?.focus({ preventScroll: true });
  }, [pathname]);

  return null;
}

/**
 * Due schermate.
 *
 * HashRouter e non BrowserRouter: il sito e' statico e puo' finire su un
 * qualsiasi host senza regole di rewrite. Con i path veri, ricaricare
 * `/dashboard` darebbe 404 su chiunque non sia configurato apposta; con
 * l'hash funziona ovunque, anche aprendo il `dist/` da un server banale.
 */
export function App() {
  return (
    <HashRouter>
      <CambioPagina />
      <Routes>
        <Route path="/" element={<Hero />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="*" element={<Hero />} />
      </Routes>
    </HashRouter>
  );
}
