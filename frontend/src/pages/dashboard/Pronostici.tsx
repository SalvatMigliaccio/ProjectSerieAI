/**
 * I miei pronostici: le giocate dell'utente, accanto al modello e al risultato.
 *
 * NESSUN PUNTEGGIO, DI PROPOSITO. La pagina confronta, non fa classifiche: per
 * ogni giocata la probabilita' che le dava il modello e com'e' andata, e in
 * cima l'unico confronto onesto senza punti — quanto spesso le giocate sono
 * uscite contro quanto il modello si aspettava che uscissero.
 *
 * SI GIOCA SOLO PRIMA DEL FISCHIO, e lo decide il server. La pagina mostra le
 * partite aperte e blocca i pulsanti di quelle iniziate, ma e' una cortesia:
 * l'API rifiuta comunque una giocata, o una cancellazione, dopo il calcio
 * d'inizio. Una giocata persa non si toglie dallo storico.
 *
 * Le probabilita' arrivano dall'API, calcolate dagli stessi gol attesi che la
 * dashboard mostra: qui non si rifa' nessun conto.
 *
 * SI GIOCA DALLA CHIUSURA DELLA GIORNATA PRIMA, non dalle quote. Fino al
 * venerdi' una partita e' aperta senza numeri ("quote in arrivo"); quando il
 * modello la prezza, la probabilita' compare anche accanto alle giocate gia'
 * fatte.
 *
 * UNA GIOCATA PER PARTITA, SCELTA E POI SALVATA. Ogni partita e' una scheda
 * chiusa: si apre con la freccia e mostra le giocate possibili. Scegliere
 * cambia solo la BOZZA, in questa pagina; e' il pulsante "Salva" in fondo che
 * le manda al server, tutte insieme (`/api/me/predictions/save`, tutto o
 * niente). Sulla scheda chiusa compare solo il pronostico SALVATO: una bozza
 * non salvata non si mostra come se lo fosse, e si riconosce solo dalla barra
 * "modifiche da salvare". Lasciare la pagina con bozze aperte le perde, e il
 * browser avvisa prima.
 */

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { me, type MyPredictions, type OpenMatch, type Pick, type RoundScore } from "../../api/me";
import { FormAlert, serverError } from "../../components/Form";
import { ScreenHeader } from "../../components/ScreenHeader";
import { localTime, odds as fmtOdds, prob, score } from "../../lib/format";
import { useFavorite } from "../../auth/Favorite";

/** "1" da solo non dice quale squadra: l'etichetta la nomina. */
function etichetta(market: string, home: string, away: string): string {
  if (market === "1") return `1 · ${home}`;
  if (market === "2") return `2 · ${away}`;
  if (market === "X") return "X · pareggio";
  if (market === "1X") return `1X · ${home} o pari`;
  if (market === "X2") return `X2 · pari o ${away}`;
  if (market === "12") return "12 · niente pari";
  if (market === "gol-gol") return "Gol";
  if (market === "no gol") return "No gol";
  return market.replace("over", "Over").replace("under", "Under");
}

function tra(iso: string): string {
  const minuti = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (minuti < 60) return `tra ${Math.max(minuti, 1)} min`;
  const ore = Math.round(minuti / 60);
  if (ore < 48) return `tra ${ore} ${ore === 1 ? "ora" : "ore"}`;
  return `tra ${Math.round(ore / 24)} giorni`;
}

const partita = (home: string, away: string) => `${home}|${away}`;

// ---------------------------------------------------------------------------

function percento(won: number, total: number): string {
  return total ? `${Math.round((won / total) * 100)}%` : "–";
}

/**
 * Tu contro il modello, sulle STESSE partite giocate.
 *
 * La percentuale del modello e' quella della sua selezione dichiarata su ogni
 * partita che hai giocato tu: stesse partite, stesso momento. Confrontarla con
 * il modello su tutta la stagione sarebbe piu' facile o piu' difficile per
 * caso. Sotto, l'altro confronto onesto: quanto il modello si aspettava che
 * uscissero proprio le tue giocate.
 */
