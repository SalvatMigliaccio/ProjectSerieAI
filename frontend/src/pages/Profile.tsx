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
import { useAuth } from "../auth/AuthContext";
import {
  FormAlert,
  FormSuccess,
  PasswordField,
  SubmitButton,
  newPasswordError,
  requiredError,
  serverError,
  useValidation,
} from "../components/Form";
import { Colophon, Masthead } from "../components/Layout";
import { localDateTime } from "../lib/format";

/**
 * I ruoli e gli stati in parole, non i nomi della tabella. "customer" in una
 * pagina italiana e' un'etichetta di database, non un'informazione. Un ruolo
 * che non e' qui passa com'e': meglio un nome tecnico che uno inventato.
 */
const RUOLI: Record<string, string> = {
  customer: "Abbonato",
  admin: "Amministratore",
  superadmin: "Super amministratore",
};

const STATI: Record<string, string> = {
  active: "attivo",
  pending: "in attesa di verifica",
  suspended: "sospeso",
};

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

  const v = useValidation(
    {
      attuale: requiredError(attuale, "la password attuale"),
      nuova:
        newPasswordError(nuova) ??
        (nuova === attuale ? "La nuova password è uguale a quella attuale." : null),
    },
    { attuale, nuova },
  );

  async function invia(event: FormEvent) {
    event.preventDefault();
    setErrore(null);
    setFatto(false);
    if (!v.check()) return;
    setInCorso(true);
    try {
      await authApi.changePassword(attuale, nuova);
      setAttuale("");
      setNuova("");
      v.reset();
      setFatto(true);
    } catch (e) {
      setErrore(serverError(e, "Non riesco a cambiare la password. Riprova."));
    } finally {
      setInCorso(false);
    }
  }

  return (
    <form className="profilo__form" onSubmit={invia} noValidate>
      <PasswordField
        id="pw-attuale"
        label="Password attuale"
        value={attuale}
        onChange={setAttuale}
        onBlur={v.blur("attuale")}
        error={v.error("attuale")}
        inputRef={v.ref("attuale")}
      />
      <PasswordField
        id="pw-nuova"
        label="Nuova password"
        autoComplete="new-password"
        counter
        value={nuova}
        onChange={setNuova}
        onBlur={v.blur("nuova")}
        error={v.error("nuova")}
        inputRef={v.ref("nuova")}
        hint="Cambiandola si chiudono le altre sessioni; questa resta aperta."
      />

      <FormAlert>{errore}</FormAlert>
      <FormSuccess>
        {fatto ? "Password aggiornata. Le altre sessioni sono state chiuse." : null}
      </FormSuccess>

      <SubmitButton busy={inCorso} busyText="Aggiornamento…">
        Aggiorna la password
      </SubmitButton>
    </form>
  );
}

function Sessioni() {
  const [righe, setRighe] = useState<SessionRow[] | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [inCorso, setInCorso] = useState(false);
  // CONFERMA IN DUE PASSI, nella pagina e non con `window.confirm`: chiudere
  // le altre sessioni disconnette davvero altri dispositivi, e un clic solo
  // sul pulsante sbagliato non deve bastare. Il secondo passo dice quante.
  const [conferma, setConferma] = useState(false);
  const [chiuse, setChiuse] = useState<number | null>(null);

  const carica = useCallback(async () => {
    try {
      setRighe(await authApi.sessions());
      setErrore(null);
    } catch (e) {
      setErrore(serverError(e, "Non riesco a leggere le sessioni. Riprova."));
    }
  }, []);

  useEffect(() => {
    void carica();
  }, [carica]);

  async function chiudiLeAltre() {
    setInCorso(true);
    setChiuse(null);
    const quante = (righe ?? []).filter((r) => !r.current).length;
    try {
      await authApi.revokeOtherSessions();
      await carica();
      setChiuse(quante);
    } catch (e) {
      setErrore(serverError(e, "Non riesco a chiudere le altre sessioni. Riprova."));
    } finally {
      setInCorso(false);
      setConferma(false);
    }
  }

  const altre = (righe ?? []).filter((r) => !r.current).length;

  return (
    <div className="profilo__sessioni">
      <FormAlert>{errore}</FormAlert>
      <FormSuccess>
        {chiuse
          ? `Fatto: ${chiuse === 1 ? "una sessione chiusa" : `${chiuse} sessioni chiuse`}.`
          : null}
      </FormSuccess>

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

      {altre === 0 ? (
        <p className="profilo__hint">Nessun altro dispositivo ha una sessione aperta.</p>
      ) : conferma ? (
        <div className="profilo__conferma" role="group" aria-label="Conferma chiusura sessioni">
          <p>
            Chiudere {altre === 1 ? "l'altra sessione" : `le altre ${altre} sessioni`}? Su{" "}
            {altre === 1 ? "quel dispositivo" : "quei dispositivi"} servirà accedere di nuovo.
          </p>
          <div className="profilo__azioni">
            <button
              className="btn btn--danger"
              type="button"
              onClick={() => void chiudiLeAltre()}
              disabled={inCorso}
              aria-busy={inCorso || undefined}
            >
              {inCorso && <span className="spinner" aria-hidden="true" />}
              {inCorso ? "Chiudo…" : "Sì, chiudi"}
            </button>
            <button
              className="btn btn--ghost"
              type="button"
              onClick={() => setConferma(false)}
              disabled={inCorso}
            >
              Annulla
            </button>
          </div>
        </div>
      ) : (
        <button className="btn btn--ghost" type="button" onClick={() => setConferma(true)}>
          {altre === 1 ? "Chiudi l'altra sessione" : `Chiudi le altre ${altre} sessioni`}
        </button>
      )}
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

          {/* Chi sei, in alto e in una riga: l'iniziale, l'indirizzo, il
              livello. "Esci" sta qui a destra e non in mezzo ai moduli: e'
              l'azione che chiude la visita, non un'impostazione. */}
          <section className="profilo__blocco profilo__identita" aria-labelledby="profilo-identita">
            <div className="profilo__avatar" aria-hidden="true">
              {user.email.slice(0, 1).toUpperCase()}
            </div>
            <div className="profilo__chi">
              <h2 id="profilo-identita" className="visually-hidden">
                Identità
              </h2>
              <p className="profilo__email">{user.email}</p>
              <p className="profilo__ruoli">
                {/* I ruoli sono righe in tabella, non un enum: un livello di
                    abbonamento e' un INSERT, e questa riga lo mostra senza
                    sapere quali livelli esistono. */}
                {(user.roles.length ? user.roles : ["nessun ruolo"]).map((r) => (
                  <span key={r} className="profilo__ruolo">
                    {RUOLI[r] ?? r}
                  </span>
                ))}
                <span className="faint">account {STATI[user.status] ?? user.status}</span>
              </p>
            </div>
            <button
              className="btn btn--ghost profilo__esci"
              type="button"
              onClick={() => void signOut().then(() => navigate("/"))}
            >
              <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Esci
            </button>
          </section>

          <section className="profilo__blocco">
            <h2>Password</h2>
            <p className="profilo__lead">
              Cambiala quando vuoi: serve quella attuale per confermare che sei tu.
            </p>
            <CambioPassword />
          </section>

          <section className="profilo__blocco">
            <h2>Dove sei collegato</h2>
            <p className="profilo__lead">
              I dispositivi da cui hai fatto l'accesso. Se ne vedi uno che non riconosci,
              chiudi le altre sessioni e cambia la password.
            </p>
            <Sessioni />
          </section>
        </div>
      </main>
      <Colophon modelVersion={null} />
    </>
  );
}
