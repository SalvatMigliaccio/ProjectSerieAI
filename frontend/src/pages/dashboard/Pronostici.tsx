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
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { me, type MyPredictions, type OpenMatch, type Pick } from "../../api/me";
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

const chiave = (home: string, away: string, market: string) => `${home}|${away}|${market}`;

// ---------------------------------------------------------------------------

function Riepilogo({ dati }: { dati: MyPredictions }) {
  const s = dati.summary;
  const meglio =
    s.hit_rate !== null && s.mean_model_probability !== null
      ? s.hit_rate - s.mean_model_probability
      : null;
  return (
    <section className="pron-stats" aria-label="Riepilogo">
      <div className="pron-stat">
        <span className="pron-stat__label">Giocate</span>
        <strong className="pron-stat__value">{s.picks}</strong>
        <span className="pron-stat__note">{s.pending} in attesa</span>
      </div>
      <div className="pron-stat">
        <span className="pron-stat__label">Uscite</span>
        <strong className="pron-stat__value">
          {s.resolved ? `${s.won}/${s.resolved}` : "–"}
        </strong>
        <span className="pron-stat__note">{s.hit_rate === null ? "nessuna ancora giocata" : prob(s.hit_rate)}</span>
      </div>
      <div className="pron-stat">
        <span className="pron-stat__label">Il modello si aspettava</span>
        <strong className="pron-stat__value">{prob(s.mean_model_probability)}</strong>
        <span className="pron-stat__note">probabilità media delle tue giocate</span>
      </div>
      <p className="pron-stats__verdict">
        {meglio === null
          ? "Il confronto compare quando le prime giocate avranno un risultato."
          : Math.abs(meglio) < 0.02
            ? "Le tue giocate sono uscite quanto il modello si aspettava."
            : meglio > 0
              ? `Le tue giocate sono uscite più spesso di quanto il modello si aspettasse (+${Math.round(meglio * 100)} punti).`
              : `Le tue giocate sono uscite meno spesso di quanto il modello si aspettasse (${Math.round(meglio * 100)} punti).`}
        {s.resolved > 0 && s.resolved < 30 && " Con meno di 30 giocate concluse, la differenza è soprattutto caso."}
      </p>
    </section>
  );
}

