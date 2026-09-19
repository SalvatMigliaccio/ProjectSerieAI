import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { Match, Round, SeasonSummary, Selection, TrackRecord } from "../api/types";
import { AsideSelections } from "../components/AsideSelections";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Colophon, Eyebrow, Masthead } from "../components/Layout";
import { MatchesGrid, chiave, type Market, type SortBy } from "../components/MatchesGrid";
import { ModelPicks } from "../components/ModelPicks";
import { StandingsPanel } from "../components/StandingsPanel";
import { EmptyState, ErrorState, Loading } from "../components/States";
import { TrackRecordPanel } from "../components/TrackRecordPanel";
import { useApi } from "../hooks/useApi";
import { ROUND_LABELS, localDateTime } from "../lib/format";
import { scorrimento } from "../lib/motion";

const MERCATI: Array<[Market, string]> = [
  ["all", "Tutte le partite"],
  ["1x2", "1X2"],
  ["ou", "Over/Under"],
  ["gg", "Goal/No Goal"],
  ["result", "Risultato finale"],
];

/** Una giornata non chiusa e gia' cominciata e' "in corso"; le altre prendono
 *  l'etichetta dello stato che l'API dichiara. */
function badge(round: Round | null): { testo: string; forte: boolean } {
  if (!round) return { testo: "", forte: false };
  if (round.closed) return { testo: "chiusa", forte: false };
  if (round.status === "played") return { testo: "giocata", forte: false };
  if (round.status === "future") return { testo: "futura", forte: false };
  return { testo: "in corso", forte: true };
}

function giorno(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleDateString("it-IT", { weekday: "short", day: "2-digit", month: "2-digit" });
}

