import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Isola un blocco: se quel pezzo esplode, il resto della pagina resta in piedi.
 *
 * PERCHE' SERVE QUI. La pagina legge un'API che puo' essere piu' vecchia del
 * frontend — basta un backend non riavviato e un campo nuovo manca. Senza
 * confine, un `undefined.map` in una scheda laterale porta via l'intera
 * schermata e sembra che sia rotto tutto; con il confine, sparisce quella
 * scheda e si legge il perche'.
 *
 * NON NASCONDE L'ERRORE. Lo scrive in console, perche' un guasto silenzioso e'
 * il modo piu' rapido di lasciarlo in giro per settimane.
 */
interface Props {
  children: ReactNode;
  /** Cosa mostrare al posto del blocco. Omesso: non mostra niente. */
  fallback?: ReactNode;
  /** Nome del blocco, per il messaggio in console. */
  label?: string;
}

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[${this.props.label ?? "blocco"}] errore non gestito:`, error, info);
  }

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
