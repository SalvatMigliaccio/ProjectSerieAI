/**
 * L'area dell'account: chi sei, la password, e dove sei entrato.
 *
 * TRE SEZIONI CON UN INDIRIZZO CIASCUNA (`/account`, `/account/sicurezza`,
 * `/account/sessioni`), non tre schede in memoria. Un collegamento che porta
 * dritto alle sessioni e' quello che si manda a chi ha appena visto un accesso
 * strano; con lo stato in memoria si arriverebbe sempre sul profilo, e il
 * tasto indietro del browser uscirebbe dall'account invece di tornare alla
 * sezione di prima.
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

import { type FormEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";

import * as authApi from "../api/auth";
import type { ActivityRow, SessionRow } from "../api/auth";
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
import { FavoritePicker } from "../components/FavoritePicker";
import { Colophon, Masthead } from "../components/Layout";
import { ago, localDateTime } from "../lib/format";

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

// ---------------------------------------------------------------------------
// Icone: tratto 1.8, 24x24, come il resto delle schermate di accesso.
// ---------------------------------------------------------------------------

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

const ICONA_PROFILO = (
  <Icon>
    <circle cx="12" cy="8.5" r="3.5" />
    <path d="M5 19.5c1.2-3.2 3.9-5 7-5s5.8 1.8 7 5" />
  </Icon>
);

const ICONA_SICUREZZA = (
  <Icon>
    <path d="M12 3.5l7 2.8v5.2c0 4.3-3 7.8-7 9-4-1.2-7-4.7-7-9V6.3l7-2.8z" />
    <path d="M9 12l2.2 2.2L15.5 10" />
  </Icon>
);

const ICONA_SESSIONI = (
  <Icon>
    <rect x="3" y="4.5" width="18" height="12" rx="2" />
    <path d="M8.5 20h7M12 16.5V20" />
  </Icon>
);

const ICONA_TELEFONO = (
  <Icon size={20}>
    <rect x="7" y="3" width="10" height="18" rx="2.2" />
    <path d="M11 17.5h2" />
  </Icon>
);

const ICONA_COMPUTER = (
  <Icon size={20}>
    <rect x="3" y="4.5" width="18" height="12" rx="2" />
    <path d="M8.5 20h7M12 16.5V20" />
  </Icon>
);

const ICONA_FRECCIA = (
  <Icon size={16}>
    <path d="M9 6l6 6-6 6" />
  </Icon>
);

// ---------------------------------------------------------------------------
// Le sezioni
// ---------------------------------------------------------------------------

type Sezione = "profilo" | "sicurezza" | "sessioni";

const SEZIONI: { id: Sezione; path: string; label: string; lead: string; icon: ReactNode }[] = [
  {
    id: "profilo",
    path: "/account",
    label: "Profilo",
    lead: "Con quale indirizzo sei registrato e cosa include il tuo accesso.",
    icon: ICONA_PROFILO,
  },
  {
    id: "sicurezza",
    path: "/account/sicurezza",
    label: "Sicurezza",
    lead: "Cambia la password quando vuoi: serve quella attuale per confermare che sei tu.",
    icon: ICONA_SICUREZZA,
  },
  {
    id: "sessioni",
    path: "/account/sessioni",
    label: "Sessioni",
    lead: "I dispositivi da cui hai fatto l'accesso. Ogni sessione dura al massimo 24 ore, poi serve accedere di nuovo. Se ne vedi una che non riconosci, chiudila e cambia la password.",
    icon: ICONA_SESSIONI,
  },
];

/**
 * Il nome del browser dallo user agent, senza libreria.
 *
 * Volutamente approssimativo e volutamente breve: serve a farsi riconoscere
 * ("questo e' il mio telefono"), non a identificare il dispositivo. Una
 * stringa intera di user agent in pagina non la legge nessuno.
 */
function dispositivo(ua: string | null): { nome: string; mobile: boolean } {
  if (!ua) return { nome: "Dispositivo sconosciuto", mobile: false };
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
            : "Browser sconosciuto";
  return { nome: `${browser} su ${sistema}`, mobile: /Mobi|Android|iPhone|iPad/i.test(ua) };
}

// ---------------------------------------------------------------------------

/**
 * "tra 5 ore", "tra 40 min": quanto manca alla fine di una sessione.
 *
 * Dall'ADR 0003 una sessione vale al massimo 24 ore, e chi la vede sparire a
 * meta' pomeriggio deve poter capire perche' senza pensare a un guasto.
 */
