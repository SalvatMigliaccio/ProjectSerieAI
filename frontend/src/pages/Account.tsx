/**
 * Every screen that deals with credentials: sign in, sign up, verify, reset.
 *
 * They live in one file because they are the same form four times over, and
 * splitting them would mostly duplicate the shell. Anything larger than this
 * belongs in its own file.
 *
 * WHAT THESE SCREENS DELIBERATELY DO NOT DO: tell the visitor whether an
 * address is registered. Signing up with a taken address and asking to reset
 * an unknown one both end on the same "check your mailbox" panel, because the
 * API answers identically in both cases. Showing "this address already exists"
 * would undo that server-side care in the interface.
 */

import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import * as authApi from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const MIN_PASSWORD = 12;

function message(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return fallback;
}

/** The shared frame: title, optional lead, the form, and a footer of links. */
function Shell({
  title,
  lead,
  children,
}: {
  title: string;
  lead?: string;
  children: ReactNode;
}) {
  return (
    <main id="contenuto" className="account" tabIndex={-1}>
      <section className="account-card">
        <h1>{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
        {children}
      </section>
    </main>
  );
}

function Problem({ text }: { text: string | null }) {
  if (!text) return null;
  // `role="alert"` so a screen reader announces it: a message that only
  // appears visually is invisible to whoever most needs it.
  return (
    <p className="account-error" role="alert">
      {text}
    </p>
  );
}

// ---------------------------------------------------------------------------

export function SignIn() {
  const { signIn, phase } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (phase === "authenticated") navigate("/dashboard", { replace: true });
  }, [phase, navigate]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(message(err, "accesso non riuscito"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="Accedi">
      <form onSubmit={submit} noValidate>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <Problem text={error} />
        <button className="cta" type="submit" disabled={busy}>
          {busy ? "Accesso…" : "Accedi"}
        </button>
      </form>

      <Shell.Footer>
        <Link to="/forgot-password">Password dimenticata?</Link>
        {" · "}
        <Link to="/sign-up">Crea un account</Link>
      </Shell.Footer>
    </Shell>
  );
}

// A tiny helper so each screen can pass footer links as children without
// threading a prop through every call.
Shell.Footer = function Footer({ children }: { children: ReactNode }) {
  return <p className="account-links">{children}</p>;
};

// ---------------------------------------------------------------------------

export function SignUp() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.signUp(email, password);
      setDone(true);
    } catch (err) {
      setError(message(err, "registrazione non riuscita"));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Shell
        title="Controlla la posta"
        lead={
          "Se l'indirizzo è valido ti arriva un messaggio con il collegamento " +
          "per attivare l'account. Guarda anche nello spam."
        }
      >
        <Shell.Footer>
          <Link to="/sign-in">Torna all'accesso</Link>
        </Shell.Footer>
      </Shell>
    );
  }

  return (
    <Shell title="Crea un account">
      <form onSubmit={submit} noValidate>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <p className="faint">
          Almeno {MIN_PASSWORD} caratteri. Nessun obbligo di maiuscole o
          simboli: una frase lunga è più robusta di «Password1!».
        </p>

        <Problem text={error} />
        <button className="cta" type="submit" disabled={busy}>
          {busy ? "Invio…" : "Crea l'account"}
        </button>
      </form>

      <Shell.Footer>
        <Link to="/sign-in">Hai già un account?</Link>
      </Shell.Footer>
    </Shell>
  );
}

// ---------------------------------------------------------------------------

export function VerifyEmail() {
  const [params] = useSearchParams();
  const { adopt } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const token = params.get("token");

  useEffect(() => {
    if (!token) {
      setError("collegamento incompleto: manca il codice di verifica");
      return;
    }
    let cancelled = false;
    void authApi
      .verifyEmail(token)
      .then((identity) => {
        if (cancelled) return;
        // Verifying signs the user in, so there is nothing else to ask for.
        adopt(identity);
        navigate("/dashboard", { replace: true });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(message(err, "verifica non riuscita"));
      });
    return () => {
      cancelled = true;
    };
  }, [token, adopt, navigate]);

  return (
    <Shell
      title={error ? "Collegamento non valido" : "Attivazione in corso…"}
      lead={
        error
          ? "Il collegamento è scaduto o è già stato usato. Richiedine uno nuovo registrandoti di nuovo con lo stesso indirizzo."
          : undefined
      }
    >
      <Problem text={error} />
      {error ? (
        <Shell.Footer>
          <Link to="/sign-up">Richiedi un nuovo collegamento</Link>
          {" · "}
          <Link to="/sign-in">Accedi</Link>
        </Shell.Footer>
      ) : null}
    </Shell>
  );
}

// ---------------------------------------------------------------------------

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.forgotPassword(email);
      setDone(true);
    } catch (err) {
      setError(message(err, "richiesta non riuscita"));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Shell
        title="Controlla la posta"
        lead={
          "Se l'indirizzo è registrato ti arriva un collegamento per scegliere " +
          "una nuova password. Vale 30 minuti e si può usare una volta sola."
        }
      >
        <Shell.Footer>
          <Link to="/sign-in">Torna all'accesso</Link>
        </Shell.Footer>
      </Shell>
    );
  }

  return (
    <Shell
      title="Password dimenticata"
      lead="Inserisci l'indirizzo con cui ti sei registrato."
    >
      <form onSubmit={submit} noValidate>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <Problem text={error} />
        <button className="cta" type="submit" disabled={busy}>
          {busy ? "Invio…" : "Mandami il collegamento"}
        </button>
      </form>

      <Shell.Footer>
        <Link to="/sign-in">Torna all'accesso</Link>
      </Shell.Footer>
    </Shell>
  );
}

// ---------------------------------------------------------------------------

export function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== repeat) {
      setError("le due password non coincidono");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await authApi.resetPassword(token, password);
      // No session is issued by the reset, by design: whoever changed the
      // password proves it by signing in with it.
      navigate("/accedi", { replace: true });
    } catch (err) {
      setError(message(err, "reimpostazione non riuscita"));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <Shell
        title="Collegamento non valido"
        lead="Manca il codice. Richiedi un nuovo collegamento."
      >
        <Shell.Footer>
          <Link to="/forgot-password">Richiedilo di nuovo</Link>
        </Shell.Footer>
      </Shell>
    );
  }

  return (
    <Shell title="Scegli una nuova password">
      <form onSubmit={submit} noValidate>
        <label htmlFor="password">Nuova password</label>
        <input
          id="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <label htmlFor="repeat">Ripetila</label>
        <input
          id="repeat"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD}
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
        />
        <p className="faint">
          Almeno {MIN_PASSWORD} caratteri. Cambiandola escono tutte le sessioni
          aperte, questa compresa.
        </p>

        <Problem text={error} />
        <button className="cta" type="submit" disabled={busy}>
          {busy ? "Salvataggio…" : "Salva la password"}
        </button>
      </form>
    </Shell>
  );
}
