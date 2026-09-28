/**
 * L'area dell'account: chi sei, la password, e dove sei entrato.
 *
 * PERCHE' LE SESSIONI STANNO IN PAGINA E NON SOLO NEL DATABASE. Il cookie e'
 * HttpOnly e revocabile dal server: questo lo protegge da uno script nella
 * pagina, e non dice niente alla persona a cui hanno prestato il portatile.
 * Un elenco con l'ora, l'indirizzo e il dispositivo e' l'unica forma in cui
 * un accesso non suo diventa visibile a chi puo' fare qualcosa.
 *
 * File suo e non dentro `Account.tsx`: li' ci sono i quattro moduli delle
 * credenziali, che sono lo stesso form quattro volte. Questa e' un'altra cosa.
 */

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import * as authApi from "../api/auth";
import type { SessionRow } from "../api/auth";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Colophon, Masthead } from "../components/Layout";
import { localDateTime } from "../lib/format";

const MIN_PASSWORD = 12;

function messaggio(errore: unknown, fallback: string): string {
  if (errore instanceof ApiError) return errore.message;
  if (errore instanceof Error) return errore.message;
  return fallback;
}

/**
 * Il nome del browser dallo user agent, senza libreria.
 *
 * Volutamente approssimativo e volutamente breve: serve a farsi riconoscere
 * ("questo e' il mio telefono"), non a identificare il dispositivo. Una
 * stringa intera di user agent in pagina non la legge nessuno.
 */
function dispositivo(ua: string | null): string {
  if (!ua) return "dispositivo sconosciuto";
  const sistema = /Android/i.test(ua)
    ? "Android"
    : /iPhone|iPad|iOS/i.test(ua)
      ? "iOS"
      : /Mac OS X/i.test(ua)
        ? "macOS"
        : /Windows/i.test(ua)
          ? "Windows"
          : /Linux/i.test(ua)
            ? "Linux"
            : "sistema sconosciuto";
  const browser = /Edg\//i.test(ua)
    ? "Edge"
    : /OPR\/|Opera/i.test(ua)
      ? "Opera"
      : /Chrome\//i.test(ua)
        ? "Chrome"
        : /Safari\//i.test(ua)
          ? "Safari"
          : /Firefox\//i.test(ua)
            ? "Firefox"
            : "browser sconosciuto";
  return `${browser} su ${sistema}`;
}

function CambioPassword() {
  const [attuale, setAttuale] = useState("");
  const [nuova, setNuova] = useState("");
  const [inCorso, setInCorso] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const [fatto, setFatto] = useState(false);

  async function invia(event: FormEvent) {
    event.preventDefault();
    setErrore(null);
    setFatto(false);
    if (nuova.length < MIN_PASSWORD) {
      setErrore(`La nuova password deve avere almeno ${MIN_PASSWORD} caratteri.`);
      return;
    }
    setInCorso(true);
    try {
      await authApi.changePassword(attuale, nuova);
      setAttuale("");
      setNuova("");
      setFatto(true);
    } catch (e) {
      setErrore(messaggio(e, "non riesco a cambiare la password"));
    } finally {
      setInCorso(false);
    }
  }

  return (
    <form className="profilo__form" onSubmit={invia} noValidate>
      <label htmlFor="pw-attuale">Password attuale</label>
      <input
        id="pw-attuale"
        type="password"
        autoComplete="current-password"
        value={attuale}
        onChange={(e) => setAttuale(e.target.value)}
        required
      />

      <label htmlFor="pw-nuova">Nuova password</label>
      <input
        id="pw-nuova"
        type="password"
        autoComplete="new-password"
        value={nuova}
        onChange={(e) => setNuova(e.target.value)}
        required
      />
      <p className="profilo__hint">
        Almeno {MIN_PASSWORD} caratteri. Cambiandola, le altre sessioni si
        chiudono: questa resta aperta.
      </p>

      {errore && (
        <p className="account-error" role="alert">
          {errore}
        </p>
      )}
      {fatto && (
        <p className="profilo__ok" role="status">
          Password aggiornata.
        </p>
      )}

      <button className="btn" type="submit" disabled={inCorso}>
        {inCorso ? "Aggiorno…" : "Aggiorna password"}
      </button>
    </form>
  );
}

