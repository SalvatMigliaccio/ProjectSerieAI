import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Poppins per i titoli e l'interfaccia (grottesco geometrico, pesante nei
// display), Inter per il testo e le cifre. Impacchettati con l'app e non presi
// da un CDN: la dashboard deve funzionare anche senza rete, e un font che cade
// in silenzio sul fallback di sistema cambia l'aspetto di tutta la pagina.
import "@fontsource/poppins/500.css";
import "@fontsource/poppins/600.css";
import "@fontsource/poppins/700.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
// Caveat serve a una cosa sola: la firma manoscritta in fondo alla hero.
import "@fontsource/caveat/600.css";

import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("manca #root in index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
