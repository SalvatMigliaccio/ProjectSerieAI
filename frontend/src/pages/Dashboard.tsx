import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { Match, SeasonSummary, TrackRecord } from "../api/types";
import { Colophon, Eyebrow, Masthead } from "../components/Layout";
import { MatchTable } from "../components/MatchTable";
import { RoundSelector } from "../components/RoundSelector";
import { PicksPanel } from "../components/PicksPanel";
import { SelectionsPanel } from "../components/SelectionsPanel";
import { EmptyState, ErrorState, Loading } from "../components/States";
import { StatusBar } from "../components/StatusBar";
import { TrackRecordPanel } from "../components/TrackRecordPanel";
import { useApi } from "../hooks/useApi";
import { ROUND_LABELS } from "../lib/format";

export function Dashboard() {
  const [selected, setSelected] = useState<number | null>(null);

  const status = useApi(() => api.status(), []);
  const rounds = useApi(() => api.rounds(), []);
  const health = useApi(() => api.health(), []);
  const track = useApi<{ record: TrackRecord; summary: SeasonSummary }>(
    () =>
      Promise.all([api.trackRecord(), api.season()]).then(([record, summary]) => ({
        record,
        summary,
      })),
    [],
  );

  // Preselezione: la giornata corrente secondo l'API, ma solo se ha qualcosa da
  // mostrare. Altrimenti l'ultima con previsioni registrate — a settembre la
  // giornata in corso e' ancora futura, e aprirla vuota non direbbe niente.
  useEffect(() => {
    if (selected !== null || !rounds.data) return;
    const list = rounds.data;
    const current = list.find((round) => round.matchday === status.data?.current_round);
    const withPredictions = list.filter((round) => (round.matches_predicted ?? 0) > 0);
    const initial =
      current && (current.matches_predicted ?? 0) > 0
        ? current
        : (withPredictions.at(-1) ?? current ?? list[0]);
    if (initial) setSelected(initial.matchday);
  }, [rounds.data, status.data, selected]);

  const matches = useApi<Match[] | null>(
    () => (selected === null ? Promise.resolve(null) : api.roundMatches(selected)),
    [selected],
  );

  const round = rounds.data?.find((item) => item.matchday === selected) ?? null;
  const roundLabel = round?.status ? (ROUND_LABELS[round.status] ?? round.status) : "";

  return (
    <>
      <Masthead current="dashboard" />

      <main className="wrap">
        <div style={{ paddingTop: "var(--s5)" }}>
          {status.loading && <Loading what="carico lo stato del sistema…" />}
          {status.error && (
            <ErrorState error={status.error} what="Stato del sistema non disponibile." />
          )}
          {status.data && <StatusBar status={status.data} />}
        </div>

        <Eyebrow>giornata</Eyebrow>
        {rounds.loading && <Loading what="carico le giornate…" />}
        {rounds.error && (
          <ErrorState error={rounds.error} what="Non riesco a caricare le giornate." />
        )}
        {rounds.data && (
          <RoundSelector rounds={rounds.data} selected={selected} onSelect={setSelected} />
        )}

        <Eyebrow>
          {selected === null
            ? "partite"
            : `partite · giornata ${selected}${roundLabel ? ` · ${roundLabel}` : ""}`}
        </Eyebrow>
        {matches.loading && <Loading what="carico le partite…" />}
        {matches.error && (
          <ErrorState error={matches.error} what="Non riesco a caricare le partite." />
        )}
        {!matches.loading && !matches.error && matches.data === null && selected !== null && (
          <EmptyState>
            Nessuna previsione registrata per questa giornata. Le quote di apertura
            escono il venerdì: fino ad allora non c'è niente da prevedere.
          </EmptyState>
        )}
        {matches.data && matches.data.length > 0 && <MatchTable matches={matches.data} />}

        <Eyebrow>selezioni del modello</Eyebrow>
        <PicksPanel matchday={selected} />

        <Eyebrow>esplora per banda di quota</Eyebrow>
        <SelectionsPanel matchday={selected} />

        <Eyebrow>track record</Eyebrow>
        {track.loading && <Loading what="carico il track record…" />}
        {track.error && (
          <ErrorState error={track.error} what="Non riesco a caricare il track record." />
        )}
        {track.data && (
          <TrackRecordPanel record={track.data.record} summary={track.data.summary} />
        )}
      </main>

      <Colophon
        modelVersion={track.data?.summary.model_version ?? null}
        updatedAt={health.data?.updated_at ?? null}
        showApi
      />
    </>
  );
}
