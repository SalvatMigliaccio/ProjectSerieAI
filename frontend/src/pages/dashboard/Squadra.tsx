/**
 * La mia squadra: la stagione della squadra preferita, partita per partita.
 *
 * LA STESSA GRIGLIA DELLA GIORNATA, non una tabella a parte. Ogni partita con
 * 1X2, Over/Under, Goal/No Goal e risultato, e lo stesso significato dei
 * colori: chi passa dalla giornata alla squadra legge la stessa cosa nello
 * stesso posto. Cambia solo il raggruppamento — per giornata, dalla piu'
 * recente — perche' qui le righe sono una stagione. Niente filtri per
 * mercato: su una squadra sola le colonne si leggono tutte insieme.
 *
 * DUE MODELLI, UNA GRIGLIA. Il pulsante in testata passa dal modello classico
 * (quello del track record) al modello AI, e la griglia cambia solo i numeri:
 * stesse colonne, stessi colori, e una riga sopra che dice quale si sta
 * guardando e da quando M5 esiste.
 *
 * A destra, nella stessa colonna da 310px della giornata, la sintesi: prossima
 * partita, ultimi risultati, quante volte il modello ha indovinato.
 *
 * Senza squadra scelta la schermata chiede di sceglierla dalla testata, qui,
 * senza mandare nell'area account.
 */

import { useState } from "react";

import { api } from "../../api/client";
import type { MLMatch, Match, Selection } from "../../api/types";
import { useFavorite } from "../../auth/Favorite";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { FavoritePanel } from "../../components/FavoritePanel";
import { FavoritePicker } from "../../components/FavoritePicker";
import { MatchesGrid, chiave } from "../../components/MatchesGrid";
import { ScreenHeader } from "../../components/ScreenHeader";
import { EmptyState, ErrorState, Loading } from "../../components/States";
import { useApi } from "../../hooks/useApi";
import { chiavePartita, conModelloAi } from "../../lib/ai";
import { num } from "../../lib/format";

type Modello = "classico" | "ai";

function Partite({ team, modello }: { team: string; modello: Modello }) {
  const partite = useApi(() => api.teamMatches(team), [team]);
  const ai = useApi<MLMatch[]>(
    () => (modello === "ai" ? api.mlTeam(team) : Promise.resolve([])),
    [team, modello],
  );
  // LA SELEZIONE DEL MODELLO, la stessa di "Selezioni del modello" nella
  // giornata: una per partita, soglia dichiarata dal progetto. Senza, la
  // griglia mostrava solo le probabilita' e nessuna cella diceva cosa il
  // modello avesse scelto.
  const picks = useApi(
    () => (modello === "ai" ? api.picksMl() : api.picks()),
    [modello],
  );
  const scelte = new Map<string, Selection>();
  for (const s of picks.data?.with_min_odds ?? []) {
    if (s.home_team === team || s.away_team === team) scelte.set(chiave(s), s);
  }

  if (partite.loading || (modello === "ai" && ai.loading)) {
    return <Loading what="carico le partite…" />;
  }
  if (partite.error) return <ErrorState error={partite.error} what="Partite non disponibili." />;
  if (modello === "ai" && ai.error) {
    return <ErrorState error={ai.error} what="Previsioni del modello AI non disponibili." />;
  }
  if (!partite.data || partite.data.length === 0) {
    return <EmptyState>Nessuna partita con una previsione in questa stagione.</EmptyState>;
  }

  let righe: Match[] = partite.data;
  let nota: string;
  if (modello === "ai") {
    const perPartita = new Map((ai.data ?? []).map((m) => [chiavePartita(m), m]));
    righe = partite.data.map((m) => conModelloAi(m, perPartita.get(chiavePartita(m))));
    const giornate = [...new Set((ai.data ?? []).map((m) => m.matchday))].sort((x, y) => x - y);
    nota = giornate.length
      ? `Modello AI (LightGBM ancorato al mercato): parte dalle quote e le corregge con la forma delle squadre. Ha girato sulle giornate ${giornate.join(", ")}; sulle altre le celle restano vuote. Seconda opinione: sul test set è indistinguibile dal mercato.`
      : "Il modello AI non ha ancora girato su nessuna partita di questa squadra.";
  } else {
    nota = "Modello classico: le quote di apertura senza il margine del book. È quello del track record.";
  }

  return (
    <>
      <p className="squadra-nota">
        <span className={`squadra-nota__tag squadra-nota__tag--${modello}`}>
          {modello === "ai" ? "Modello AI" : "Modello classico"}
        </span>
        {nota}
        {picks.data && (
          <>
            {" "}La cella accesa è la selezione del modello, la più probabile fra quelle che
            pagano almeno {num(picks.data.min_odds, 2)}; se il mercato non ha una colonna,
            compare sotto i nomi delle squadre.
          </>
        )}
      </p>
      <MatchesGrid
        matches={righe}
        market="all"
        sort="time"
        favorite={team}
        picked={scelte}
        perGiornata
      />
    </>
  );
}

export function Squadra() {
  const { team } = useFavorite();
  const [modello, setModello] = useState<Modello>("classico");

  return (
    <>
      <ScreenHeader
        title={team ?? "La mia squadra"}
        subtitle={team ? "Tutte le partite della stagione, con la previsione del modello" : "Nessuna squadra scelta"}
        aside={
          <>
            {team && (
              <div className="filters picks__models squadra-modelli" role="group" aria-label="Modello">
                {(["classico", "ai"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    className="filter"
                    aria-pressed={modello === m}
                    onClick={() => setModello(m)}
                  >
                    {m === "ai" ? "Modello AI" : "Modello classico"}
                  </button>
                ))}
              </div>
            )}
            <FavoritePicker compatto />
          </>
        }
      />

      {team ? (
        <>
          <div className="dash-layout dash-layout--squadra">
            <div>
              <ErrorBoundary label="partite della squadra" fallback={<p className="state">Partite non disponibili.</p>}>
                <Partite team={team} modello={modello} />
              </ErrorBoundary>
            </div>
            <aside>
              <ErrorBoundary label="la tua squadra" fallback={<p className="state">Squadra non disponibile.</p>}>
                <FavoritePanel team={team} />
              </ErrorBoundary>
            </aside>
          </div>
        </>
      ) : (
        <div className="dash-layout">
          <EmptyState>
            Scegli la squadra da seguire dal menu qui sopra: ritrovi tutte le sue partite
            con le previsioni del modello, evidenziate anche nella giornata e in cima ai tuoi
            pronostici.
          </EmptyState>
        </div>
      )}
    </>
  );
}