export function Dashboard() {
  const [selected, setSelected] = useState<number | null>(null);
  const [market, setMarket] = useState<Market>("all");
  const [sort, setSort] = useState<SortBy>("time");
  // La soglia di quota sta QUI e non nella scheda laterale: sceglierla accende
  // anche le celle della griglia, quindi e' stato della pagina. Vedi
  // AsideSelections per il perche' la linea principale del modello, invece,
  // non e' regolabile da nessuna parte.
  //
  // PARTE DA 1.20 perche' e' la soglia a cui si gioca davvero. A quel prezzo la
  // selezione piu' probabile e' quasi sempre un mercato senza colonna in
  // tabella (over 1.5, doppia chance, "segna"), e infatti compare come
  // etichetta sotto i nomi delle squadre invece che come cella accesa: non e'
  // un difetto della griglia, e' dove sta la selezione a quella quota.
  const [soglia, setSoglia] = useState<number>(1.2);
  const track = useRef<HTMLDivElement>(null);

  const status = useApi(() => api.status(), []);
  const rounds = useApi(() => api.rounds(), []);
  const record = useApi<{ record: TrackRecord; summary: SeasonSummary }>(
    () =>
      Promise.all([api.trackRecord(), api.season()]).then(([r, s]) => ({
        record: r,
        summary: s,
      })),
    [],
  );

  useEffect(() => {
    if (selected !== null || !rounds.data) return;
    const list = rounds.data;
    const corrente = list.find((r) => r.matchday === status.data?.current_round);
    const conPrevisioni = list.filter((r) => (r.matches_predicted ?? 0) > 0);
    const iniziale =
      corrente && (corrente.matches_predicted ?? 0) > 0
        ? corrente
        : (conPrevisioni.at(-1) ?? corrente ?? list[0]);
    if (iniziale) setSelected(iniziale.matchday);
  }, [rounds.data, status.data, selected]);

  const matches = useApi<Match[] | null>(
    () => (selected === null ? Promise.resolve(null) : api.roundMatches(selected)),
    [selected],
  );

  // Una sola chiamata per la banda: la stessa risposta accende la griglia e
  // riempie la scheda laterale.
  const banda = useApi(() => api.selections(soglia, 100), [soglia]);

  const scelte = new Map<string, Selection>();
  for (const selection of banda.data?.best_per_match ?? []) {
    if (selected === null || selection.matchday === selected) {
      scelte.set(chiave(selection), selection);
    }
  }

  const round = rounds.data?.find((r) => r.matchday === selected) ?? null;
  const stato = badge(round);

  const runs = status.data?.last_runs ?? [];
  const ultimaPrevisione =
    runs.find((r) => r.command === "predict_round")?.finished_at ??
    (matches.data ?? [])
      .map((m) => m.timestamp_prediction)
      .filter((v): v is string => v !== null)
      .sort()
      .at(-1) ??
    null;
  const ultimaChiusura =
    runs.find((r) => r.command === "close_round")?.finished_at ??
    (rounds.data ?? []).filter((r) => r.closed).at(-1)?.last_date ??
    null;

  const scorri = (verso: number) => {
    track.current?.scrollBy({ left: verso * 320, behavior: scorrimento() });
  };

  return (
    <div className="page--wide">
      <Masthead current="dashboard" />

      <main className="wrap" id="contenuto" tabIndex={-1}>
        <section className="dash-banner">
          <div className="dash-banner__title">
            <div className="dash-banner__row">
              <h1>{selected === null ? "Giornata" : `Giornata ${selected}`}</h1>
              {stato.testo && (
                <span className={`badge${stato.forte ? "" : " badge--quiet"}`}>
                  {stato.testo}
                </span>
              )}
            </div>
            <span className="dash-banner__dates">
              {round?.first_date && round?.last_date
                ? `${giorno(round.first_date)} – ${giorno(round.last_date)}`
                : "Serie A"}
              {round?.status ? ` · ${ROUND_LABELS[round.status] ?? ""}` : ""}
            </span>
          </div>

          <div className="dash-chips">
            <div className="dash-chip">
              <span className="dash-chip__glyph">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <rect x="3" y="5" width="18" height="16" rx="2" />
                  <path d="M8 3v4M16 3v4M3 11h18" strokeLinecap="round" />
                </svg>
              </span>
              <span>
                <span className="dash-chip__label">Ultima previsione</span>
                <span className="dash-chip__value">
                  {ultimaPrevisione ? localDateTime(ultimaPrevisione) : "—"}
                </span>
              </span>
            </div>

            <div className="dash-chip">
              <span className="dash-chip__glyph">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 2" strokeLinecap="round" />
                </svg>
              </span>
              <span>
                <span className="dash-chip__label">Ultima chiusura</span>
                <span className="dash-chip__value">
                  {ultimaChiusura ? localDateTime(ultimaChiusura) : "—"}
                </span>
              </span>
            </div>
          </div>
        </section>

        {(status.data?.warnings ?? []).length > 0 && (
          <ul className="notes">
            {status.data?.warnings.map((nota) => (
              <li key={nota}>{nota}</li>
            ))}
          </ul>
        )}

        <div className="round-strip">
          <button
            type="button"
            className="round-strip__arrow"
            onClick={() => scorri(-1)}
            aria-label="giornate precedenti"
          >
            ‹
          </button>
          <div className="round-strip__track" ref={track}>
            {(rounds.data ?? []).map((r) => (
              <button
                key={r.matchday}
                type="button"
                className={`round-chip${r.closed ? " round-chip--closed" : ""}`}
                aria-pressed={r.matchday === selected}
                onClick={() => setSelected(r.matchday)}
                title={r.status ? (ROUND_LABELS[r.status] ?? r.status) : ""}
              >
                <strong>{r.matchday}</strong>
                <span>Giornata</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="round-strip__arrow"
            onClick={() => scorri(1)}
            aria-label="giornate successive"
          >
            ›
          </button>
        </div>

        <div className="filters">
          {MERCATI.map(([chiave, etichetta]) => (
            <button
              key={chiave}
              type="button"
              className="filter"
              aria-pressed={market === chiave}
              onClick={() => setMarket(chiave)}
            >
              {etichetta}
            </button>
          ))}
          <div className="filters__sort">
            <label htmlFor="ordina">Ordina per</label>
            <select
              id="ordina"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortBy)}
            >
              <option value="time">Orario</option>
              <option value="prob">Probabilità</option>
            </select>
          </div>
        </div>

        <div className="dash-layout">
          <div>
            {matches.loading && <Loading what="carico le partite…" />}
            {matches.error && (
              <ErrorState error={matches.error} what="Non riesco a caricare le partite." />
            )}
            {!matches.loading && !matches.error && matches.data === null && selected !== null && (
              <EmptyState>
                Nessuna previsione registrata per la giornata {selected}. Le quote
                di apertura escono il venerdì: fino ad allora non c'è niente da
                prevedere.
              </EmptyState>
            )}
            {matches.data && matches.data.length > 0 && (
              <ErrorBoundary
                label="griglia partite"
                fallback={<p className="state">Griglia non disponibile.</p>}
              >
                <MatchesGrid
                  matches={matches.data}
                  market={market}
                  sort={sort}
                  picked={scelte}
                  soglia={soglia}
                />
              </ErrorBoundary>
            )}

            {/* La linea dichiarata del modello, in riga sotto la giornata: e'
                la principale, e nella colonna stretta accanto a una lettura
                regolabile sembrava la secondaria delle due. */}
            <ErrorBoundary
              label="selezioni del modello"
              fallback={<p className="state">Selezioni non disponibili.</p>}
            >
              <ModelPicks matchday={selected} />
            </ErrorBoundary>
          </div>

          <aside>
            <ErrorBoundary
              label="selezioni"
              fallback={<p className="state">Selezioni non disponibili.</p>}
            >
              <AsideSelections
                matchday={selected}
                soglia={soglia}
                onSoglia={setSoglia}
                banda={banda}
              />
            </ErrorBoundary>
          </aside>
        </div>

        <div className="dash-bottom">
          <div>
            <Eyebrow>classifica</Eyebrow>
            <ErrorBoundary
              label="classifica"
              fallback={<p className="state">Classifica non disponibile.</p>}
            >
              <StandingsPanel />
            </ErrorBoundary>
          </div>

          <div>
            <Eyebrow>track record</Eyebrow>
            {record.loading && <Loading what="carico il track record…" />}
            {record.error && (
              <ErrorState error={record.error} what="Non riesco a caricare il track record." />
            )}
            {record.data && (
              <ErrorBoundary
                label="track record"
                fallback={<p className="state">Track record non disponibile.</p>}
              >
                <TrackRecordPanel record={record.data.record} summary={record.data.summary} />
              </ErrorBoundary>
            )}
          </div>
        </div>
      </main>

      <Colophon
        modelVersion={record.data?.summary.model_version ?? null}
        showApi
      />
    </div>
  );
}
