import { HashRouter, Route, Routes } from "react-router-dom";

import { Dashboard } from "./pages/Dashboard";
import { Hero } from "./pages/Hero";

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
      <Routes>
        <Route path="/" element={<Hero />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="*" element={<Hero />} />
      </Routes>
    </HashRouter>
  );
}
