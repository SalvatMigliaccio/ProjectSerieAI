/**
 * La stagione: il campionato visto dai numeri — classifica, capocannonieri,
 * assistman e punti attesi.
 *
 * TRE FATTI E UNA PREVISIONE. Classifica, marcatori e assist sono aritmetica
 * su cose gia' successe; i punti attesi sono il modello. Stanno nella stessa
 * schermata perche' rispondono alla stessa domanda — come sta andando il
 * campionato — e la tabella del modello lo dichiara nel titolo, per non
 * confondersi con le altre.
 *
 * Track record e modello AI stavano qui: ora sono in "Modello", che e'
 * un'altra domanda.
 */

import { ErrorBoundary } from "../../components/ErrorBoundary";
import { ExpectedPointsPanel } from "../../components/ExpectedPointsPanel";
import { Eyebrow } from "../../components/Layout";
import { PlayersPanel } from "../../components/PlayersPanel";
import { ScreenHeader } from "../../components/ScreenHeader";
import { StandingsPanel } from "../../components/StandingsPanel";

export function Stagione() {
  return (
    <>
      <ScreenHeader
        title="Stagione"
        subtitle="Classifica, capocannonieri, assistman e i punti che il modello si aspettava da ogni squadra"
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

        <div className="stagione-giocatori">
          <div>
            <Eyebrow>capocannonieri</Eyebrow>
            <ErrorBoundary label="capocannonieri" fallback={<p className="state">Marcatori non disponibili.</p>}>
              <PlayersPanel tipo="gol" />
            </ErrorBoundary>
          </div>
          <div>
            <Eyebrow>assistman</Eyebrow>
            <ErrorBoundary label="assistman" fallback={<p className="state">Assist non disponibili.</p>}>
              <PlayersPanel tipo="assist" />
            </ErrorBoundary>
          </div>
        </div>
      </div>

      <div className="stagione-xpts">
        <Eyebrow>punti attesi dal modello</Eyebrow>
        <ErrorBoundary label="punti attesi" fallback={<p className="state">Punti attesi non disponibili.</p>}>
          <ExpectedPointsPanel />
        </ErrorBoundary>
      </div>
    </>
  );
}
