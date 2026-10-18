/**
 * Every screen that deals with credentials: sign in, sign up, verify, reset.
 *
 * They live in one file because they are the same form four times over, and
 * splitting them would mostly duplicate the shell. The fields themselves —
 * label, input, the error under it, show/hide password — live in
 * `components/Form.tsx`, shared with the account area.
 *
 * WHAT THESE SCREENS DELIBERATELY DO NOT DO: tell the visitor whether an
 * address is registered. Signing up with a taken address and asking to reset
 * an unknown one both end on the same "check your mailbox" panel, because the
 * API answers identically in both cases. Showing "this address already exists"
 * would undo that server-side care in the interface.
 *
 * ERRORS SIT WHERE THEY ARE FIXED. A field's problem appears under that field
 * (on leaving it, or on submit, with focus moved to the first one); what only
 * the server can know — wrong credentials, too many attempts — appears once,
 * above the button, in Italian and with what to do next.
 */

import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import * as authApi from "../api/auth";
import { useAuth } from "../auth/AuthContext";
import {
  EmailField,
  FormAlert,
  FormSuccess,
  MIN_PASSWORD,
  PasswordField,
  SubmitButton,
  emailError,
  newPasswordError,
  requiredError,
  serverError,
  useValidation,
} from "../components/Form";
import { Colophon, Masthead } from "../components/Layout";

/** The shared frame: brand mark, title, optional lead, the form, links. */
function Shell({
  title,
  lead,
  icon,
  children,
}: {
  title: string;
  lead?: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
}) {
  // TESTATA E PIEDE ANCHE QUI. Senza, la scheda galleggia su una pagina vuota:
  // niente marchio, niente modo di tornare indietro, e l'aria di un errore di
  // caricamento invece di un passaggio previsto. E' anche il punto in cui una
  // persona decide se questo sito merita la sua password.
  return (
    <>
      <Masthead current="hero" />
      <main id="contenuto" className="account" tabIndex={-1}>
        <section className="account-card" aria-labelledby="account-titolo">
          <div className="account-card__mark" aria-hidden="true">
            {icon ?? <img src="/Logo_NoName.png" alt="" width={331} height={333} />}
          </div>
          <h1 id="account-titolo">{title}</h1>
          {lead ? <p className="lead">{lead}</p> : null}
          {children}
        </section>
      </main>
      <Colophon modelVersion={null} />
    </>
  );
}

function Links({ children }: { children: ReactNode }) {
  return <div className="account-links">{children}</div>;
}