function tra(iso: string): string {
  const minuti = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (Number.isNaN(minuti)) return "";
  if (minuti < 1) return "a momenti";
  if (minuti < 60) return `tra ${minuti} min`;
  const ore = Math.round(minuti / 60);
  return `tra ${ore} ${ore === 1 ? "ora" : "ore"}`;
}

/** Solo la data, senza ora: "iscritto il 14 settembre 2026" non ha bisogno dei minuti. */
function soloData(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("it-IT", { day: "numeric", month: "long", year: "numeric" });
}

function SezioneProfilo({
  email,
  ruoli,
  stato,
  iscritto,
  verificato,
}: {
  email: string;
  ruoli: string[];
  stato: string;
  iscritto?: string | null;
  verificato?: string | null;
}) {
  const dataIscrizione = soloData(iscritto);
  const dataVerifica = soloData(verificato);
  return (
    <>
      <section className="profilo__blocco" aria-labelledby="dati-titolo">
        <h2 id="dati-titolo">Dati dell'account</h2>
        <dl className="profilo__dati">
          <div>
            <dt>Email</dt>
            <dd className="profilo__email">
              {email}
              {/* La verifica accanto all'indirizzo che verifica: e' una
                  proprieta' dell'email, non dell'account. */}
              <span className={`profilo__verifica${dataVerifica ? " is-ok" : ""}`}>
                {dataVerifica ? `verificata il ${dataVerifica}` : "non ancora verificata"}
              </span>
            </dd>
          </div>
          <div>
            <dt>Livello</dt>
            <dd className="profilo__ruoli">
              {/* I ruoli sono righe in tabella, non un enum: un livello di
                  abbonamento e' un INSERT, e questa riga lo mostra senza
                  sapere quali livelli esistono. */}
              {(ruoli.length ? ruoli : ["nessun ruolo"]).map((r) => (
                <span key={r} className="profilo__ruolo">
                  {RUOLI[r] ?? r}
                </span>
              ))}
            </dd>
          </div>
          <div>
            <dt>Stato</dt>
            <dd>
              <span className={`profilo__stato profilo__stato--${stato}`}>
                Account {STATI[stato] ?? stato}
              </span>
            </dd>
          </div>
          {dataIscrizione && (
            <div>
              <dt>Membro dal</dt>
              <dd>
                <time dateTime={iscritto ?? undefined}>{dataIscrizione}</time>
              </dd>
            </div>
          )}
        </dl>
      </section>

      <FavoritePicker />

      <div className="profilo__scorciatoie">
        {SEZIONI.filter((s) => s.id !== "profilo").map((s) => (
          <Link key={s.id} className="scorciatoia" to={s.path}>
            <span className="scorciatoia__icon">{s.icon}</span>
            <span className="scorciatoia__testo">
              <strong>{s.label}</strong>
              <span>{s.lead}</span>
            </span>
            <span className="scorciatoia__freccia">{ICONA_FRECCIA}</span>
          </Link>
        ))}
      </div>
    </>
  );
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
    <section className="profilo__blocco" aria-labelledby="password-titolo">
      <h2 id="password-titolo">Cambia la password</h2>
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
    </section>
  );
}

function SezioneSicurezza() {
  return (
    <>
      <CambioPassword />
      <aside className="profilo__consiglio" aria-label="Consiglio">
        <span className="profilo__consiglio-icon">{ICONA_SICUREZZA}</span>
        <p>
          <strong>Una frase lunga batte un simbolo.</strong> Quattro parole a caso
          sono più robuste di «Password1!» e più facili da ricordare. Il gestore di
          password del browser funziona anche qui: puoi incollare e completare in
          automatico.
        </p>
      </aside>
    </>
  );
}

