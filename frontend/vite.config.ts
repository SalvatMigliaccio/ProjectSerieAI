import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// La porta 5173 non e' un caso: e' fra le origini che l'API accetta per
// default (vedi allowed_origins in backend/api/__init__.py), quindi in
// sviluppo il CORS funziona senza configurare niente. Cambiandola, va
// aggiornata anche AI_NAPLES_CORS_ORIGINS lato backend.
export default defineConfig({
  plugins: [react()],
  server: {
    // In ascolto su tutte le interfacce, non solo su `localhost`. Su Windows
    // `localhost` si risolve in `::1` e basta: `netstat` mostrava il server in
    // ascolto su `[::1]:5173` e NIENTE su `127.0.0.1:5173`, quindi un tunnel
    // che si collega all'IPv4 di loopback — quasi tutti lo fanno — si prendeva
    // un "connection refused" mentre dal browser di casa il sito funzionava.
    // Il prezzo e' che il server di sviluppo e' raggiungibile anche dalla rete
    // locale finche' resta acceso; `allowedHosts` qui sotto limita comunque i
    // nomi accettati.
    host: true,
    port: 5173,
    strictPort: true,

    // IL PROXY ESISTE PER IL TUNNEL, e risolve due problemi in una riga.
    // Quando si espone il frontend a qualcuno fuori casa, `127.0.0.1:8000` e'
    // il computer di CHI GUARDA, non il nostro: le chiamate all'API cadono
    // tutte. Passando da qui, il browser chiede `/api/...` alla stessa origine
    // della pagina — quindi un solo tunnel basta, e il CORS non entra nemmeno
    // in gioco perche' non c'e' piu' una seconda origine.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/openapi.json": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },

    // Vite rifiuta le richieste con un Host che non conosce ("Blocked request.
    // This host is not allowed"), e un tunnel ne porta sempre uno nuovo. Qui si
    // aprono i domini dei servizi di tunneling, non tutti: `true` accetterebbe
    // qualunque Host, compreso quello di un attacco DNS rebinding verso il
    // server di sviluppo in ascolto sulla macchina.
    // ngrok ha cambiato dominio piu' volte: gli URL gratuiti di oggi finiscono
    // in `.ngrok-free.dev`, quelli di ieri in `.ngrok-free.app`, quelli di
    // prima ancora in `.ngrok.io`. Ci sono tutti, perche' il sintomo di uno
    // mancante e' un messaggio che sembra un errore di configurazione
    // ("Blocked request") proprio mentre qualcuno sta guardando.
    allowedHosts: [
      ".ngrok-free.dev",
      ".ngrok.dev",
      ".ngrok-free.app",
      ".ngrok.app",
      ".ngrok.io",
      ".trycloudflare.com",
      ".loca.lt",
      ".serveo.net",
    ],
  },
  build: { outDir: "dist", sourcemap: true },
});
