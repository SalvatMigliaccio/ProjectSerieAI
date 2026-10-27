/**
 * Il modello: quanto ha sbagliato, giornata per giornata, e come se la cava il
 * modello AI rispetto al classico.
 *
 * STAVA IN "STAGIONE", accanto alla classifica. Ma la stagione e' il
 * campionato — classifica, marcatori, punti — e il track record e' una domanda
 * sul modello, non sul campionato: chi cercava il capocannoniere si trovava
 * davanti l'RPS cumulativo, e chi cercava l'RPS doveva sapere che stava sotto
 * "Stagione". Una schermata per domanda.
 */

import { api } from "../../api/client";
import type { SeasonSummary, TrackRecord } from "../../api/types";
import { AiExplainer } from "../../components/AiExplainer";
import { AiTrackRecord } from "../../components/AiTrackRecord";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { Eyebrow } from "../../components/Layout";
import { ScreenHeader } from "../../components/ScreenHeader";
import { ErrorState, Loading } from "../../components/States";
import { TrackRecordPanel } from "../../components/TrackRecordPanel";
import { useApi } from "../../hooks/useApi";

export function Modello() {
  const record = useApi<{ record: TrackRecord; summary: SeasonSummary }>(
    () =>
      Promise.all([api.trackRecord(), api.season()]).then(([r, s]) => ({
        record: r,
        summary: s,
      })),
    [],
  );

  return (
    <>
      <ScreenHeader
        title="Modello"
        subtitle="Quanto ha sbagliato il modello giornata per giornata, e come se la cava il modello AI"
      />

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

      {/* LA SECONDA OPINIONE, MISURATA COME LA PRIMA: sotto il track record
          ufficiale e non al suo posto, perche' il modello di produzione resta
          il classico. */}
      <div className="aitr-layout">
        <ErrorBoundary label="modello AI" fallback={<p className="state">Confronto non disponibile.</p>}>
          <AiTrackRecord />
        </ErrorBoundary>
        <AiExplainer />
      </div>
    </>
  );
}
