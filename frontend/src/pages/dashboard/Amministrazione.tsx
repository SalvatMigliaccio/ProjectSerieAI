/**
 * L'amministrazione: i nuovi iscritti, la ricerca, la sospensione, i ruoli.
 *
 * UNA SCHERMATA DELLA DASHBOARD, con tre sezioni e un indirizzo ciascuna:
 * `/dashboard/admin`, `/dashboard/admin/utenti` (con `/utenti/<id>` per la
 * scheda di una persona) e `/dashboard/admin/registro`. La voce nella barra
 * laterale compare solo a chi ha `users:manage`. Il link alla scheda di un utente e' quello che si manda
 * a un altro amministratore, e deve aprire quella scheda.
 *
 * QUESTA PAGINA NON PROTEGGE NIENTE. Il server rifiuta ogni chiamata a chi non
 * ha `users:manage`, leggendo i ruoli dal database a ogni richiesta. Qui si
 * nasconde solo cio' che verrebbe rifiutato, per non mostrare pulsanti che
 * rispondono 403. La scheda dice cosa si puo' fare (`can_manage`,
 * `can_change_roles`) con le stesse regole che il server applica.
 *
 * IL LANCIO DI PREDICT ROUND E CLOSE ROUND NON E' QUI, ED E' VOLUTO: e' la
 * fase 3 del piano del 18 novembre 2026. Il pulsante mettera' in coda una
 * richiesta per il worker, non far girare il modello dentro l'API.
 */

import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Link, NavLink, Navigate, useNavigate, useParams } from "react-router-dom";

import { type AdminEvent, type AdminUser, type UserDetail, admin } from "../../api/admin";
import { useAuth } from "../../auth/AuthContext";
import { FormAlert, FormSuccess, serverError } from "../../components/Form";
import { ScreenHeader } from "../../components/ScreenHeader";
import { EmptyState, ErrorState, Loading } from "../../components/States";
import { useApi } from "../../hooks/useApi";
import { ago, localDateTime } from "../../lib/format";

const RUOLI: Record<string, string> = {
  customer: "Abbonato",
  admin: "Amministratore",
  superadmin: "Super amministratore",
};

const STATI: Record<string, string> = {
  active: "Attivo",
  pending: "In attesa di verifica",
  suspended: "Sospeso",
};

/** Gli eventi in parole. Un tipo che non e' qui passa com'e'. */
const EVENTI: Record<string, string> = {
  signup: "Registrazione",
  verify_email: "Email verificata",
  verification_mail: "Invio email di verifica",
  sign_in: "Accesso",
  sign_out: "Uscita",
  change_password: "Cambio password",
  reset_requested: "Richiesta reset password",
  reset_done: "Password reimpostata",
  session_revoked: "Sessione chiusa",
  sessions_revoked: "Sessioni chiuse",
  role_granted: "Ruolo concesso",
  role_revoked: "Ruolo revocato",
  suspended: "Account sospeso",
  reactivated: "Account riattivato",
  unlocked: "Account sbloccato",
  account_deleted: "Account eliminato",
};

/** Dentro la dashboard, dal 18 novembre 2026: una schermata come le altre. */
export const BASE = "/dashboard/admin";

const SEZIONI = [
  { id: "panoramica", path: BASE, label: "Panoramica",
    lead: "Le iscrizioni degli ultimi trenta giorni e lo stato degli account." },
  { id: "utenti", path: `${BASE}/utenti`, label: "Utenti",
    lead: "Cerca un account per indirizzo, guardane la storia, sospendilo o riattivalo." },
  { id: "registro", path: `${BASE}/registro`, label: "Registro",
    lead: "Cosa hanno fatto gli amministratori, e chi lo ha fatto. Non si cancella." },
] as const;

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

const ICONE: Record<string, ReactNode> = {
  panoramica: <Icon><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></Icon>,
  utenti: (
    <Icon>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 19.5c.9-3 3.2-4.6 6-4.6s5.1 1.6 6 4.6M16 4.8a3.2 3.2 0 0 1 0 6.4M18.5 14.9c1.3.7 2.1 2.2 2.5 4.6" />
    </Icon>
  ),
  registro: <Icon><path d="M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6" /></Icon>,
};

function StatoBadge({ u }: { u: AdminUser }) {
  const cls = u.status === "active" ? "ok" : u.status === "suspended" ? "ko" : "idle";
  return (
    <>
      <span className={`adm-badge adm-badge--${cls}`}>{STATI[u.status] ?? u.status}</span>
      {u.locked && <span className="adm-badge adm-badge--warn">Bloccato</span>}
    </>
  );
}

