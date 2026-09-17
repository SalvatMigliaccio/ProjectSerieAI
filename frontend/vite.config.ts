import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// La porta 5173 non e' un caso: e' fra le origini che l'API accetta per
// default (vedi allowed_origins in backend/api/__init__.py), quindi in
// sviluppo il CORS funziona senza configurare niente. Cambiandola, va
// aggiornata anche AI_NAPLES_CORS_ORIGINS lato backend.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { outDir: "dist", sourcemap: true },
});
