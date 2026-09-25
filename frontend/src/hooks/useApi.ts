import { useEffect, useState } from "react";

import { ApiError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
}

/**
 * Carica una risorsa dell'API e tiene i tre stati che contano.
 *
 * TRE STATI E NON DUE. "Sto caricando", "e' andata male" e "non c'e' niente"
 * sono cose diverse, e una dashboard che le confonde mostra una pagina bianca
 * quando il server e' giu' — facendo cercare il problema nei dati, che stanno
 * benissimo. Qui l'errore e' un valore come gli altri, e va renderizzato.
 *
 * `deps` decide quando rifare la chiamata; il flag `cancelled` evita di
 * scrivere sullo stato di un componente gia' smontato.
 */
export function useApi<T>(load: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, loading: true });

    load()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          data: null,
          error: error instanceof Error ? error : new Error(String(error)),
          loading: false,
        });
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
