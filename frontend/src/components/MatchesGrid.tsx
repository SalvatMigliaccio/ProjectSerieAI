import type { Match, Selection } from "../api/types";
import { odds as fmtOdds, prob, score } from "../lib/format";

/**
 * La griglia delle partite, raggruppata per giorno.
 *
 * LA SOGLIA DI QUOTA SI VEDE QUI, NON SOLO NELLA COLONNA A FIANCO. Cambiando
 * soglia, la cella che quella soglia sceglie per ogni partita si accende: il
 * filtro agisce sulla giornata, altrimenti resterebbe un dettaglio confinato
 * in una scheda e non si capirebbe cosa sta selezionando.
 *
 * LE QUOTE NON SONO TUTTE DELLA STESSA NATURA, E LA TABELLA LO DICE. Il
 * registro conserva il prezzo del bookmaker solo per l'1X2; per over/under e
 * gol-gol al suo posto c'e' la QUOTA EQUA (1/p), il prezzo a valore atteso
 * zero. Stimare il prezzo del book con un margine medio sarebbe un numero
 * inventato con l'aria di essere misurato.
 *
 * L'ESITO SEGUE LA SELEZIONE MOSTRATA. La pastiglia del risultato dice se ha
 * vinto **quello che la riga sta evidenziando**: la selezione della soglia se
 * c'e', altrimenti l'1X2 di M1. Lasciarla ferma sull'1X2 mentre la cella accesa
 * e' "no gol" farebbe leggere "indovinato" accanto a una selezione persa, che e'
 * il modo piu' rapido di rendere il track record incomprensibile.
 *
 * NESSUNA BARRA DI SCORRIMENTO: la tabella e' a larghezze fisse e sta dentro
 * la colonna. Dover trascinare per leggere l'ultima colonna e' il modo piu'
 * rapido di non farla leggere a nessuno.
 */

export type Market = "all" | "1x2" | "ou" | "gg" | "result";
export type SortBy = "time" | "prob";

/** Dal nome del mercato alla colonna della griglia. I mercati che non hanno
 *  una colonna (doppia chance, over 1.5, "segna") restano fuori di proposito:
 *  compaiono come etichetta sotto i nomi delle squadre. */
const COLONNA: Record<string, string> = {
  "1": "1",
  X: "X",
  "2": "2",
  "over 2.5": "O",
  "under 2.5": "U",
  "gol-gol": "GG",
  "no gol": "NG",
};

const GIORNI = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MESI = [
  "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
  "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
];

export const chiave = (match: { home_team: string; away_team: string }): string =>
  `${match.home_team}-${match.away_team}`;

function giorno(match: Match): string {
  const iso = match.kickoff_utc ?? match.match_date;
  if (!iso) return "data da definire";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "data da definire";
  const nome = GIORNI[date.getDay()] ?? "";
  return `${nome.charAt(0).toUpperCase()}${nome.slice(1)} ${date.getDate()} ${
    MESI[date.getMonth()] ?? ""
  } ${date.getFullYear()}`;
}

function ora(match: Match): string {
  const iso = match.kickoff_utc;
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}

function topProb(match: Match): number {
  return Math.max(match.p_home ?? 0, match.p_draw ?? 0, match.p_away ?? 0);
}

const equa = (p: number | null): number | null => (p && p > 0 ? 1 / p : null);

function Pick({
  value,
  odd,
  tone,
  best = false,
  picked = false,
  persa = false,
}: {
  value: number | null;
  odd: number | null;
  tone: string;
  best?: boolean;
  picked?: boolean;
  /** Solo per la cella accesa, e solo a partita giocata. */
  persa?: boolean;
}) {
  return (
    <div
      className={`pick${best ? " pick--best" : ""}${picked ? " pick--picked" : ""}${
        picked && persa ? " pick--persa" : ""
      }`}
    >
      <span className="pick__pct">{prob(value)}</span>
      <span className="pick__bar">
        <i style={{ width: `${(value ?? 0) * 100}%`, background: tone }} />
      </span>
      <span className="pick__odds">{fmtOdds(odd)}</span>
    </div>
  );
}