const MAIL_ICON = (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="M4 7l8 6 8-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const KEY_ICON = (
  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
    <circle cx="8" cy="15" r="4" />
    <path d="M11 12l9-9M16 7l3 3M18 5l2 2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

// ---------------------------------------------------------------------------

export function SignIn() {
  const { signIn, phase } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Back to where the visitor was heading (RequireAuth puts it in `state`),
  // not always the dashboard.
  const from = (location.state as { from?: string } | null)?.from ?? "/dashboard";
  const justReset = params.get("reset") === "1";

  const v = useValidation(
    { email: emailError(email), password: requiredError(password, "la password") },
    { email, password },
  );

  useEffect(() => {
    if (phase === "authenticated") navigate(from, { replace: true });
  }, [phase, navigate, from]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!v.check()) return;
    setBusy(true);
    try {
      await signIn(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(serverError(err, "Accesso non riuscito. Riprova."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell
      title="Accedi"
      lead="Entra per vedere le previsioni delle partite ancora da giocare."
    >
      {justReset && (
        <FormSuccess>Password aggiornata. Accedi con quella nuova.</FormSuccess>
      )}
      <form onSubmit={submit} noValidate>
        <EmailField
          id="email"
          label="Email"
          value={email}
          onChange={setEmail}
          onBlur={v.blur("email")}
          error={v.error("email")}
          inputRef={v.ref("email")}
        />
        <PasswordField
          id="password"
          label="Password"
          value={password}
          onChange={setPassword}
          onBlur={v.blur("password")}
          error={v.error("password")}
          inputRef={v.ref("password")}
        />
        <p className="field__aside">
          <Link to="/forgot-password">Password dimenticata?</Link>
        </p>

        <FormAlert>{error}</FormAlert>
        <SubmitButton busy={busy} busyText="Accesso in corso…">
          Accedi
        </SubmitButton>
      </form>

      <Links>
        <span className="faint">Non hai un account?</span>
        <Link to="/sign-up">Creane uno</Link>
      </Links>
    </Shell>
  );
}

// ---------------------------------------------------------------------------

export function SignUp() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const v = useValidation(
    { email: emailError(email), password: newPasswordError(password) },
    { email, password },
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!v.check()) return;
    setBusy(true);
    try {
      await authApi.signUp(email.trim(), password);
      setSentTo(email.trim());
    } catch (err) {
      setError(serverError(err, "Registrazione non riuscita. Riprova."));
    } finally {
      setBusy(false);
    }
  }

  if (sentTo) {
    // The address the visitor TYPED, not a statement about the account: the
    // panel is the same whether or not it was already registered.
    return (
      <Shell
        icon={MAIL_ICON}
        title="Controlla la posta"
        lead={
          <>
            Se l'indirizzo è valido, a <strong>{sentTo}</strong> arriva un messaggio
            con il collegamento per attivare l'account.
          </>
        }
      >
        <ul className="account-steps">
          <li>Apri il messaggio di MatchPoint e premi il collegamento.</li>
          <li>Non lo trovi? Guarda nello spam o nelle promozioni.</li>
          <li>Indirizzo sbagliato? Puoi registrarti di nuovo con quello giusto.</li>
        </ul>
        <Links>
          <Link to="/sign-in">Torna all'accesso</Link>
          <button type="button" className="link-button" onClick={() => setSentTo(null)}>
            Usa un altro indirizzo
          </button>
        </Links>
      </Shell>
    );
  }

  return (
    <Shell
      title="Crea un account"
      lead="Ti basta un indirizzo email: ti mandiamo un collegamento per attivarlo."
    >
      <form onSubmit={submit} noValidate>
        <EmailField
          id="email"
          label="Email"
          value={email}
          onChange={setEmail}
          onBlur={v.blur("email")}
          error={v.error("email")}
          inputRef={v.ref("email")}
        />
        <PasswordField
          id="password"
          label="Password"
          autoComplete="new-password"
          counter
          value={password}
          onChange={setPassword}
          onBlur={v.blur("password")}
          error={v.error("password")}
          inputRef={v.ref("password")}
          hint={
            <>
              Nessun obbligo di maiuscole o simboli: una frase lunga è più robusta
              di «Password1!». Puoi usare il gestore di password del browser.
            </>
          }
        />

        <FormAlert>{error}</FormAlert>
        <SubmitButton busy={busy} busyText="Invio in corso…">
          Crea l'account
        </SubmitButton>
      </form>

      <Links>
        <span className="faint">Hai già un account?</span>
        <Link to="/sign-in">Accedi</Link>
      </Links>
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
      setError("Il collegamento è incompleto: manca il codice di verifica.");
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
        if (!cancelled) setError(serverError(err, "Verifica non riuscita."));
      });
    return () => {
      cancelled = true;
    };
  }, [token, adopt, navigate]);

  if (!error) {
    return (
      <Shell icon={MAIL_ICON} title="Attivazione in corso…">
        <p className="account-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          Controllo il collegamento, un attimo.
        </p>
      </Shell>
    );
  }

  return (
    <Shell
      icon={MAIL_ICON}
      title="Collegamento non valido"
      lead="Capita se è scaduto o se l'hai già usato. Registrati di nuovo con lo stesso indirizzo e te ne mandiamo uno nuovo."
    >
      <FormAlert>{error}</FormAlert>
      <Link className="btn btn--block" to="/sign-up">
        Richiedi un nuovo collegamento
      </Link>
      <Links>
        <span className="faint">Account già attivo?</span>
        <Link to="/sign-in">Accedi</Link>
      </Links>
    </Shell>
  );
}

