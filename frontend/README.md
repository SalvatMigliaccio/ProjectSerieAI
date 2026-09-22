# frontend/ — dashboard in sola lettura

React + TypeScript con Vite. Legge l'API in `backend/api/` e non scrive niente:
non esiste una chiamata che non sia una GET, ed è voluto — il track record si
scrive dai comandi locali, mai da un browser.

```
src/
  api/types.ts     i tipi del contratto, ricalcati da web/openapi.json
  api/client.ts    l'unico punto che fa fetch
  lib/format.ts    formattazione, e la regola "null si mostra vuoto, mai zero"
  hooks/useApi.ts  dati, errore, caricamento — tre stati, non due
  components/      StatusBar, RoundSelector, MatchTable, TrackRecordPanel, RpsChart
  pages/           Hero (presentazione) e Dashboard
```

**I comandi non stanno qui**, stanno in `COMANDI.md` — due elenchi divergono e
viene sempre letto quello sbagliato. In breve: `npm install` e poi `npm run dev`
dentro questa cartella, con l'API accesa su `http://127.0.0.1:8000`.

**L'URL dell'API** arriva da `VITE_API_BASE_URL` (copia `.env.example` in
`.env.local`). Si può anche passare al volo con `?api=https://...` nella query:
viene ricordato in `localStorage`, perché l'URL di ngrok cambia a ogni riavvio.

**La porta 5173 non è casuale**: è fra le origini CORS che l'API accetta per
default. Cambiandola, va aggiornata anche `AI_NAPLES_CORS_ORIGINS` lato backend.

**Le immagini** stanno in `public/` e sono servite alla radice (`/hero-bg.jpg`).
Le specifiche sono in `public/.gitkeep`. Senza l'immagine la hero resta
leggibile: il fondale dipinto in CSS è già un fondale.
