/**
 * Il modello AI partita per partita, in parole e richiudibile.
 *
 * PRIMA SI LEGGEVA COME UN LOG: "AI: 2" (un codice — 2 vuol dire vittoria in
 * trasferta), "scarto max 2.5 pt" (rispetto a cosa? a favore di chi?), un
 * punteggio senza dire se l'AI ci aveva preso, e dieci righe aperte che
 * riempivano la pagina. Ora ogni riga dice tre cose in italiano:
 *
 *   cosa prevede l'AI      "vince l'Inter, 46%"
 *   come si scosta         "rispetto alle quote: Inter +2,5 punti", oppure
 *                          "in linea con le quote" quando lo scarto e' minimo
 *   com'e' andata          "finita 1–3 · 1X2 giusto", con un'icona
 *
 * "1X2" SCRITTO SEMPRE, e non per pignoleria. Sopra questo riquadro c'e' la
 * tabella delle selezioni, che conta le giocate piu' larghe (doppia chance,
 * under 3.5...) e quindi indovina di piu': una sintesi che diceva "esito
 * indovinato in 7 su 10" sotto una tabella che ne mostra 9 su 10 sembrava un
 * errore. Erano due conteggi diversi con lo stesso nome.
 *
 * LA SEZIONE E' UNA FINESTRA CHE SI APRE E SI CHIUDE, chiusa di default: da
 * chiusa resta una riga di sintesi, da aperta mostra le prime cinque partite
 * e le altre a richiesta. Il dettaglio di ogni partita — tutti i mercati, AI
 * contro quote — si apre a parte.
 *
 * Lo scarto resta il dato centrale, perche' l'AI parte dal mercato e ne impara
 * la correzione: dove lo scarto e' zero, l'AI non ha niente da dire, e la riga
 * lo dice invece di mostrare "0.2 pt".
 */

import { useId, useState } from "react";

import { api } from "../api/client";
import type { MLMatch } from "../api/types";
import { useApi } from "../hooks/useApi";
import { localTime, prob, score } from "../lib/format";
import { EmptyState, ErrorState, Loading } from "./States";

/** Sotto questo scarto (in probabilita') l'AI e' "in linea con le quote". */
const IN_LINEA = 0.01;
const PRIME = 5;

