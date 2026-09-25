/**
 * The route guard.
 *
 * WHY IT WAITS. While the session check is in flight the answer is not "signed
 * out", it is "not known yet". Redirecting during that window shows the
 * sign-in screen on every reload, for a fraction of a second, to people who
 * are signed in — which reads as being logged out at random and is the classic
 * way this goes wrong.
 *
 * WHAT IT IS NOT. This is convenience, never enforcement: the API refuses
 * unauthenticated requests on its own. Anyone can edit the JavaScript; nobody
 * can edit the server's answer. If this file were deleted the data would still
 * be protected, and the only difference would be uglier error screens.
 */

import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { phase } = useAuth();
  const location = useLocation();

  if (phase === "checking") {
    return (
      <main id="contenuto" className="account" tabIndex={-1}>
        <section className="account-card">
          <p className="lead">Controllo la sessione…</p>
        </section>
      </main>
    );
  }

  if (phase === "anonymous") {
    // `state.from` so signing in returns to where they were heading rather
    // than dumping everyone on the dashboard.
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