function Row({
  match,
  market,
  selection,
}: {
  match: Match;
  market: Market;
  selection: Selection | undefined;
}) {
  const over = match.p_over25;
  const under = over === null ? null : 1 - over;
  const gg = match.p_btts;
  const ng = gg === null ? null : 1 - gg;
  const played = match.goals_home !== null && match.goals_away !== null;

  const show1x2 = market === "all" || market === "1x2";
  const showOu = market === "all" || market === "ou";
  const showGg = market === "all" || market === "gg";

  const colonna = selection ? COLONNA[selection.market] : undefined;
  const visibile =
    (colonna === "1" || colonna === "X" || colonna === "2") ? show1x2
    : (colonna === "O" || colonna === "U") ? showOu
    : (colonna === "GG" || colonna === "NG") ? showGg
    : false;
  const scelta = (chiave: string) => visibile && colonna === chiave;
  const persa = selection?.won === false;

  // L'esito della riga e' quello della cosa evidenziata: la selezione della
  // soglia quando c'e', l'1X2 di M1 quando la banda non copre la partita.
  const esito = selection ? selection.won : match.correct;
  const chip = !played
    ? "score-chip"
    : esito === true
      ? "score-chip score-chip--ok"
      : esito === false
        ? "score-chip score-chip--ko"
        : "score-chip";
  const spiegazione = !played
    ? undefined
    : selection
      ? `${selection.market_label}: ${esito === true ? "uscito" : esito === false ? "non uscito" : "non risolvibile"}`
      : "esito dell'1X2 previsto da M1";

  return (
    <tr>
      <td>
        <div className="match-cell">
          <span className="match-cell__home">{match.home_team}</span>
          <span className="match-cell__time">{ora(match)}</span>
          <span className="match-cell__away">{match.away_team}</span>
          {selection && !visibile && (
            <span className={`match-cell__flag${persa ? " match-cell__flag--persa" : ""}`}>
              ▸ {selection.market_label} · {fmtOdds(selection.fair_odds)}
            </span>
          )}
        </div>
      </td>

      {show1x2 && (
        <>
          <td data-k="1">
            <Pick value={match.p_home} odd={match.odds_home} tone="var(--cat-1)"
                  best={match.predicted_outcome === "H"} picked={scelta("1")} persa={persa} />
          </td>
          <td data-k="X">
            <Pick value={match.p_draw} odd={match.odds_draw} tone="var(--cat-x)"
                  best={match.predicted_outcome === "D"} picked={scelta("X")} persa={persa} />
          </td>
          <td data-k="2">
            <Pick value={match.p_away} odd={match.odds_away} tone="var(--cat-2)"
                  best={match.predicted_outcome === "A"} picked={scelta("2")} persa={persa} />
          </td>
        </>
      )}

      {showOu && (
        <>
          <td data-k="O">
            <Pick value={over} odd={equa(over)} tone="var(--mint)"
                  best={(over ?? 0) > 0.5} picked={scelta("O")} persa={persa} />
          </td>
          <td data-k="U">
            <Pick value={under} odd={equa(under)} tone="var(--cat-1)"
                  best={(under ?? 0) > 0.5} picked={scelta("U")} persa={persa} />
          </td>
        </>
      )}

      {showGg && (
        <>
          <td data-k="GG">
            <Pick value={gg} odd={equa(gg)} tone="var(--mint)"
                  best={(gg ?? 0) > 0.5} picked={scelta("GG")} persa={persa} />
          </td>
          <td data-k="NG">
            <Pick value={ng} odd={equa(ng)} tone="var(--cat-2)"
                  best={(ng ?? 0) > 0.5} picked={scelta("NG")} persa={persa} />
          </td>
        </>
      )}

      <td style={{ textAlign: "center" }}>
        {played ? (
          <span className={chip} title={spiegazione}>
            {score(match.goals_home, match.goals_away)}
          </span>
        ) : (
          <span className="faint">–</span>
        )}
      </td>
    </tr>
  );
}