function RuoliBadge({ roles }: { roles: string[] }) {
  const staff = roles.filter((r) => r !== "customer");
  if (!staff.length) return null;
  return <>{staff.map((r) => <span key={r} className="adm-badge adm-badge--role">{RUOLI[r] ?? r}</span>)}</>;
}

// ---------------------------------------------------------------------------
// Panoramica

function Panoramica() {
  const s = useApi(() => admin.summary(), []);
  const ultimi = useApi(() => admin.users({ limit: 8 }), []);

  if (s.error) return <ErrorState error={s.error} what="Non riesco a leggere il riepilogo" />;
  if (!s.data) return <Loading what="Carico il riepilogo…" />;
  const d = s.data;
  const max = Math.max(1, ...d.signups_by_day.map((x) => x.signups));

  const tiles: [string, number, string?][] = [
    ["Account", d.total],
    ["Nuovi in 24 ore", d.new_24h],
    ["Nuovi in 7 giorni", d.new_7d],
    ["Nuovi in 30 giorni", d.new_30d],
    ["Attivi", d.by_status.active ?? 0],
    ["In attesa di verifica", d.by_status.pending ?? 0, "idle"],
    ["Sospesi", d.by_status.suspended ?? 0, "ko"],
    ["Sessioni aperte", d.active_sessions],
  ];

  return (
    <>
      <div className="adm-tiles">
        {tiles.map(([label, value, tone]) => (
          <div key={label} className={`adm-tile${tone ? ` adm-tile--${tone}` : ""}`}>
            <span className="adm-tile__value">{value}</span>
            <span className="adm-tile__label">{label}</span>
          </div>
        ))}
      </div>

      <section className="adm-card">
        <h2>Iscrizioni, ultimi 30 giorni</h2>
        <div className="adm-bars" role="img"
          aria-label={`Iscrizioni al giorno negli ultimi 30 giorni, ${d.new_30d} in tutto`}>
          {d.signups_by_day.map((x) => (
            <span key={x.day} className="adm-bars__col"
              title={`${new Date(x.day).toLocaleDateString("it-IT")}: ${x.signups}`}>
              <span className="adm-bars__bar" style={{ height: `${(x.signups / max) * 100}%` }} />
            </span>
          ))}
        </div>
        <p className="adm-bars__axis">
          <span>{new Date(d.signups_by_day[0]?.day ?? "").toLocaleDateString("it-IT")}</span>
          <span>oggi</span>
        </p>
      </section>

      <section className="adm-card">
        <div className="adm-card__head">
          <h2>Ultimi iscritti</h2>
          <Link to={`${BASE}/utenti`} className="adm-link">Tutti gli utenti →</Link>
        </div>
        {ultimi.error && <ErrorState error={ultimi.error} what="Non riesco a leggere gli utenti" />}
        {ultimi.data && <ElencoUtenti rows={ultimi.data.rows} />}
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Utenti

function ElencoUtenti({ rows }: { rows: AdminUser[] }) {
  const navigate = useNavigate();
  if (!rows.length) return <EmptyState>Nessun account corrisponde.</EmptyState>;
  return (
    <div className="adm-table-wrap">
      <table className="adm-table">
        <thead>
          <tr>
            <th scope="col">Email</th>
            <th scope="col">Stato</th>
            <th scope="col">Iscritto</th>
            <th scope="col">Ultima attività</th>
            <th scope="col" className="num">Sessioni</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => (
            <tr key={u.id} onClick={() => navigate(`${BASE}/utenti/${u.id}`)}>
              <td>
                {/* Il link e' il bersaglio vero, per tastiera e screen reader;
                    il clic sulla riga e' solo una comodita' per il mouse. */}
                <Link to={`${BASE}/utenti/${u.id}`} onClick={(e) => e.stopPropagation()}>{u.email}</Link>
                <RuoliBadge roles={u.roles} />
              </td>
              <td><StatoBadge u={u} /></td>
              <td><time dateTime={u.created_at} title={localDateTime(u.created_at)}>{ago(u.created_at)}</time></td>
              <td>{u.last_seen_at
                ? <time dateTime={u.last_seen_at} title={localDateTime(u.last_seen_at)}>{ago(u.last_seen_at)}</time>
                : <span className="adm-faint">mai</span>}</td>
              <td className="num">{u.active_sessions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PAGINA = 25;

function Utenti() {
  const [testo, setTesto] = useState("");
  const [q, setQ] = useState("");
  const [stato, setStato] = useState("");
  const [ruolo, setRuolo] = useState("");
  const [offset, setOffset] = useState(0);

  // Una richiesta per pausa, non per tasto: con 250 ms si scrive un indirizzo
  // intero e parte una sola ricerca.
  useEffect(() => {
    const t = window.setTimeout(() => {
      setQ(testo.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(t);
  }, [testo]);

  const page = useApi(
    () => admin.users({ q, status: stato, role: ruolo, limit: PAGINA, offset }),
    [q, stato, ruolo, offset],
  );

  return (
    <>
      <form className="adm-filters" role="search" onSubmit={(e) => e.preventDefault()}>
        <label className="adm-filters__search">
          <span className="visually-hidden">Cerca per email</span>
          <input type="search" placeholder="Cerca per email…" value={testo}
            onChange={(e) => setTesto(e.target.value)} autoComplete="off" spellCheck={false} />
        </label>
        <label>
          <span className="visually-hidden">Stato</span>
          <select value={stato} onChange={(e) => { setStato(e.target.value); setOffset(0); }}>
            <option value="">Tutti gli stati</option>
            <option value="active">Attivi</option>
            <option value="pending">In attesa di verifica</option>
            <option value="suspended">Sospesi</option>
          </select>
        </label>
        <label>
          <span className="visually-hidden">Ruolo</span>
          <select value={ruolo} onChange={(e) => { setRuolo(e.target.value); setOffset(0); }}>
            <option value="">Tutti i ruoli</option>
            <option value="staff">Amministratori</option>
            <option value="customer">Abbonati</option>
          </select>
        </label>
      </form>

      {page.error && <ErrorState error={page.error} what="Non riesco a leggere gli utenti" />}
      {page.loading && <Loading what="Cerco…" />}
      {page.data && (
        <>
          <ElencoUtenti rows={page.data.rows} />
          <nav className="adm-pager" aria-label="Pagine dei risultati">
            <span>
              {page.data.total === 0 ? "0 risultati" :
                `${offset + 1}–${offset + page.data.rows.length} di ${page.data.total}`}
            </span>
            <button type="button" className="btn btn--ghost btn--sm" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGINA))}>← Precedenti</button>
            <button type="button" className="btn btn--ghost btn--sm"
              disabled={offset + PAGINA >= page.data.total}
              onClick={() => setOffset(offset + PAGINA)}>Successivi →</button>
          </nav>
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Scheda di un utente

function dettagli(e: AdminEvent): string {
  const d = e.details ?? {};
  const parti: string[] = [];
  if (typeof d.role === "string") parti.push(RUOLI[d.role] ?? d.role);
  if (typeof d.cause === "string") parti.push(d.cause.replace(/_/g, " "));
  if (typeof d.reason === "string" && d.reason) parti.push(`motivo: ${d.reason}`);
  if (typeof d.sessions_revoked === "number") parti.push(`${d.sessions_revoked} sessioni chiuse`);
  if (typeof d.via === "string") parti.push("da variabile d'ambiente");
  if (e.actor_email) parti.push(`da ${e.actor_email}`);
  return parti.join(" · ");
}

function Eventi({ rows, conUtente = false }: { rows: AdminEvent[]; conUtente?: boolean }) {
  if (!rows.length) return <EmptyState>Nessun evento.</EmptyState>;
  return (
    <ul className="adm-events">
      {rows.map((e) => (
        <li key={e.id} className={e.outcome === "ok" ? "" : "is-ko"}>
          <span className="adm-events__dot" aria-hidden="true" />
          <div>
            <p className="adm-events__what">
              {EVENTI[e.kind] ?? e.kind}
              {e.outcome !== "ok" && <span className="adm-badge adm-badge--ko">rifiutato</span>}
              {conUtente && e.user_email && <> · <span className="adm-faint">{e.user_email}</span></>}
            </p>
            <p className="adm-events__meta">
              <time dateTime={e.at} title={localDateTime(e.at)}>{localDateTime(e.at)}</time>
              {e.ip && <> · {e.ip}</>}
              {dettagli(e) && <> · {dettagli(e)}</>}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function SchedaUtente({ userId }: { userId: string }) {
  const [stato, setStato] = useState<UserDetail | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [esito, setEsito] = useState<string | null>(null);
  const [caricamento, setCaricamento] = useState<Error | null>(null);
  const [inCorso, setInCorso] = useState<string | null>(null);
  const [conferma, setConferma] = useState<string | null>(null);
  const [motivo, setMotivo] = useState("");

  useEffect(() => {
    let annullato = false;
    setStato(null);
    setCaricamento(null);
    admin.user(userId)
      .then((d) => { if (!annullato) setStato(d); })
      .catch((e: unknown) => { if (!annullato) setCaricamento(e instanceof Error ? e : new Error(String(e))); });
    return () => { annullato = true; };
  }, [userId]);

  if (caricamento) return <ErrorState error={caricamento} what="Non riesco a leggere questo account" />;
  if (!stato) return <Loading what="Carico l'account…" />;
  const { user: u } = stato;

  async function esegui(nome: string, azione: () => Promise<UserDetail>, fatto: string) {
    setInCorso(nome);
    setErrore(null);
    setEsito(null);
    try {
      setStato(await azione());
      setEsito(fatto);
      setConferma(null);
      setMotivo("");
    } catch (e) {
      setErrore(serverError(e, "l'operazione non e' riuscita"));
    } finally {
      setInCorso(null);
    }
  }

  function sospendi(e: FormEvent) {
    e.preventDefault();
    void esegui("sospendi", () => admin.suspend(u.id, motivo.trim()),
      "Account sospeso e sessioni chiuse. Le pagine dati restano leggibili per al massimo 15 minuti.");
  }

  const isAdmin = u.roles.includes("admin");
  const isSuper = u.roles.includes("superadmin");

  return (
    <>
      <Link to={`${BASE}/utenti`} className="adm-link adm-back">← Tutti gli utenti</Link>

      <section className="adm-card">
        <div className="adm-user-head">
          <div className="profilo__avatar" aria-hidden="true">{u.email.slice(0, 1).toUpperCase()}</div>
          <div>
            <h2 className="adm-user-head__email">{u.email}</h2>
            <p className="adm-user-head__badges">
              <StatoBadge u={u} />
              <RuoliBadge roles={u.roles} />
            </p>
          </div>
        </div>

        <dl className="adm-facts">
          <div><dt>Iscritto</dt><dd>{localDateTime(u.created_at)}</dd></div>
          <div><dt>Email verificata</dt><dd>{u.email_verified_at ? localDateTime(u.email_verified_at) : "no"}</dd></div>
          <div><dt>Ultima attività</dt><dd>{u.last_seen_at ? `${localDateTime(u.last_seen_at)} (${ago(u.last_seen_at)})` : "mai"}</dd></div>
          <div><dt>Sessioni aperte</dt><dd>{u.active_sessions}</dd></div>
        </dl>
      </section>

      <FormAlert>{errore}</FormAlert>
      <FormSuccess>{esito}</FormSuccess>

      <section className="adm-card">
        <h2>Azioni</h2>
        {!stato.can_manage ? (
          <p className="adm-faint">
            {isSuper
              ? "Un super amministratore si gestisce dalla variabile d'ambiente o dalla riga di comando, non da qui."
              : "Questo account non e' gestibile da te: e' il tuo, o e' un amministratore e serve un super amministratore."}
          </p>
        ) : (
          <div className="adm-actions">
            {(u.status === "active" || u.status === "pending") && (
              conferma === "sospendi" ? (
                <form className="adm-confirm" onSubmit={sospendi}>
                  <label>
                    <span>Motivo (facoltativo, resta nel registro)</span>
                    <input value={motivo} onChange={(e) => setMotivo(e.target.value)} maxLength={500}
                      placeholder="es. spam, abuso, richiesta dell'utente" autoFocus />
                  </label>
                  <div className="adm-confirm__btns">
                    <button className="btn btn--danger btn--sm" type="submit" disabled={inCorso !== null}
                      aria-busy={inCorso === "sospendi" || undefined}>
                      {inCorso === "sospendi" ? "Sospendo…" : "Sì, sospendi"}
                    </button>
                    <button className="btn btn--ghost btn--sm" type="button" onClick={() => setConferma(null)}>Annulla</button>
                  </div>
                </form>
              ) : (
                <button className="btn btn--danger btn--sm" type="button" onClick={() => setConferma("sospendi")}>
                  Sospendi account
                </button>
              )
            )}
            {u.status === "suspended" && (
              <button className="btn btn--sm" type="button" disabled={inCorso !== null}
                onClick={() => void esegui("riattiva", () => admin.reactivate(u.id), "Account riattivato.")}>
                {inCorso === "riattiva" ? "Riattivo…" : "Riattiva account"}
              </button>
            )}
            {u.locked && (
              <button className="btn btn--ghost btn--sm" type="button" disabled={inCorso !== null}
                onClick={() => void esegui("sblocca", () => admin.unlock(u.id), "Blocco per tentativi falliti rimosso.")}>
                {inCorso === "sblocca" ? "Sblocco…" : "Rimuovi blocco accessi"}
              </button>
            )}
            {stato.can_change_roles && !isAdmin && u.status === "active" && (
              conferma === "promuovi" ? (
                <span className="adm-confirm adm-confirm--inline">
                  <span>Potrà vedere e sospendere gli utenti.</span>
                  <button className="btn btn--sm" type="button" disabled={inCorso !== null}
                    onClick={() => void esegui("promuovi", () => admin.grantAdmin(u.id), "Ora è amministratore.")}>
                    {inCorso === "promuovi" ? "Nomino…" : "Sì, nomina"}
                  </button>
                  <button className="btn btn--ghost btn--sm" type="button" onClick={() => setConferma(null)}>Annulla</button>
                </span>
              ) : (
                <button className="btn btn--ghost btn--sm" type="button" onClick={() => setConferma("promuovi")}>
                  Nomina amministratore
                </button>
              )
            )}
            {stato.can_change_roles && isAdmin && (
              <button className="btn btn--ghost btn--sm" type="button" disabled={inCorso !== null}
                onClick={() => void esegui("revoca", () => admin.revokeAdmin(u.id), "Non è più amministratore.")}>
                {inCorso === "revoca" ? "Revoco…" : "Revoca amministratore"}
              </button>
            )}
          </div>
        )}
      </section>

      {stato.sessions.length > 0 && (
        <section className="adm-card">
          <h2>Sessioni aperte</h2>
          <ul className="adm-events">
            {stato.sessions.map((s) => (
              <li key={s.id}>
                <span className="adm-events__dot" aria-hidden="true" />
                <div>
                  <p className="adm-events__what">{s.user_agent ?? "dispositivo sconosciuto"}</p>
                  <p className="adm-events__meta">
                    attiva {ago(s.last_seen_at)}{s.ip && <> · {s.ip}</>} · accesso del {localDateTime(s.created_at)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="adm-card">
        <h2>Storia dell'account</h2>
        <Eventi rows={stato.events} />
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Registro

function Registro() {
  const r = useApi(() => admin.audit(100), []);
  if (r.error) return <ErrorState error={r.error} what="Non riesco a leggere il registro" />;
  if (!r.data) return <Loading what="Carico il registro…" />;
  return <section className="adm-card"><Eventi rows={r.data} conUtente /></section>;
}

// ---------------------------------------------------------------------------

export function Amministrazione() {
  const { user, can } = useAuth();
  const { sezione: param, id } = useParams();

  // La rotta sta dentro RequireAuth: qui manca solo il permesso. Chi non lo ha
  // vede perche', non un rimbalzo; il server rifiuta comunque ogni chiamata.
  if (!user || !can("users:manage")) {
    return (
      <>
        <ScreenHeader title="Amministrazione" subtitle="Sezione riservata agli amministratori" />
        <EmptyState>Il tuo account non ha i permessi per questa sezione.</EmptyState>
      </>
    );
  }

  const attiva = SEZIONI.find((s) => s.id === (param ?? "panoramica"));
  if (!attiva || (id && attiva.id !== "utenti")) return <Navigate to={BASE} replace />;

  return (
    <>
      <ScreenHeader
        title={id ? "Scheda utente" : "Amministrazione"}
        badge={<span className="adm-badge adm-badge--role">
          {can("system:manage") ? RUOLI.superadmin : RUOLI.admin}
        </span>}
        subtitle={attiva.lead}
      />

      <nav className="filters adm-tabs" aria-label="Sezioni dell'amministrazione">
        {SEZIONI.map((s) => (
          <NavLink key={s.id} to={s.path} end={s.id === "panoramica"} className="filter">
            {ICONE[s.id]}
            {s.label}
          </NavLink>
        ))}
      </nav>

      <div key={`${attiva.id}/${id ?? ""}`} className="adm-screen">
        {attiva.id === "panoramica" && <Panoramica />}
        {attiva.id === "utenti" && (id ? <SchedaUtente userId={id} /> : <Utenti />)}
        {attiva.id === "registro" && <Registro />}
      </div>
    </>
  );
}