function punti(d: number): string {
  const v = Math.round(d * 1000) / 10;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1).replace(".", ",")}`;
}

/** "1" / "X" / "2" detti con le squadre. */
function esito(market: string, m: MLMatch): string {
  if (market === "1") return `vince ${m.home_team}`;
  if (market === "2") return `vince ${m.away_team}`;
  return "pareggio";
}

function chi(market: string, m: MLMatch): string {
  if (market === "1") return m.home_team;
  if (market === "2") return m.away_team;
  return "pareggio";
}

function Icona({ ok }: { ok: boolean }) {
  return ok ? (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6">
      <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6">
      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </svg>
  );
}

function Riga({ m }: { m: MLMatch }) {
  const uno = m.markets.filter((mk) => mk.group === "1X2");
  const scelta = uno.find((mk) => mk.pick);
  // L'esito su cui l'AI si sposta di piu' rispetto alle quote.
  const spostato = [...uno].sort((a, b) => Math.abs(b.difference) - Math.abs(a.difference))[0];
  const giocata = m.goals_home !== null && m.goals_away !== null;
  const giusta = giocata && scelta ? scelta.won === true : null;

  const gruppi = new Map<string, MLMatch["markets"]>();
  for (const mk of m.markets) gruppi.set(mk.group, [...(gruppi.get(mk.group) ?? []), mk]);

  return (
    <details className="aimc">
      <summary className="aimc__summary">
        <span className="aimc__teams">
          {m.home_team} <span className="faint">–</span> {m.away_team}
          <span className="aimc__when">{localTime(m.kickoff_utc)}</span>
        </span>

        <span className="aimc__cell">
          <span className="aimc__key">Risultato previsto (1X2)</span>
          <span className="aimc__val">
            {scelta ? <>{esito(scelta.market, m)} <span className="aimc__num">{prob(scelta.p_ml)}</span></> : "–"}
          </span>
        </span>

        <span className="aimc__cell">
          <span className="aimc__key">Rispetto alle quote</span>
          <span className="aimc__val">
            {!spostato || Math.abs(spostato.difference) < IN_LINEA ? (
              <span className="faint">in linea con le quote</span>
            ) : (
              <>
                {chi(spostato.market, m)}{" "}
                <span className="aimc__num">{punti(spostato.difference)} punti</span>
              </>
            )}
          </span>
        </span>

        <span className="aimc__cell aimc__cell--esito">
          {giocata ? (
            <>
              <span className="aimc__key">Finita {score(m.goals_home, m.goals_away)}</span>
              {giusta !== null && (
                <span className={`aimc__verdetto ${giusta ? "is-ok" : "is-ko"}`}>
                  <Icona ok={giusta} />
                  {giusta ? "1X2 giusto" : "1X2 sbagliato"}
                </span>
              )}
            </>
          ) : (
            <span className="aimc__key">da giocare</span>
          )}
        </span>

        <span className="aimc__chevron" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </summary>

      <div className="aimc__body">
        <p className="aimc__lambda">
          Gol attesi secondo l'AI: <strong>{m.home_team} {m.lambda_home_ml.toFixed(2).replace(".", ",")}</strong>,{" "}
          <strong>{m.away_team} {m.lambda_away_ml.toFixed(2).replace(".", ",")}</strong>
          <span className="faint"> · secondo le quote {m.lambda_home_market.toFixed(2).replace(".", ",")} e{" "}
            {m.lambda_away_market.toFixed(2).replace(".", ",")}</span>
        </p>
        <div className="std">
          <table className="std__table aimc__table">
            <thead>
              <tr>
                <th>Mercato</th>
                <th className="right">AI</th>
                <th className="right">Quote</th>
                <th className="right" title="Probabilità dell'AI meno quella delle quote, in punti percentuali">Differenza</th>
                {giocata && <th className="right">Esito</th>}
              </tr>
            </thead>
            {[...gruppi.entries()].map(([gruppo, righe]) => (
              <tbody key={gruppo}>
                <tr className="aimc__group"><th colSpan={giocata ? 5 : 4}>{gruppo}</th></tr>
                {righe.map((r) => (
                  <tr key={r.market} className={r.pick ? "is-pick" : undefined}>
                    <td>
                      {gruppo === "1X2" ? esito(r.market, m) : r.label}
                      {r.pick && <span className="aimc__tag">scelta AI</span>}
                    </td>
                    <td className="right">{prob(r.p_ml)}</td>
                    <td className="right faint">{prob(r.p_market)}</td>
                    <td className={`right${Math.abs(r.difference) >= 0.02 ? " aimc__big" : ""}`}>
                      {punti(r.difference)}
                    </td>
                    {giocata && (
                      <td className="right">
                        {r.won === null ? "–" : r.won ? (
                          <span className="aimc__won">uscito</span>
                        ) : (
                          <span className="aimc__lost">no</span>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </div>
    </details>
  );
}

export function AiMatchCards({ matchday }: { matchday: number | null }) {
  const [aperta, setAperta] = useState(false);
  const [tutte, setTutte] = useState(false);
  const corpo = useId();
  const state = useApi(
    () => (matchday === null ? Promise.resolve(null) : api.ml(matchday)),
    [matchday],
  );
  if (matchday === null) return null;

  const partite = [...(state.data?.matches ?? [])].sort(
    (a, b) => b.max_divergence_1x2 - a.max_divergence_1x2,
  );
  const giocate = partite.filter((m) => m.goals_home !== null);
  const giuste = giocate.filter((m) => m.markets.find((mk) => mk.group === "1X2" && mk.pick)?.won).length;
  const prima = partite[0];

  const sintesi = state.loading
    ? "carico…"
    : !partite.length
      ? "non ancora disponibile per questa giornata"
      : giocate.length
        ? `${partite.length} partite · risultato finale (1X2) indovinato in ${giuste} su ${giocate.length}`
        : `${partite.length} partite · scostamento più grande dalle quote: ${prima ? `${prima.home_team}–${prima.away_team}` : "–"}`;

  const visibili = tutte ? partite : partite.slice(0, PRIME);

  return (
    <section className="aimc-panel">
      {/* LA FINESTRA SI APRE E SI CHIUDE con un pulsante vero, che dice il
          proprio stato (aria-expanded) e quale zona controlla. */}
      <h3 className="aimc-panel__head">
        <button
          type="button"
          className="aimc-panel__toggle"
          aria-expanded={aperta}
          aria-controls={corpo}
          onClick={() => setAperta((v) => !v)}
        >
          <span className="aimc-panel__title">Modello AI partita per partita</span>
          <span className="aimc-panel__sum">{sintesi}</span>
          <span className="aimc-panel__action">
            {aperta ? "Chiudi" : "Apri"}
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </button>
      </h3>

      <div id={corpo} className="aimc-panel__body" hidden={!aperta}>
        {state.error && <ErrorState error={state.error} what="Modello AI non disponibile." />}
        {state.loading && <Loading what="carico il modello AI…" />}
        {!state.loading && !state.error && partite.length === 0 && (
          <EmptyState>
            Il modello AI non ha ancora girato sulla giornata {matchday}: parte con le quote,
            subito dopo il modello classico.
          </EmptyState>
        )}
        {partite.length > 0 && (
          <>
            <p className="aimc-panel__hint">
              Qui conta il <strong>risultato finale</strong> (vince la squadra di casa, pareggio,
              vince quella in trasferta) che l'AI ritiene più probabile: è una previsione più secca
              delle selezioni qui sopra, che usano anche doppia chance, under/over e gol, e per
              questo esce meno spesso. In alto le partite in cui l'AI si allontana di più dalle
              quote; tocca una partita per vedere tutti i mercati.
            </p>
            {visibili.map((m) => (
              <Riga key={`${m.home_team}-${m.away_team}`} m={m} />
            ))}
            {partite.length > PRIME && (
              <button type="button" className="btn btn--ghost aimc-panel__more"
                onClick={() => setTutte((v) => !v)}>
                {tutte ? "Mostra meno" : `Mostra le altre ${partite.length - PRIME}`}
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