export function MatchesGrid({
  matches,
  market,
  sort,
  picked,
  soglia,
}: {
  matches: Match[];
  market: Market;
  sort: SortBy;
  /** Selezione scelta dalla soglia, per partita. */
  picked?: Map<string, Selection>;
  soglia?: number;
}) {
  const ordinate = [...matches].sort((a, b) =>
    sort === "prob"
      ? topProb(b) - topProb(a)
      : (a.kickoff_utc ?? a.match_date ?? "").localeCompare(b.kickoff_utc ?? b.match_date ?? ""),
  );

  const gruppi = new Map<string, Match[]>();
  for (const match of ordinate) {
    const titolo = sort === "prob" ? "" : giorno(match);
    gruppi.set(titolo, [...(gruppi.get(titolo) ?? []), match]);
  }

  const show1x2 = market === "all" || market === "1x2";
  const showOu = market === "all" || market === "ou";
  const showGg = market === "all" || market === "gg";
  const nPick = (show1x2 ? 3 : 0) + (showOu ? 2 : 0) + (showGg ? 2 : 0);

  // Il conto delle selezioni mostrate: si ricalcola con la soglia, come tutto
  // il resto. Solo le partite gia' giocate, altrimenti direbbe "0 su 10" per
  // una giornata che deve ancora cominciare.
  const scelte = matches
    .map((match) => picked?.get(chiave(match)))
    .filter((s): s is Selection => s !== undefined && s.won !== null);
  const vinte = scelte.filter((s) => s.won).length;

  return (
    <>
      {[...gruppi.entries()].map(([titolo, righe]) => (
        <div key={titolo || "tutte"}>
          {titolo && (
            <h2 className="day-head">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <rect x="3" y="5" width="18" height="16" rx="2" />
                <path d="M8 3v4M16 3v4M3 11h18" strokeLinecap="round" />
              </svg>
              {titolo}
            </h2>
          )}

          <div className="grid-wrap">
            <table className="grid">
              <colgroup>
                <col className="c-match" />
                {Array.from({ length: nPick }, (_, i) => <col className="c-pick" key={i} />)}
                <col className="c-result" />
              </colgroup>
              <thead>
                <tr className="groups">
                  <th className="left" rowSpan={2}>Partita</th>
                  {show1x2 && <th colSpan={3}>1 · X · 2</th>}
                  {showOu && <th colSpan={2}>Over/Under 2.5</th>}
                  {showGg && <th colSpan={2}>Goal/No Goal</th>}
                  <th rowSpan={2}>Risultato</th>
                </tr>
                <tr className="keys">
                  {show1x2 && (<><th>1</th><th>X</th><th>2</th></>)}
                  {showOu && (<><th>O</th><th>U</th></>)}
                  {showGg && (<><th>GG</th><th>NG</th></>)}
                </tr>
              </thead>
              <tbody>
                {righe.map((match) => (
                  <Row
                    key={chiave(match)}
                    match={match}
                    market={market}
                    selection={picked?.get(chiave(match))}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      <p className="small faint" style={{ marginTop: "var(--s4)" }}>
        {soglia !== undefined && (
          <>
            Le celle accese sono le selezioni con quota sopra{" "}
            <strong>{soglia.toFixed(2)}</strong>; dove il mercato scelto non ha
            una colonna, compare sotto i nomi delle squadre. Il risultato è
            verde o rosso secondo <strong>quella</strong> selezione, non secondo
            l'1X2{scelte.length > 0 && (
              <>
                : <strong>{vinte} su {scelte.length}</strong> in questa giornata
              </>
            )}
            .{" "}
          </>
        )}
        Sotto ogni percentuale c'è la quota: per l'<strong>1X2</strong> è quella
        registrata da B365 al momento della previsione, per over/under e gol-gol
        è la <strong>quota equa</strong> (1/p), il prezzo a valore atteso zero.
      </p>
    </>
  );
}
