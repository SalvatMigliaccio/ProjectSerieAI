import type { ReactNode } from "react";

import { ApiError, baseUrl } from "../api/client";

export function Loading({ what }: { what: string }) {
  return (
    <p className="state" role="status">
      {what}
    </p>
  );
}

/**
 * Errore: cosa non ha funzionato, contro quale indirizzo, e cosa guardare.
 *
 * Il caso tipico non e' un difetto del frontend: e' il server spento o il
 * tunnel scaduto. Dirlo qui risparmia mezz'ora cercata nel posto sbagliato.
 */
export function ErrorState({ error, what }: { error: Error; what: string }) {
  const hint = error instanceof ApiError ? error.hint : null;
  return (
    <div className="state state--error" role="alert">
      <strong>{what}</strong>
      {error.message} — <code>{baseUrl()}</code>
      {hint && (
        <>
          <br />
          {hint}
        </>
      )}
      {/* error-recovery: un errore senza una via d'uscita lascia solo il tasto
          indietro. Il caso tipico e' il server riavviato o il tunnel che
          torna su: ricaricare e' esattamente la cosa giusta da fare. */}
      <button type="button" className="state__retry" onClick={() => window.location.reload()}>
        Riprova
      </button>
    </div>
  );
}

/** Vuoto legittimo: non c'e' niente da mostrare, e non e' un guasto. */
export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="state">{children}</p>;
}

export function Skeleton({ width = "5rem" }: { width?: string }) {
  return <span className="skeleton" style={{ width }} aria-hidden="true" />;
}