// ---------------------------------------------------------------------------

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const v = useValidation({ email: emailError(email) }, { email });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!v.check()) return;
    setBusy(true);
    try {
      await authApi.forgotPassword(email.trim());
      setSentTo(email.trim());
    } catch (err) {
      setError(serverError(err, "Richiesta non riuscita. Riprova."));
    } finally {
      setBusy(false);
    }
  }

  if (sentTo) {
    return (
      <Shell
        icon={MAIL_ICON}
        title="Controlla la posta"
        lead={
          <>
            Se <strong>{sentTo}</strong> è registrato, ti arriva un collegamento
            per scegliere una nuova password.
          </>
        }
      >
        <ul className="account-steps">
          <li>Il collegamento vale 30 minuti e si usa una volta sola.</li>
          <li>Non lo trovi? Guarda nello spam o nelle promozioni.</li>
        </ul>
        <Links>
          <Link to="/sign-in">Torna all'accesso</Link>
          <button type="button" className="link-button" onClick={() => setSentTo(null)}>
            Usa un altro indirizzo
          </button>
        </Links>
      </Shell>
    );
  }

  return (
    <Shell
      icon={KEY_ICON}
      title="Password dimenticata"
      lead="Scrivi l'indirizzo con cui ti sei registrato: ti mandiamo un collegamento per sceglierne una nuova."
    >
      <form onSubmit={submit} noValidate>
        <EmailField
          id="email"
          label="Email"
          value={email}
          onChange={setEmail}
          onBlur={v.blur("email")}
          error={v.error("email")}
          inputRef={v.ref("email")}
        />

        <FormAlert>{error}</FormAlert>
        <SubmitButton busy={busy} busyText="Invio in corso…">
          Mandami il collegamento
        </SubmitButton>
      </form>

      <Links>
        <span className="faint">Te la sei ricordata?</span>
        <Link to="/sign-in">Torna all'accesso</Link>
      </Links>
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

  const v = useValidation(
    {
      password: newPasswordError(password),
      repeat: !repeat
        ? "Ripeti la password."
        : repeat !== password
          ? "Le due password non coincidono."
          : null,
    },
    { password, repeat },
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!v.check()) return;
    setBusy(true);
    try {
      await authApi.resetPassword(token, password);
      // No session is issued by the reset, by design: whoever changed the
      // password proves it by signing in with it. `reset=1` tells the sign-in
      // screen to say it worked, instead of landing on a bare form.
      navigate("/sign-in?reset=1", { replace: true });
    } catch (err) {
      setError(serverError(err, "Reimpostazione non riuscita. Riprova."));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <Shell
        icon={KEY_ICON}
        title="Collegamento non valido"
        lead="Il collegamento è incompleto: manca il codice. Richiedine uno nuovo."
      >
        <Link className="btn btn--block" to="/forgot-password">
          Richiedi un nuovo collegamento
        </Link>
      </Shell>
    );
  }

  return (
    <Shell
      icon={KEY_ICON}
      title="Scegli una nuova password"
      lead="Cambiandola si chiudono tutte le sessioni aperte: poi accedi con quella nuova."
    >
      <form onSubmit={submit} noValidate>
        <PasswordField
          id="password"
          label="Nuova password"
          autoComplete="new-password"
          counter
          value={password}
          onChange={setPassword}
          onBlur={v.blur("password")}
          error={v.error("password")}
          inputRef={v.ref("password")}
        />
        <PasswordField
          id="repeat"
          label="Ripeti la nuova password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          onBlur={v.blur("repeat")}
          error={v.error("repeat")}
          inputRef={v.ref("repeat")}
          hint={repeat && repeat === password && password.length >= MIN_PASSWORD
            ? "✓ Le due password coincidono."
            : undefined}
        />

        <FormAlert>{error}</FormAlert>
        <SubmitButton busy={busy} busyText="Salvataggio…">
          Salva la nuova password
        </SubmitButton>
      </form>
    </Shell>
  );
}