function PartitaAperta({
  partita,
  ids,
  inCorso,
  onToggle,
  preferita,
}: {
  partita: OpenMatch;
  ids: Map<string, string>;
  inCorso: string | null;
  onToggle: (partita: OpenMatch, market: string) => void;
  preferita: boolean;
}) {
  const gruppi = new Map<string, OpenMatch["markets"]>();
  for (const m of partita.markets) gruppi.set(m.group, [...(gruppi.get(m.group) ?? []), m]);
  const scelte = partita.markets.filter((m) => m.picked).length;

  return (
    <article className={`pron-match${preferita ? " is-fav" : ""}`}
      aria-labelledby={`pm-${partita.home_team}-${partita.away_team}`}>
      <header className="pron-match__head">
        <h3 id={`pm-${partita.home_team}-${partita.away_team}`}>
          {partita.home_team} <span className="faint">–</span> {partita.away_team}
        </h3>
        <p className="pron-match__meta">
          Giornata {partita.matchday} · {localTime(partita.kickoff_utc)} ·{" "}
          <time dateTime={partita.kickoff_utc}>{tra(partita.kickoff_utc)}</time>
          {!partita.priced && (
            <span className="pron-match__pending" title="Le probabilità del modello compaiono quando escono le quote, di solito il venerdì">
              quote in arrivo
            </span>
          )}
          {scelte > 0 && <span className="pron-match__count">{scelte} {scelte === 1 ? "giocata" : "giocate"}</span>}
        </p>
      </header>
      <div className="pron-groups">
        {[...gruppi.entries()].map(([gruppo, opzioni]) => (
          <div className="pron-group" key={gruppo} role="group" aria-label={gruppo}>
            <span className="pron-group__title">{gruppo}</span>
            <div className="pron-chips">
              {opzioni.map((m) => {
                const k = chiave(partita.home_team, partita.away_team, m.market);
                const occupato = inCorso === k;
                return (
                  <button
                    key={m.market}
                    type="button"
                    className="pron-chip"
                    aria-pressed={m.picked}
                    disabled={occupato || (m.picked && !ids.has(k))}
                    aria-busy={occupato || undefined}
                    onClick={() => onToggle(partita, m.market)}
                  >
                    <span className="pron-chip__label">
                      {etichetta(m.market, partita.home_team, partita.away_team)}
                    </span>
                    <span className="pron-chip__nums">
                      <span title="probabilità secondo il modello">{prob(m.probability)}</span>
                      <span className="faint" title="quota equa, 1/p">{fmtOdds(m.fair_odds)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

function Storico({
  picks,
  inCorso,
  onRitira,
}: {
  picks: Pick[];
  inCorso: string | null;
  onRitira: (pick: Pick) => void;
}) {
  const giornate = useMemo(() => {
    const g = new Map<number, Pick[]>();
    for (const p of picks) g.set(p.matchday, [...(g.get(p.matchday) ?? []), p]);
    return [...g.entries()].sort((a, b) => b[0] - a[0]);
  }, [picks]);

  if (picks.length === 0) {
    return <p className="profilo__hint">Nessuna giocata ancora. Scegli dalle partite qui sopra.</p>;
  }

  return (
    <>
      {giornate.map(([giornata, righe]) => (
        <div key={giornata} className="pron-storico">
          <h3 className="day-head">Giornata {giornata}</h3>
          <div className="std">
            <table className="std__table pron-table">
              <thead>
                <tr>
                  <th>Partita</th>
                  <th>Giocata</th>
                  <th className="right" title="probabilità che le dava il modello">Modello</th>
                  <th className="right hide-narrow" title="1/p">Quota equa</th>
                  <th className="right">Esito</th>
                </tr>
              </thead>
              <tbody>
                {righe.map((p) => (
                  <tr key={p.id}>
                    <td className="picks__match">
                      {p.home_team} <span className="faint">–</span> {p.away_team}
                    </td>
                    <td>{etichetta(p.market, p.home_team, p.away_team)}</td>
                    <td className="right num">{prob(p.model_probability)}</td>
                    <td className="right num hide-narrow">{fmtOdds(p.fair_odds)}</td>
                    <td className="right">
                      {p.won === null ? (
                        p.locked ? (
                          <span className="faint">in corso</span>
                        ) : (
                          <button
                            type="button"
                            className="link-button pron-ritira"
                            onClick={() => onRitira(p)}
                            disabled={inCorso === p.id}
                            aria-label={`Ritira la giocata ${etichetta(p.market, p.home_team, p.away_team)} su ${p.home_team} – ${p.away_team}`}
                          >
                            Ritira
                          </button>
                        )
                      ) : (
                        <span className={`score-chip score-chip--${p.won ? "ok" : "ko"}`}>
                          <span className="visually-hidden">{p.won ? "Uscita: " : "Non uscita: "}</span>
                          {score(p.goals_home, p.goals_away)}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
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

  // L'id di ogni giocata, per poterla ritirare cliccando di nuovo la stessa.
  const ids = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of dati?.picks ?? []) m.set(chiave(p.home_team, p.away_team, p.market), p.id);
    return m;
  }, [dati]);

  async function onToggle(partita: OpenMatch, market: string) {
    const k = chiave(partita.home_team, partita.away_team, market);
    setInCorso(k);
    setErrore(null);
    try {
      const esistente = ids.get(k);
      if (esistente) await me.deletePick(esistente);
      else await me.addPick(partita.home_team, partita.away_team, market);
      await carica();
    } catch (e) {
      setErrore(serverError(e, "Non riesco a salvare la giocata. Riprova."));
      await carica();
    } finally {
      setInCorso(null);
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
                    ids={ids}
                    inCorso={inCorso}
                    onToggle={(partita, market) => void onToggle(partita, market)}
                    preferita={!!preferita && (p.home_team === preferita || p.away_team === preferita)}
                  />
                ))}
              </div>
            )}
          </section>

          <section className="pron-section" aria-labelledby="storico-titolo">
            <h2 id="storico-titolo">Le tue giocate</h2>
            {dati === null ? (
              !errore && <p className="state">Carico lo storico…</p>
            ) : (
              <Storico picks={dati.picks} inCorso={inCorso} onRitira={(p) => void onRitira(p)} />
            )}
          </section>
    </div>
  );
}
