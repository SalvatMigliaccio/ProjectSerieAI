import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// IBM Plex Sans per titoli e testo, IBM Plex Mono per i numeri. Un carattere
// solo e il suo gemello monospaziato: Poppins nei titoli era la firma di ogni
// pagina "startup", e il corsivo Caveat non c'entrava con un prodotto di dati.
// Impacchettati con l'app e non presi da un CDN: la dashboard deve funzionare
// anche senza rete, e un font che cade in silenzio sul fallback di sistema
// cambia l'aspetto di tutta la pagina.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";

import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("manca #root in index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