function Sessioni() {
  const [righe, setRighe] = useState<SessionRow[] | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [inCorso, setInCorso] = useState(false);

  const carica = useCallback(async () => {
    try {
      setRighe(await authApi.sessions());
      setErrore(null);
    } catch (e) {
      setErrore(messaggio(e, "non riesco a leggere le sessioni"));
    }
  }, []);

  useEffect(() => {
    void carica();
  }, [carica]);

  async function chiudiLeAltre() {
    setInCorso(true);
    try {
      await authApi.revokeOtherSessions();
      await carica();
    } catch (e) {
      setErrore(messaggio(e, "non riesco a chiudere le altre sessioni"));
    } finally {
      setInCorso(false);
    }
  }

  const altre = (righe ?? []).filter((r) => !r.current).length;

  return (
    <div className="profilo__sessioni">
      {errore && (
        <p className="account-error" role="alert">
          {errore}
        </p>
      )}

      {righe === null ? (
        <p className="state">Carico le sessioni…</p>
      ) : (
        <ul className="sessioni">
          {righe.map((r) => (
            <li key={r.id} className={r.current ? "sessioni__riga is-current" : "sessioni__riga"}>
              <span className="sessioni__device">
                {dispositivo(r.user_agent)}
                {r.current && <em className="sessioni__badge">questa</em>}
              </span>
              <span className="sessioni__meta">
                ultimo accesso {localDateTime(r.last_seen_at)}
                {r.ip ? ` · ${r.ip}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}

      <button
        className="btn btn--ghost"
        type="button"
        onClick={() => void chiudiLeAltre()}
        disabled={inCorso || altre === 0}
      >
        {altre === 0
          ? "Nessun'altra sessione aperta"
          : inCorso
            ? "Chiudo…"
            : `Chiudi le altre ${altre} sessioni`}
      </button>
      <p className="profilo__hint">
        Chiude ovunque tranne qui. Se vedi un accesso che non riconosci, chiudi
        le altre sessioni e cambia la password.
      </p>
    </div>
  );
}

export function Profile() {
  const { phase, user, signOut } = useAuth();
  const navigate = useNavigate();

  // Nessun redirect automatico: chi arriva senza sessione vede perche', non
  // un rimbalzo che sembra un errore.
  if (phase !== "authenticated" || !user) {
    return (
      <>
        <Masthead current="hero" />
        <main id="contenuto" className="account" tabIndex={-1}>
          <section className="account-card">
            <h1>Il tuo account</h1>
            <p className="lead">Per vedere questa pagina serve entrare.</p>
            <Link className="btn" to="/sign-in">
              Accedi
            </Link>
          </section>
        </main>
        <Colophon modelVersion={null} />
      </>
    );
  }

  return (
    <>
      <Masthead current="hero" />
      <main id="contenuto" className="profilo" tabIndex={-1}>
        <div className="wrap">
          <h1>Il tuo account</h1>

          <section className="profilo__blocco">
            <h2>Identita'</h2>
            <dl className="profilo__dati">
              <dt>Indirizzo</dt>
              <dd>{user.email}</dd>
              <dt>Stato</dt>
              <dd>{user.status === "active" ? "attivo" : user.status}</dd>
              <dt>Accesso</dt>
              {/* I ruoli sono righe in tabella, non un enum: un livello di
                  abbonamento e' un INSERT, e questa riga lo mostra senza
                  sapere quali livelli esistono. */}
              <dd>{user.roles.join(", ") || "nessun ruolo"}</dd>
            </dl>
            <button
              className="btn btn--ghost"
              type="button"
              onClick={() => void signOut().then(() => navigate("/"))}
            >
              Esci
            </button>
          </section>

          <section className="profilo__blocco">
            <h2>Password</h2>
            <CambioPassword />
          </section>

          <section className="profilo__blocco">
            <h2>Sessioni attive</h2>
            <Sessioni />
          </section>
        </div>
      </main>
      <Colophon modelVersion={null} />
    </>
  );
}
