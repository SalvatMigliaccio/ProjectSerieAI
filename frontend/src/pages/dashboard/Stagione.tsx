/**
 * La stagione: classifica e track record, in una schermata loro.
 *
 * Stavano in fondo alla giornata, sotto griglia e analisi: chi li cercava
 * doveva scorrere oltre tutto il resto, e chi guardava la giornata se li
 * trovava sotto senza averli chiesti. La classifica e' aritmetica sui
 * risultati, il track record e' quanto ha sbagliato il modello: vicini perche'
 * non vanno confusi, separati dalla giornata perche' sono un'altra domanda.
 */

import { api } from "../../api/client";
import type { SeasonSummary, TrackRecord } from "../../api/types";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { Eyebrow } from "../../components/Layout";
import { ScreenHeader } from "../../components/ScreenHeader";
import { StandingsPanel } from "../../components/StandingsPanel";
import { ErrorState, Loading } from "../../components/States";
import { TrackRecordPanel } from "../../components/TrackRecordPanel";
import { useApi } from "../../hooks/useApi";

export function Stagione() {
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
        title="Stagione"
        subtitle="La classifica, e quanto ha sbagliato il modello giornata per giornata"
      />

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
    </>
  );
}