function Riepilogo({ dati }: { dati: MyPredictions }) {
  const s = dati.summary;
  const meglio =
    s.hit_rate !== null && s.mean_model_probability !== null
      ? s.hit_rate - s.mean_model_probability
      : null;
  const scarto = s.hit_rate !== null && s.model_hit_rate !== null ? s.hit_rate - s.model_hit_rate : null;

  return (
    <section className="pron-stats" aria-label="Riepilogo">
      <div className="pron-vs">
        <div className="pron-vs__side">
          <span className="pron-stat__label">Tu</span>
          <strong className="pron-vs__value">{s.hit_rate === null ? "–" : prob(s.hit_rate)}</strong>
          <span className="pron-stat__note">{s.resolved ? `${s.won} uscite su ${s.resolved}` : "nessuna ancora giocata"}</span>
        </div>
        <span className="pron-vs__sep" aria-hidden="true">contro</span>
        <div className="pron-vs__side">
          <span className="pron-stat__label">Il modello, sulle stesse partite</span>
          <strong className="pron-vs__value">{s.model_hit_rate === null ? "–" : prob(s.model_hit_rate)}</strong>
          <span className="pron-stat__note">
            {s.model_resolved ? `${s.model_won} uscite su ${s.model_resolved}` : "nessun confronto ancora"}
          </span>
        </div>
      </div>
      <div className="pron-stat">
        <span className="pron-stat__label">Giocate</span>
        <strong className="pron-stat__value">{s.picks}</strong>
        <span className="pron-stat__note">{s.pending} in attesa</span>
      </div>
      <div className="pron-stat">
        <span className="pron-stat__label">Il modello si aspettava</span>
        <strong className="pron-stat__value">{prob(s.mean_model_probability)}</strong>
        <span className="pron-stat__note">probabilità media delle tue giocate</span>
      </div>
      <p className="pron-stats__verdict">
        {scarto === null
          ? "Il confronto compare quando le prime giocate avranno un risultato."
          : Math.abs(scarto) < 0.02
            ? "Sulle stesse partite tu e il modello siete alla pari."
            : scarto > 0
              ? `Sulle stesse partite hai fatto meglio del modello di ${Math.round(scarto * 100)} punti.`
              : `Sulle stesse partite il modello ha fatto meglio di te di ${Math.round(-scarto * 100)} punti.`}
        {meglio !== null && Math.abs(meglio) >= 0.02 &&
          (meglio > 0
            ? " Le tue giocate sono uscite più spesso di quanto il modello si aspettasse."
            : " Le tue giocate sono uscite meno spesso di quanto il modello si aspettasse.")}
        {s.resolved > 0 && s.resolved < 30 && " Con meno di 30 giocate concluse, la differenza è soprattutto caso."}
      </p>
    </section>
  );
}

