/**
 * La squadra preferita, per tutta l'applicazione.
 *
 * SALVATA NELL'ACCOUNT, NON NEL BROWSER: la si ritrova su ogni dispositivo.
 * Il contesto la legge una volta all'accesso e la tiene in memoria; chi la
 * cambia (il profilo) passa da `scegli`, che scrive sul server e aggiorna
 * tutti i componenti insieme — la dashboard evidenzia la squadra nuova senza
 * ricaricare.
 *
 * Senza accesso vale `null`, e nessun componente deve trattarlo come errore:
 * e' semplicemente "nessuna squadra da evidenziare".
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { me } from "../api/me";
import { useAuth } from "./AuthContext";

interface FavoriteValue {
  team: string | null;
  scegli: (team: string | null) => Promise<void>;
}

const FavoriteContext = createContext<FavoriteValue>({
  team: null,
  scegli: async () => undefined,
});

export function FavoriteProvider({ children }: { children: ReactNode }) {
  const { phase } = useAuth();
  const [team, setTeam] = useState<string | null>(null);

  useEffect(() => {
    if (phase !== "authenticated") {
      setTeam(null);
      return;
    }
    let annullato = false;
    me.preferences()
      .then((p) => {
        if (!annullato) setTeam(p.favorite_team);
      })
      .catch(() => {
        // Una preferenza che non si legge non deve rompere la pagina: la
        // dashboard resta identica, solo senza evidenziazione.
        if (!annullato) setTeam(null);
      });
    return () => {
      annullato = true;
    };
  }, [phase]);

  const scegli = useCallback(async (nuova: string | null) => {
    const saved = await me.setFavorite(nuova);
    setTeam(saved.favorite_team);
  }, []);

  const value = useMemo(() => ({ team, scegli }), [team, scegli]);
  return <FavoriteContext.Provider value={value}>{children}</FavoriteContext.Provider>;
}

export function useFavorite(): FavoriteValue {
  return useContext(FavoriteContext);
}
