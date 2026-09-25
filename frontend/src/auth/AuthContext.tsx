/**
 * Who is signed in, for the whole application.
 *
 * THREE STATES, NOT TWO, for the same reason the data hooks have three:
 * "checking", "signed in" and "signed out" are different things. An interface
 * that collapses the first into the third flashes the sign-in screen on every
 * reload before the session check comes back — which looks exactly like being
 * logged out at random, and is the single most common way this goes wrong.
 *
 * NO TOKEN IS STORED HERE. The session is an HttpOnly cookie the browser sends
 * on its own. Keeping a copy in localStorage would hand an XSS bug the thing
 * the cookie flag exists to protect.
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

import * as authApi from "../api/auth";
import type { Identity } from "../api/auth";
import { UNAUTHENTICATED_EVENT } from "../api/client";

type Phase = "checking" | "authenticated" | "anonymous";

interface AuthValue {
  phase: Phase;
  user: Identity | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Adopt an identity the server already returned, e.g. after verifying. */
  adopt: (user: Identity) => void;
  /** Re-ask the server. Used when a request comes back 401. */
  refresh: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [user, setUser] = useState<Identity | null>(null);

  const refresh = useCallback(async () => {
    const found = await authApi.me();
    setUser(found);
    setPhase(found ? "authenticated" : "anonymous");
  }, []);

  useEffect(() => {
    let cancelled = false;
    void authApi.me().then((found) => {
      if (cancelled) return;
      setUser(found);
      setPhase(found ? "authenticated" : "anonymous");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // A 401 from any data request means the session ended on the server side:
  // expired, revoked, or the account was suspended. The interface has to agree
  // with the server rather than keep showing a signed-in shell full of errors.
  useEffect(() => {
    const onUnauthenticated = () => {
      setUser(null);
      setPhase("anonymous");
    };
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauthenticated);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const identity = await authApi.signIn(email, password);
    setUser(identity);
    setPhase("authenticated");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.signOut();
    } finally {
      // Local state clears even if the call failed. The server may already
      // have dropped the session; leaving the interface pretending otherwise
      // is worse than an optimistic sign-out.
      setUser(null);
      setPhase("anonymous");
    }
  }, []);

  const adopt = useCallback((identity: Identity) => {
    setUser(identity);
    setPhase("authenticated");
  }, []);

  const can = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user],
  );

  const value = useMemo(
    () => ({ phase, user, signIn, signOut, adopt, refresh, can }),
    [phase, user, signIn, signOut, adopt, refresh, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (value === null) {
    // A clear error beats `undefined.phase` three components deeper.
    throw new Error("useAuth used outside AuthProvider");
  }
  return value;
}
