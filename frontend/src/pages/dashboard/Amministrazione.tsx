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

import {
  type AdminEvent,
  type AdminUser,
  type Count,
  type Job,
  type PipelineState,
  type UserDetail,
  admin,
} from "../../api/admin";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { FormAlert, FormSuccess, serverError } from "../../components/Form";
import { ScreenHeader } from "../../components/ScreenHeader";
import { EmptyState, ErrorState, Loading } from "../../components/States";
import { useApi } from "../../hooks/useApi";
import { ROUND_LABELS, ago, localDateTime } from "../../lib/format";

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
  pipeline_requested: "Comando della pipeline lanciato",
  pipeline_cancelled: "Comando della pipeline annullato",
};

/** Dentro la dashboard, dal 18 novembre 2026: una schermata come le altre. */
export const BASE = "/dashboard/admin";

const SEZIONI = [
  { id: "panoramica", path: BASE, label: "Panoramica",
    lead: "Le iscrizioni degli ultimi trenta giorni e lo stato degli account." },
  { id: "utenti", path: `${BASE}/utenti`, label: "Utenti",
    lead: "Cerca un account per indirizzo, guardane la storia, sospendilo o riattivalo." },
  { id: "pipeline", path: `${BASE}/pipeline`, label: "Pipeline",
    lead: "Lancia predict round e close round. Li esegue il worker sulla macchina della pipeline." },
  { id: "metriche", path: `${BASE}/metriche`, label: "Metriche",
    lead: "Chi usa il sito, chi non riesce a entrare, cosa si pronostica. Ultimi 30 giorni." },
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
  metriche: <Icon><path d="M3 12h4l3-8 4 16 3-8h4" /></Icon>,
  pipeline: <Icon><path d="M7 4v16l13-8z" /></Icon>,
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

/**
 * Lo stato della pipeline, da `/api/status`: la giornata, cosa va fatto, come
 * sono andati gli ultimi lanci. Solo lettura: il pulsante per lanciare
 * predict e close arriva con la fase 3 (ADR 0007).
 */
function Pipeline() {
  const st = useApi(() => api.status(), []);
  if (st.error) return <ErrorState error={st.error} what="Non riesco a leggere lo stato della pipeline" />;
  if (!st.data) return null;
  const d = st.data;
  return (
    <section className="adm-card">
      <div className="adm-card__head">
        <h2>Pipeline</h2>
        {d.current_round !== null && (
          <span className="adm-faint">
            Giornata {d.current_round} · {ROUND_LABELS[d.current_round_status ?? ""] ?? d.current_round_status}
          </span>
        )}
      </div>
      {d.next_action.command && (
        <p className="adm-next">
          Prossimo passo: <code>{d.next_action.command}</code>
          {d.next_action.reason && <> — {d.next_action.reason}</>}
        </p>
      )}
      {d.last_runs.length === 0 ? (
        <p className="adm-faint">Nessun lancio registrato.</p>
      ) : (
        <ul className="adm-events">
          {d.last_runs.map((r) => (
            <li key={`${r.command}-${r.started_at}`} className={r.ok === false ? "is-ko" : ""}>
              <span className="adm-events__dot" aria-hidden="true" />
              <div>
                <p className="adm-events__what">
                  {r.command}
                  {r.ok === false && <span className="adm-badge adm-badge--ko">uscita {r.exit_code}</span>}
                </p>
                <p className="adm-events__meta">
                  {r.started_at ? `${localDateTime(r.started_at)} (${ago(r.started_at)})` : "mai"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="adm-events__meta">
        Snapshot quote:{" "}
        {d.odds_snapshot.exists
          ? d.odds_snapshot.empty
            ? "vuoto (turno non ancora pubblicato)"
            : `${d.odds_snapshot.rows} partite, scaricato ${ago(d.odds_snapshot.downloaded_at)}`
          : "assente"}
      </p>
      {d.warnings.map((w) => <p key={w} className="adm-warn">{w}</p>)}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Pipeline: lanciare predict e close (ADR 0007, fase 3)

const COMANDI: Record<string, { label: string; spiega: string }> = {
  predict_round: {
    label: "Predict round",
    spiega: "Scarica quote e dati, riaddestra e registra le previsioni della prossima giornata aperta. "
      + "Se le quote non sono ancora uscite non scrive niente.",
  },
  close_round: {
    label: "Close round",
    spiega: "Aggancia i risultati e archivia la prima giornata predetta e interamente giocata. "
      + "Una giornata incompleta non si chiude.",
  },
};

const STATI_JOB: Record<string, [string, string]> = {
  queued: ["In coda", "idle"],
  running: ["In corso", "warn"],
  done: ["Completato", "ok"],
  failed: ["Fallito", "ko"],
  cancelled: ["Annullato", "idle"],
};

function JobBadge({ job }: { job: Job }) {
  const [label, tone] = job.stale ? ["Worker muto", "ko"] : STATI_JOB[job.status] ?? [job.status, "idle"];
  return <span className={`adm-badge adm-badge--${tone}`}>{label}</span>;
}

function durata(a: string | null, b: string | null): string {
  if (!a) return "";
  const ms = (b ? new Date(b).getTime() : Date.now()) - new Date(a).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

function SezionePipeline() {
  const [stato, setStato] = useState<PipelineState | null>(null);
  const [errore, setErrore] = useState<Error | null>(null);
  const [azione, setAzione] = useState<string | null>(null);
  const [fallita, setFallita] = useState<string | null>(null);
  const [conferma, setConferma] = useState<string | null>(null);
  const [prova, setProva] = useState(false);
  const [senzaDownload, setSenzaDownload] = useState(false);
  const [aperto, setAperto] = useState<Job | null>(null);

  // Ogni 3 secondi mentre un job e' attivo, ogni 30 altrimenti: l'output in
  // diretta serve solo quando qualcosa gira.
  const attivo = stato?.active?.status;
  useEffect(() => {
    let vivo = true;
    const carica = () =>
      admin.pipeline()
        .then((s) => { if (vivo) { setStato(s); setErrore(null); } })
        .catch((e: unknown) => { if (vivo) setErrore(e instanceof Error ? e : new Error(String(e))); });
    void carica();
    const t = window.setInterval(carica, attivo === "queued" || attivo === "running" ? 3000 : 30000);
    return () => { vivo = false; window.clearInterval(t); };
  }, [attivo]);

  if (errore && !stato) return <ErrorState error={errore} what="Non riesco a leggere la pipeline" />;
  if (!stato) return <Loading what="Carico la pipeline…" />;

  async function lancia(command: string) {
    setAzione(command);
    setFallita(null);
    try {
      setStato(await admin.launch(command, { dry_run: prova, skip_ingest: senzaDownload }));
      setConferma(null);
    } catch (e) {
      setFallita(serverError(e, "non sono riuscito a mettere in coda il comando"));
    } finally {
      setAzione(null);
    }
  }

  async function annulla(job: Job) {
    setAzione(`annulla-${job.id}`);
    setFallita(null);
    try {
      setStato(await admin.cancelJob(job.id));
    } catch (e) {
      setFallita(serverError(e, "non sono riuscito ad annullare"));
    } finally {
      setAzione(null);
    }
  }

  async function apri(job: Job) {
    if (aperto?.id === job.id) { setAperto(null); return; }
    try {
      setAperto(await admin.job(job.id));
    } catch (e) {
      setFallita(serverError(e, "non riesco a leggere il log"));
    }
  }

  const a = stato.active;
  const worker = stato.workers[0];

  return (
    <>
      <section className="adm-card">
        <div className="adm-card__head">
          <h2>Worker</h2>
          <span className={`adm-badge adm-badge--${stato.any_online ? "ok" : "ko"}`}>
            {stato.any_online ? "Online" : "Offline"}
          </span>
        </div>
        {worker ? (
          <p className="adm-next">
            <code>{worker.name}</code> · ultimo contatto {ago(worker.last_seen_at)}
            {" "}· controlla la coda ogni {worker.poll_seconds} s
          </p>
        ) : (
          <p className="adm-next">Nessun worker si è mai collegato.</p>
        )}
        {!stato.any_online && (
          <p className="adm-warn">
            Il worker non risponde: probabilmente il PC della pipeline è spento. Un comando lanciato
            ora resta in coda e parte quando il worker torna online.
          </p>
        )}
      </section>

      <FormAlert>{fallita}</FormAlert>

      {a && (
        <section className="adm-card adm-job--active">
          <div className="adm-card__head">
            <h2>{COMANDI[a.command]?.label ?? a.command} <JobBadge job={a} /></h2>
            <span className="adm-faint">
              {a.status === "running" ? `da ${durata(a.started_at, null)}` : `in coda ${ago(a.requested_at)}`}
            </span>
          </div>
          <p className="adm-next">
            Richiesto da {a.requested_by ?? "?"} · {localDateTime(a.requested_at)}
            {a.options.dry_run && " · prova, senza scrivere"}
            {a.options.skip_ingest && " · senza scaricare"}
            {a.worker && <> · worker <code>{a.worker}</code></>}
          </p>
          {a.log_tail && <pre className="adm-log" aria-live="polite">{a.log_tail}</pre>}
          {stato.can_launch && (a.status === "queued" || a.stale) && (
            <button className="btn btn--ghost btn--sm" type="button" disabled={azione !== null}
              onClick={() => void annulla(a)}>
              {a.status === "queued" ? "Togli dalla coda" : "Segna come fallito"}
            </button>
          )}
        </section>
      )}

      {stato.can_launch ? (
        <section className="adm-card">
          <h2>Lancia</h2>
          <div className="adm-launch">
            {Object.entries(COMANDI).map(([cmd, c]) => (
              <div key={cmd} className="adm-launch__item">
                <p className="adm-events__what">{c.label}</p>
                <p className="adm-events__meta">{c.spiega}</p>
                {conferma === cmd ? (
                  <div className="adm-confirm__btns">
                    <button className="btn btn--sm" type="button" disabled={azione !== null}
                      aria-busy={azione === cmd || undefined} onClick={() => void lancia(cmd)}>
                      {azione === cmd ? "Metto in coda…" : `Sì, lancia${prova ? " in prova" : ""}`}
                    </button>
                    <button className="btn btn--ghost btn--sm" type="button" onClick={() => setConferma(null)}>
                      Annulla
                    </button>
                  </div>
                ) : (
                  <button className="btn btn--sm" type="button" disabled={a !== null}
                    title={a ? "C'è già un comando in coda o in corso" : undefined}
                    onClick={() => setConferma(cmd)}>
                    {c.label}
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="adm-launch__opts">
            <label>
              <input type="checkbox" checked={prova} onChange={(e) => setProva(e.target.checked)} />
              Prova (<code>--dry-run</code>): mostra cosa farebbe, non scrive niente
            </label>
            <label>
              <input type="checkbox" checked={senzaDownload} onChange={(e) => setSenzaDownload(e.target.checked)} />
              Senza scaricare (<code>--skip-ingest</code>): usa i dati già presenti
            </label>
          </div>
        </section>
      ) : (
        <p className="adm-faint adm-card">Lanciare i comandi è riservato al super amministratore.</p>
      )}

      <Pipeline />

      <section className="adm-card">
        <h2>Ultimi lanci</h2>
        {stato.jobs.length === 0 ? (
          <EmptyState>Nessun comando lanciato dal pannello.</EmptyState>
        ) : (
          <ul className="adm-events">
            {stato.jobs.map((j) => (
              <li key={j.id} className={j.status === "failed" ? "is-ko" : ""}>
                <span className="adm-events__dot" aria-hidden="true" />
                <div>
                  <p className="adm-events__what">
                    {COMANDI[j.command]?.label ?? j.command} <JobBadge job={j} />
                    {j.options.dry_run && <span className="adm-badge">prova</span>}
                    {j.exit_code !== null && j.exit_code !== 0 && (
                      <span className="adm-faint"> · uscita {j.exit_code}</span>
                    )}
                  </p>
                  <p className="adm-events__meta">
                    {localDateTime(j.requested_at)} · {j.requested_by ?? "?"}
                    {j.started_at && ` · durata ${durata(j.started_at, j.finished_at)}`}
                    {" · "}
                    <button type="button" className="adm-linkbtn" onClick={() => void apri(j)}>
                      {aperto?.id === j.id ? "nascondi output" : "output"}
                    </button>
                  </p>
                  {aperto?.id === j.id && (
                    <pre className="adm-log">{aperto.log_tail ?? "(nessun output)"}</pre>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Metriche

const CAUSE: Record<string, string> = {
  wrong_password: "password sbagliata",
  unknown_email: "email non registrata",
  locked: "account bloccato",
  status_pending: "email non verificata",
  status_suspended: "account sospeso",
};

function Barre({ rows, etichette = {} }: { rows: Count[]; etichette?: Record<string, string> }) {
  if (!rows.length) return <EmptyState>Ancora niente.</EmptyState>;
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ul className="adm-hbars">
      {rows.map((r) => (
        <li key={r.label}>
          <span className="adm-hbars__label" title={etichette[r.label] ?? r.label}>{etichette[r.label] ?? r.label}</span>
          <span className="adm-hbars__track">
            <span className="adm-hbars__bar" style={{ width: `${(r.count / max) * 100}%` }} />
          </span>
          <span className="adm-hbars__value">{r.count}</span>
        </li>
      ))}
    </ul>
  );
}

type Serie = "sign_ins" | "failed" | "picks";
const SERIE: [Serie, string][] = [["sign_ins", "Accessi"], ["failed", "Accessi falliti"], ["picks", "Pronostici"]];

function Tile({ value, label, tone, hint }: { value: ReactNode; label: string; tone?: string; hint?: string }) {
  return (
    <div className={`adm-tile${tone ? ` adm-tile--${tone}` : ""}`} title={hint}>
      <span className="adm-tile__value">{value}</span>
      <span className="adm-tile__label">{label}</span>
    </div>
  );
}

function pct(a: number, b: number): string {
  return b ? `${Math.round((a / b) * 100)}%` : "—";
}

function Metriche() {
  const m = useApi(() => admin.metrics(), []);
  const [serie, setSerie] = useState<Serie>("sign_ins");
  if (m.error) return <ErrorState error={m.error} what="Non riesco a leggere le metriche" />;
  if (!m.data) return <Loading what="Calcolo le metriche…" />;
  const d = m.data;
  const max = Math.max(1, ...d.daily.map((x) => x[serie]));
  const totale = d.daily.reduce((s, x) => s + x[serie], 0);
  const nome = SERIE.find(([k]) => k === serie)?.[1] ?? "";

  return (
    <>
      <h2 className="adm-group">Attività</h2>
      <div className="adm-tiles">
        <Tile value={d.active_24h} label="Attivi in 24 ore" hint="Account con una sessione usata nelle ultime 24 ore" />
        <Tile value={d.active_7d} label="Attivi in 7 giorni" />
        <Tile value={d.active_30d} label="Attivi in 30 giorni" />
        <Tile value={pct(d.verified_30d, d.signups_30d)} label={`Email verificate (${d.verified_30d} su ${d.signups_30d})`}
          hint="Iscritti negli ultimi 30 giorni che hanno confermato l'indirizzo" />
        <Tile value={d.pending_stale} label="In attesa da oltre 3 giorni" tone={d.pending_stale ? "idle" : undefined}
          hint="Probabilmente un indirizzo sbagliato o la mail finita nello spam" />
      </div>

      <section className="adm-card">
        <div className="adm-card__head">
          <h2>{nome}, ultimi 30 giorni · {totale}</h2>
          <div className="adm-switch" role="group" aria-label="Serie da mostrare">
            {SERIE.map(([k, l]) => (
              <button key={k} type="button" className="filter" aria-pressed={serie === k}
                onClick={() => setSerie(k)}>{l}</button>
            ))}
          </div>
        </div>
        <div className={`adm-bars${serie === "failed" ? " adm-bars--ko" : ""}`} role="img"
          aria-label={`${nome} al giorno negli ultimi 30 giorni, ${totale} in tutto`}>
          {d.daily.map((x) => (
            <span key={x.day} className="adm-bars__col"
              title={`${new Date(x.day).toLocaleDateString("it-IT")}: ${x[serie]}`}>
              <span className="adm-bars__bar" style={{ height: `${(x[serie] / max) * 100}%` }} />
            </span>
          ))}
        </div>
        <p className="adm-bars__axis">
          <span>{new Date(d.daily[0]?.day ?? "").toLocaleDateString("it-IT")}</span>
          <span>oggi</span>
        </p>
      </section>

      <h2 className="adm-group">Sicurezza</h2>
      <div className="adm-tiles">
        <Tile value={d.failed_sign_ins_24h} label="Accessi falliti in 24 ore" tone={d.failed_sign_ins_24h ? "ko" : undefined} />
        <Tile value={d.failed_sign_ins_7d} label="Accessi falliti in 7 giorni" />
        <Tile value={d.failed_ips_24h} label="IP con accessi falliti (24 ore)"
          hint="Tanti fallimenti da pochi indirizzi: qualcuno prova password. Da tanti indirizzi: un attacco distribuito" />
        <Tile value={d.locked_now} label="Account bloccati ora" tone={d.locked_now ? "ko" : undefined} />
      </div>
      <section className="adm-card">
        <h2>Perché falliscono gli accessi (7 giorni)</h2>
        <Barre rows={d.failure_causes_7d} etichette={CAUSE} />
      </section>

      <h2 className="adm-group">Pronostici{d.pick_season ? ` · stagione ${d.pick_season}` : ""}</h2>
      <div className="adm-tiles">
        <Tile value={d.users_with_picks} label="Utenti che pronosticano" />
        <Tile value={d.picks_total} label="Pronostici in stagione" />
        <Tile value={d.picks_7d} label="Pronostici in 7 giorni" />
        <Tile value={d.users_with_favorite} label="Con una squadra preferita" />
      </div>
      <div className="adm-grid2">
        <section className="adm-card">
          <h2>Mercati più scelti</h2>
          <Barre rows={d.picks_by_market} />
        </section>
        <section className="adm-card">
          <h2>Squadre preferite</h2>
          <Barre rows={d.favorite_teams} />
        </section>
      </div>
      <section className="adm-card">
        <h2>Pronostici per giornata</h2>
        <Barre rows={d.picks_by_matchday.map((r) => ({ ...r, label: `Giornata ${r.label}` }))} />
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
  if (typeof d.command === "string") parti.push(d.command);
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
        {attiva.id === "pipeline" && <SezionePipeline />}
        {attiva.id === "metriche" && <Metriche />}
        {attiva.id === "registro" && <Registro />}
      </div>
    </>
  );
}