function PartitaAperta({
  partita,
  salvata,
  bozza,
  onScegli,
  preferita,
}: {
  partita: OpenMatch;
  /** Il pronostico salvato sul server, se c'e'. */
  salvata: Pick | undefined;
  /** La scelta in questa pagina: undefined = nessuna modifica, null = tolta. */
  bozza: string | null | undefined;
  onScegli: (market: string | null) => void;
  preferita: boolean;
}) {
  const [aperta, setAperta] = useState(false);
  const corpo = useId();
  const gruppi = new Map<string, OpenMatch["markets"]>();
  for (const m of partita.markets) gruppi.set(m.group, [...(gruppi.get(m.group) ?? []), m]);

  // Cosa e' selezionato ADESSO nella scheda: la bozza se c'e', altrimenti il salvato.
  const scelta = bozza !== undefined ? bozza : (salvata?.market ?? null);
  const modificata = bozza !== undefined && bozza !== (salvata?.market ?? null);
  const pSalvata = salvata
    ? partita.markets.find((m) => m.market === salvata.market)?.probability ?? salvata.model_probability
    : null;

  return (
    <article className={`pron-card${preferita ? " is-fav" : ""}${aperta ? " is-open" : ""}`}>
      <h3 className="pron-card__head">
        <button
          type="button"
          className="pron-card__toggle"
          aria-expanded={aperta}
          aria-controls={corpo}
          onClick={() => setAperta((v) => !v)}
        >
          <span className="pron-card__teams">
            <span className="pron-card__name">
              {partita.home_team} <span className="faint">–</span> {partita.away_team}
            </span>
            <span className="pron-card__meta">
              Giornata {partita.matchday} · {localTime(partita.kickoff_utc)} ·{" "}
              <time dateTime={partita.kickoff_utc}>{tra(partita.kickoff_utc)}</time>
              {!partita.priced && <span className="pron-match__pending">quote in arrivo</span>}
            </span>
          </span>

          {/* L'ANTEPRIMA E' SOLO DEL SALVATO. Senza un pronostico salvato non
              compare niente; una bozza si vede solo a scheda aperta. */}
          {salvata && (
            <span className="pron-card__saved">
              <span className="pron-card__saved-key">Il tuo pronostico</span>
              <span className="pron-card__saved-val">
                {etichetta(salvata.market, partita.home_team, partita.away_team)}
                {pSalvata !== null && <span className="pron-card__saved-p">{prob(pSalvata)}</span>}
              </span>
            </span>
          )}
          {modificata && <span className="pron-card__dirty">da salvare</span>}

          <span className="pron-card__chevron" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </button>
      </h3>

      <div id={corpo} className="pron-card__body" hidden={!aperta}>
        <p className="pron-card__hint">
          Una giocata per partita: sceglierne un'altra sostituisce la precedente, toccare di
          nuovo quella scelta la toglie. Poi salva in fondo alla pagina.
        </p>
        <div className="pron-groups">
          {[...gruppi.entries()].map(([gruppo, opzioni]) => (
            <div className="pron-group" key={gruppo} role="group" aria-label={gruppo}>
              <span className="pron-group__title">{gruppo}</span>
              <div className="pron-chips">
                {opzioni.map((m) => (
                  <button
                    key={m.market}
                    type="button"
                    className="pron-chip"
                    aria-pressed={scelta === m.market}
                    onClick={() => onScegli(scelta === m.market ? null : m.market)}
                  >
                    <span className="pron-chip__label">
                      {etichetta(m.market, partita.home_team, partita.away_team)}
                    </span>
                    <span className="pron-chip__nums">
                      <span title="probabilità secondo il modello">{prob(m.probability)}</span>
                      <span className="faint" title="quota equa, 1/p">{fmtOdds(m.fair_odds)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </article>
  );
}

function Esito({ won }: { won: boolean | null }) {
  if (won === null) return <span className="faint">–</span>;
  return (
    <span className={`pron-esito ${won ? "is-ok" : "is-ko"}`}>
      <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6">
        {won ? <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
          : <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />}
      </svg>
      {won ? "uscita" : "non uscita"}
    </span>
  );
}

/**
 * L'archivio: una scheda per giornata, chiusa, con tu contro il modello
 * nell'intestazione. Dentro, partita per partita, la tua giocata e la
 * selezione del modello affiancate, ognuna con il suo esito.
 */
function GiornataArchivio({
  giornata,
  punteggio,
  righe,
  inCorso,
  onRitira,
}: {
  giornata: number;
  punteggio: RoundScore | undefined;
  righe: Pick[];
  inCorso: string | null;
  onRitira: (pick: Pick) => void;
}) {
  const [aperta, setAperta] = useState(false);
  const corpo = useId();
  const attesa = righe.filter((p) => p.won === null).length;

  return (
    <article className={`pron-card${aperta ? " is-open" : ""}`}>
      <h3 className="pron-card__head">
        <button type="button" className="pron-card__toggle pron-arch__toggle"
          aria-expanded={aperta} aria-controls={corpo} onClick={() => setAperta((v) => !v)}>
          <span className="pron-card__teams">
            <span className="pron-card__name">Giornata {giornata}</span>
            <span className="pron-card__meta">
              {righe.length} {righe.length === 1 ? "giocata" : "giocate"}
              {attesa > 0 && ` · ${attesa} in attesa`}
            </span>
          </span>
          {punteggio && punteggio.resolved > 0 ? (
            <span className="pron-arch__score">
              <span><span className="pron-arch__who">Tu</span> {percento(punteggio.won, punteggio.resolved)}
                <span className="faint"> ({punteggio.won}/{punteggio.resolved})</span></span>
              <span><span className="pron-arch__who">Modello</span> {percento(punteggio.model_won, punteggio.model_resolved)}
                <span className="faint"> ({punteggio.model_won}/{punteggio.model_resolved})</span></span>
            </span>
          ) : (
            <span className="pron-arch__score faint">da giocare</span>
          )}
          <span className="pron-card__chevron" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </button>
      </h3>

      <div id={corpo} className="pron-card__body" hidden={!aperta}>
        <div className="std">
          <table className="std__table pron-table">
            <thead>
              <tr>
                <th>Partita</th>
                <th>La tua giocata</th>
                <th className="right">Esito</th>
                <th className="hide-narrow">Il modello</th>
                <th className="right hide-narrow">Esito</th>
              </tr>
            </thead>
            <tbody>
              {righe.map((p) => (
                <tr key={p.id}>
                  <td className="picks__match">
                    {p.home_team} <span className="faint">–</span> {p.away_team}
                    {p.goals_home !== null && (
                      <span className="pron-arch__result"> {score(p.goals_home, p.goals_away)}</span>
                    )}
                  </td>
                  <td>
                    {etichetta(p.market, p.home_team, p.away_team)}
                    {p.model_probability !== null && (
                      <span className="pron-arch__p"> {prob(p.model_probability)}</span>
                    )}
                  </td>
                  <td className="right">
                    {p.won === null && !p.locked ? (
                      <button type="button" className="link-button pron-ritira"
                        onClick={() => onRitira(p)} disabled={inCorso === p.id}
                        aria-label={`Ritira la giocata su ${p.home_team} – ${p.away_team}`}>
                        Ritira
                      </button>
                    ) : p.won === null ? (
                      <span className="faint">in corso</span>
                    ) : (
                      <Esito won={p.won} />
                    )}
                  </td>
                  <td className="hide-narrow">{p.model_label ?? <span className="faint">–</span>}</td>
                  <td className="right hide-narrow"><Esito won={p.model_won} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </article>
  );
}

function Archivio({
  dati,
  inCorso,
  onRitira,
}: {
  dati: MyPredictions;
  inCorso: string | null;
  onRitira: (pick: Pick) => void;
}) {
  const giornate = useMemo(() => {
    const g = new Map<number, Pick[]>();
    for (const p of dati.picks) g.set(p.matchday, [...(g.get(p.matchday) ?? []), p]);
    return [...g.entries()].sort((a, b) => b[0] - a[0]);
  }, [dati]);
  const punteggi = new Map(dati.rounds.map((r) => [r.matchday, r]));

  if (giornate.length === 0) {
    return <p className="profilo__hint">Nessuna giocata ancora. Scegli dalle partite qui sopra e salva.</p>;
  }
  return (
    <div className="pron-list">
      {giornate.map(([giornata, righe]) => (
        <GiornataArchivio key={giornata} giornata={giornata} punteggio={punteggi.get(giornata)}
          righe={righe} inCorso={inCorso} onRitira={onRitira} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function Pronostici() {
  const { team: preferita } = useFavorite();
  const [aperte, setAperte] = useState<OpenMatch[] | null>(null);
  const [dati, setDati] = useState<MyPredictions | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [senzaPermesso, setSenzaPermesso] = useState(false);
  const [inCorso, setInCorso] = useState<string | null>(null);
  // Le scelte non ancora salvate, per partita. Assente = nessuna modifica.
  const [bozze, setBozze] = useState<Map<string, string | null>>(new Map());
  const [salvando, setSalvando] = useState(false);
  const [conferma, setConferma] = useState<string | null>(null);

  const carica = useCallback(async () => {
    try {
      setDati(await me.predictions());
    } catch (e) {
      setErrore(serverError(e, "Non riesco a leggere i tuoi pronostici. Riprova."));
    }
    try {
      setAperte(await me.openMatches());
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setSenzaPermesso(true);
        setAperte([]);
      } else {
        setErrore(serverError(e, "Non riesco a leggere le partite aperte. Riprova."));
      }
    }
  }, []);

  useEffect(() => {
    void carica();
  }, [carica]);

  // Il pronostico salvato di ogni partita: una sola giocata per partita.
  const salvate = useMemo(() => {
    const m = new Map<string, Pick>();
    for (const p of dati?.picks ?? []) m.set(partita(p.home_team, p.away_team), p);
    return m;
  }, [dati]);

  // Le bozze che cambiano davvero qualcosa rispetto al salvato.
  const modifiche = [...bozze.entries()].filter(
    ([k, v]) => v !== (salvate.get(k)?.market ?? null),
  );

  // Lasciare la pagina con modifiche aperte le perderebbe in silenzio.
  useEffect(() => {
    if (modifiche.length === 0) return;
    const avvisa = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", avvisa);
    return () => window.removeEventListener("beforeunload", avvisa);
  }, [modifiche.length]);

  // La conferma "salvato" sparisce da sola dopo qualche secondo.
  useEffect(() => {
    if (!conferma) return;
    const t = window.setTimeout(() => setConferma(null), 4000);
    return () => window.clearTimeout(t);
  }, [conferma]);

  function scegli(home: string, away: string, market: string | null) {
    setConferma(null);
    setBozze((prev) => new Map(prev).set(partita(home, away), market));
  }

  async function salva() {
    setSalvando(true);
    setErrore(null);
    try {
      const picks = modifiche.map(([k, market]) => {
        const [home_team, away_team] = k.split("|") as [string, string];
        return { home_team, away_team, market };
      });
      await me.savePicks(picks);
      setBozze(new Map());
      await carica();
      setConferma(
        modifiche.length === 1 ? "Pronostico salvato." : `${modifiche.length} pronostici salvati.`,
      );
    } catch (e) {
      // Tutto o niente: se il server rifiuta, niente e' cambiato e le bozze
      // restano, cosi' si corregge e si riprova senza ricominciare.
      setErrore(serverError(e, "Non riesco a salvare i pronostici. Riprova."));
    } finally {
      setSalvando(false);
    }
  }

  async function onRitira(pick: Pick) {
    setInCorso(pick.id);
    setErrore(null);
    try {
      await me.deletePick(pick.id);
      await carica();
    } catch (e) {
      setErrore(serverError(e, "Non riesco a ritirare la giocata. Riprova."));
    } finally {
      setInCorso(null);
    }
  }

  // La squadra preferita in cima, poi in ordine di calcio d'inizio.
  const ordinate = useMemo(
    () =>
      [...(aperte ?? [])].sort((a, b) => {
        const fa = preferita && (a.home_team === preferita || a.away_team === preferita) ? 0 : 1;
        const fb = preferita && (b.home_team === preferita || b.away_team === preferita) ? 0 : 1;
        return fa - fb || a.kickoff_utc.localeCompare(b.kickoff_utc);
      }),
    [aperte, preferita],
  );

  return (
    <div className="pronostici">
      <ScreenHeader
        title="I miei pronostici"
        subtitle="Scegli le tue giocate prima del calcio d'inizio: accanto a ognuna, la probabilità del modello e, dopo la partita, com'è andata"
      />

          <FormAlert>{errore}</FormAlert>

          {dati && <Riepilogo dati={dati} />}

          <section className="pron-section" aria-labelledby="aperte-titolo">
            <h2 id="aperte-titolo">Da giocare</h2>
            {aperte === null ? (
              <p className="state">Carico le partite…</p>
            ) : senzaPermesso ? (
              <p className="profilo__hint">
                Le partite non ancora giocate fanno parte dell'abbonamento.{" "}
                <Link to="/account">Vai al tuo account</Link>
              </p>
            ) : ordinate.length === 0 ? (
              <p className="profilo__hint">
                Nessuna partita aperta in questo momento. Le partite della prossima
                giornata si aprono appena si chiude quella in corso; le probabilità
                del modello arrivano con le quote, di solito il venerdì.
              </p>
            ) : (
              <div className="pron-list">
                {ordinate.map((p) => (
                  <PartitaAperta
                    key={`${p.home_team}-${p.away_team}`}
                    partita={p}
                    salvata={salvate.get(partita(p.home_team, p.away_team))}
                    bozza={bozze.get(partita(p.home_team, p.away_team))}
                    onScegli={(market) => scegli(p.home_team, p.away_team, market)}
                    preferita={!!preferita && (p.home_team === preferita || p.away_team === preferita)}
                  />
                ))}
              </div>
            )}
          </section>

          {/* LA BARRA DI SALVATAGGIO, in fondo e sempre raggiungibile: compare
              solo quando c'e' qualcosa da salvare. La conferma e' una regione
              di stato: lo screen reader la annuncia senza spostare il fuoco. */}
          {(modifiche.length > 0 || conferma) && (
            <div className="pron-savebar" role="region" aria-label="Salvataggio dei pronostici">
              {modifiche.length > 0 ? (
                <>
                  <span className="pron-savebar__text">
                    {modifiche.length === 1
                      ? "1 pronostico da salvare"
                      : `${modifiche.length} pronostici da salvare`}
                  </span>
                  <button type="button" className="btn btn--ghost" onClick={() => setBozze(new Map())}
                    disabled={salvando}>
                    Annulla
                  </button>
                  <button type="button" className="btn" onClick={() => void salva()}
                    disabled={salvando} aria-busy={salvando || undefined}>
                    {salvando && <span className="spinner" aria-hidden="true" />}
                    {salvando ? "Salvo…" : "Salva i pronostici"}
                  </button>
                </>
              ) : null}
              <span className="pron-savebar__ok" role="status">
                {conferma && (
                  <>
                    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                      <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {conferma}
                  </>
                )}
              </span>
            </div>
          )}

          <section className="pron-section" aria-labelledby="storico-titolo">
            <h2 id="storico-titolo">Archivio delle giornate</h2>
            <p className="pron-section__lead">
              Ogni giornata con le tue giocate e, sulle stesse partite, la selezione del modello.
            </p>
            {dati === null ? (
              !errore && <p className="state">Carico l'archivio…</p>
            ) : (
              <Archivio dati={dati} inCorso={inCorso} onRitira={(p) => void onRitira(p)} />
            )}
          </section>
    </div>
  );
}