function SezioneSessioni() {
  const [righe, setRighe] = useState<SessionRow[] | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [esito, setEsito] = useState<string | null>(null);
  // Quale riga sta chiedendo conferma, e quale sta chiudendo. Una sola alla
  // volta: due conferme aperte insieme sono due pulsanti rossi uguali, e il
  // clic sbagliato chiude il dispositivo sbagliato.
  const [daConfermare, setDaConfermare] = useState<string | "altre" | null>(null);
  const [inCorso, setInCorso] = useState<string | "altre" | null>(null);
  // Dove va il fuoco quando la riga col pulsante appena premuto sparisce:
  // senza, chi usa la tastiera ricomincia dall'inizio della pagina.
  const titolo = useRef<HTMLHeadingElement>(null);

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

  async function chiudiUna(riga: SessionRow) {
    setInCorso(riga.id);
    setEsito(null);
    try {
      await authApi.revokeSession(riga.id);
      await carica();
      setEsito(`Sessione chiusa: ${dispositivo(riga.user_agent).nome}.`);
      titolo.current?.focus();
    } catch (e) {
      setErrore(serverError(e, "Non riesco a chiudere la sessione. Riprova."));
    } finally {
      setInCorso(null);
      setDaConfermare(null);
    }
  }

  async function chiudiLeAltre() {
    setInCorso("altre");
    setEsito(null);
    const quante = (righe ?? []).filter((r) => !r.current).length;
    try {
      await authApi.revokeOtherSessions();
      await carica();
      setEsito(`Fatto: ${quante === 1 ? "una sessione chiusa" : `${quante} sessioni chiuse`}.`);
      titolo.current?.focus();
    } catch (e) {
      setErrore(serverError(e, "Non riesco a chiudere le altre sessioni. Riprova."));
    } finally {
      setInCorso(null);
      setDaConfermare(null);
    }
  }

  const altre = (righe ?? []).filter((r) => !r.current).length;

  return (
    <section className="profilo__blocco" aria-labelledby="sessioni-titolo">
      <h2 id="sessioni-titolo" ref={titolo} tabIndex={-1}>
        Sessioni attive{righe ? <span className="profilo__conta"> {righe.length}</span> : null}
      </h2>
      <FormAlert>{errore}</FormAlert>
      <FormSuccess>{esito}</FormSuccess>

      {righe === null ? (
        !errore && (
          <ul className="sessioni" aria-busy="true" aria-label="Carico le sessioni">
            {[0, 1].map((i) => (
              <li key={i} className="sessione sessione--scheletro" aria-hidden="true">
                <span className="skeleton sessione__icon" />
                <span className="sessione__info">
                  <span className="skeleton skeleton--line" />
                  <span className="skeleton skeleton--line skeleton--short" />
                </span>
              </li>
            ))}
          </ul>
        )
      ) : (
        <ul className="sessioni">
          {righe.map((r) => {
            const d = dispositivo(r.user_agent);
            const confermando = daConfermare === r.id;
            const chiudendo = inCorso === r.id;
            return (
              <li key={r.id} className={`sessione${r.current ? " is-current" : ""}${confermando ? " is-confirming" : ""}`}>
                <span className="sessione__icon">{d.mobile ? ICONA_TELEFONO : ICONA_COMPUTER}</span>
                <div className="sessione__info">
                  <p className="sessione__device">
                    {d.nome}
                    {/* "questo dispositivo" e non un pallino colorato: il
                        colore da solo non e' un'indicazione per chi non lo
                        distingue. */}
                    {r.current && <span className="sessione__badge">Questo dispositivo</span>}
                  </p>
                  <p className="sessione__meta">
                    {r.current ? "In uso adesso" : (
                      <>Attiva <time dateTime={r.last_seen_at} title={localDateTime(r.last_seen_at)}>{ago(r.last_seen_at)}</time></>
                    )}
                    {r.ip ? <> · {r.ip}</> : null}
                    {" · "}accesso del {localDateTime(r.created_at)}
                    {" · "}
                    <time dateTime={r.expires_at} title={localDateTime(r.expires_at)}>
                      scade {tra(r.expires_at)}
                    </time>
                  </p>
                </div>

                {!r.current && (
                  <div className="sessione__azioni">
                    {confermando ? (
                      <div className="sessione__conferma" role="group" aria-label={`Conferma chiusura: ${d.nome}`}>
                        <span>Chiudere questa sessione?</span>
                        <button
                          className="btn btn--danger btn--sm"
                          type="button"
                          onClick={() => void chiudiUna(r)}
                          disabled={chiudendo}
                          aria-busy={chiudendo || undefined}
                        >
                          {chiudendo && <span className="spinner" aria-hidden="true" />}
                          {chiudendo ? "Chiudo…" : "Sì, chiudi"}
                        </button>
                        <button
                          className="btn btn--ghost btn--sm"
                          type="button"
                          onClick={() => setDaConfermare(null)}
                          disabled={chiudendo}
                        >
                          Annulla
                        </button>
                      </div>
                    ) : (
                      <button
                        className="btn btn--ghost btn--sm"
                        type="button"
                        onClick={() => setDaConfermare(r.id)}
                        disabled={inCorso !== null}
                        aria-label={`Chiudi la sessione: ${d.nome}`}
                      >
                        Chiudi
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {/* CONFERMA IN DUE PASSI, nella pagina e non con `window.confirm`:
          chiudere le altre sessioni disconnette davvero altri dispositivi, e
          un clic solo sul pulsante sbagliato non deve bastare. Il secondo
          passo dice quante. */}
      {righe !== null && (
        <div className="profilo__zona">
          {altre === 0 ? (
            <p className="profilo__hint">Nessun altro dispositivo ha una sessione aperta.</p>
          ) : daConfermare === "altre" ? (
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
                  disabled={inCorso !== null}
                  aria-busy={inCorso === "altre" || undefined}
                >
                  {inCorso === "altre" && <span className="spinner" aria-hidden="true" />}
                  {inCorso === "altre" ? "Chiudo…" : "Sì, chiudi"}
                </button>
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => setDaConfermare(null)}
                  disabled={inCorso !== null}
                >
                  Annulla
                </button>
              </div>
            </div>
          ) : (
            <>
              <p className="profilo__hint">
                Esci ovunque tranne che qui: utile se hai usato un computer non tuo.
              </p>
              <button
                className="btn btn--ghost"
                type="button"
                onClick={() => setDaConfermare("altre")}
                disabled={inCorso !== null}
              >
                {altre === 1 ? "Chiudi l'altra sessione" : `Chiudi tutte le altre ${altre} sessioni`}
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * Gli eventi del registro in parole. Un tipo che non e' qui si mostra col suo
 * nome tecnico: il backend decide cosa esporre, e un evento nuovo deve
 * comparire anche prima che qualcuno gli scriva un'etichetta.
 */
const EVENTI: Record<string, { ok: string; ko: string }> = {
  sign_in: { ok: "Accesso", ko: "Accesso non riuscito" },
  sign_out: { ok: "Uscita", ko: "Uscita non riuscita" },
  signup: { ok: "Registrazione", ko: "Registrazione non riuscita" },
  verify_email: { ok: "Email verificata", ko: "Verifica email non riuscita" },
  change_password: { ok: "Password cambiata", ko: "Cambio password non riuscito" },
  reset_requested: { ok: "Richiesto il reset della password", ko: "Reset della password non riuscito" },
  reset_done: { ok: "Password reimpostata dal collegamento email", ko: "Reimpostazione non riuscita" },
  session_revoked: { ok: "Chiusa una sessione", ko: "Chiusura sessione non riuscita" },
  sessions_revoked: { ok: "Chiuse le altre sessioni", ko: "Chiusura sessioni non riuscita" },
};

function SezioneAttivita() {
  const [righe, setRighe] = useState<ActivityRow[] | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    let annullato = false;
    authApi
      .activity()
      .then((r) => {
        if (!annullato) setRighe(r);
      })
      .catch((e: unknown) => {
        if (!annullato) setErrore(serverError(e, "Non riesco a leggere l'attività. Riprova."));
      });
    return () => {
      annullato = true;
    };
  }, []);

  const falliti = (righe ?? []).filter((r) => r.outcome === "ko").length;

  return (
    <section className="profilo__blocco" aria-labelledby="attivita-titolo">
      <h2 id="attivita-titolo">Attività recente</h2>
      <p className="profilo__hint">
        Gli ultimi eventi del tuo account. Un tentativo fallito che non riconosci
        vuol dire che qualcuno sta provando la tua password: cambiala.
      </p>
      <FormAlert>{errore}</FormAlert>

      {/* Il riassunto dei tentativi falliti in cima, in testo: e' l'unica
          cosa di questa lista che chiede di fare qualcosa. */}
      {falliti > 0 && (
        <p className="attivita__avviso">
          {falliti === 1
            ? "1 tentativo non riuscito tra gli ultimi eventi."
            : `${falliti} tentativi non riusciti tra gli ultimi eventi.`}{" "}
          <Link to="/account/sicurezza">Cambia la password</Link>
        </p>
      )}

      {righe === null ? (
        !errore && <p className="state">Carico l'attività…</p>
      ) : righe.length === 0 ? (
        <p className="profilo__hint">Nessun evento registrato.</p>
      ) : (
        <ol className="attivita">
          {righe.map((r, i) => {
            const ko = r.outcome === "ko";
            const testo = EVENTI[r.kind]?.[ko ? "ko" : "ok"] ?? `${r.kind} (${r.outcome})`;
            return (
              <li key={`${r.at}-${i}`} className={`attivita__riga${ko ? " is-ko" : ""}`}>
                <span className="attivita__segno" aria-hidden="true">
                  {ko ? (
                    <Icon size={14}><path d="M12 7.5v5.5M12 16.5h.01" /><circle cx="12" cy="12" r="9" /></Icon>
                  ) : (
                    <Icon size={14}><path d="M6 12.5l4 4L18 8.5" /></Icon>
                  )}
                </span>
                <div className="attivita__info">
                  <p className="attivita__evento">
                    {ko && <span className="visually-hidden">Non riuscito: </span>}
                    {testo}
                  </p>
                  <p className="sessione__meta">
                    <time dateTime={r.at} title={localDateTime(r.at)}>{ago(r.at)}</time>
                    {r.user_agent ? <> · {dispositivo(r.user_agent).nome}</> : null}
                    {r.ip ? <> · {r.ip}</> : null}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

export function Profile() {
  const { phase, user, signOut } = useAuth();
  const navigate = useNavigate();
  const { sezione: param } = useParams();
  const [uscendo, setUscendo] = useState(false);

  // Nessun redirect automatico: chi arriva senza sessione vede perche', non
  // un rimbalzo che sembra un errore.
  if (phase !== "authenticated" || !user) {
    return (
      <>
        <Masthead current="hero" />
        <main id="contenuto" className="account" tabIndex={-1}>
          <section className="account-card">
            <div className="account-card__mark" aria-hidden="true">{ICONA_PROFILO}</div>
            <h1>Il tuo account</h1>
            <p className="lead">Per vedere questa pagina serve entrare.</p>
            <Link className="btn btn--block" to="/sign-in" state={{ from: "/account" }}>
              Accedi
            </Link>
          </section>
        </main>
        <Colophon modelVersion={null} />
      </>
    );
  }

  const attiva = SEZIONI.find((s) => s.id === (param ?? "profilo"));
  // Un indirizzo sbagliato (`/account/xyz`) torna al profilo invece di
  // mostrare una pagina vuota con il menu e niente accanto.
  if (!attiva) return <Navigate to="/account" replace />;

  async function esci() {
    setUscendo(true);
    try {
      await signOut();
    } finally {
      navigate("/");
    }
  }

  return (
    <>
      <Masthead current="hero" />
      <main id="contenuto" className="profilo" tabIndex={-1}>
        <div className="wrap profilo__layout">
          <aside className="profilo__side">
            {/* Chi sei, in cima alla colonna: l'iniziale, l'indirizzo, il
                livello. Resta visibile in ogni sezione, cosi' e' sempre chiaro
                di quale account si stanno cambiando le impostazioni. */}
            <div className="profilo__identita">
              <div className="profilo__avatar" aria-hidden="true">
                {user.email.slice(0, 1).toUpperCase()}
              </div>
              <div className="profilo__chi">
                <p className="profilo__chi-email">{user.email}</p>
                <p className="profilo__chi-ruolo">
                  {user.roles.map((r) => RUOLI[r] ?? r).join(", ") || "nessun ruolo"}
                </p>
              </div>
            </div>

            <nav className="profilo__nav" aria-label="Sezioni dell'account">
              {SEZIONI.map((s) => (
                <NavLink key={s.id} to={s.path} end className="profilo__nav-link">
                  {s.icon}
                  <span>{s.label}</span>
                </NavLink>
              ))}
            </nav>

            {/* "Esci" in fondo e separato dalle sezioni: e' l'azione che chiude
                la visita, non un'impostazione, e non deve stare dove si clicca
                per cambiare sezione. */}
            <div className="profilo__side-foot">
              <button className="profilo__esci" type="button" onClick={() => void esci()}
                disabled={uscendo} aria-busy={uscendo || undefined}>
                {uscendo ? <span className="spinner" aria-hidden="true" /> : (
                  <Icon>
                    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" />
                  </Icon>
                )}
                {uscendo ? "Esco…" : "Esci"}
              </button>
            </div>
          </aside>

          <div className="profilo__panel" key={attiva.id}>
            <header className="profilo__head">
              <p className="profilo__eyebrow">Il tuo account</p>
              <h1>{attiva.label}</h1>
              <p className="profilo__lead">{attiva.lead}</p>
            </header>

            {attiva.id === "profilo" && (
              <SezioneProfilo
                email={user.email}
                ruoli={user.roles}
                stato={user.status}
                iscritto={user.created_at}
                verificato={user.email_verified_at}
              />
            )}
            {attiva.id === "sicurezza" && <SezioneSicurezza />}
            {attiva.id === "sessioni" && (
              <>
                <SezioneSessioni />
                <SezioneAttivita />
              </>
            )}
          </div>
        </div>
      </main>
      <Colophon modelVersion={null} />
    </>
  );
}
