import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { useFavorite } from "../../auth/Favorite";
import type { Fixture, Match, Round, Selection } from "../../api/types";
import { AsideSelections } from "../../components/AsideSelections";
import { AiMatchCards } from "../../components/AiMatchCards";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { FixturesList } from "../../components/FixturesList";
import { Eyebrow } from "../../components/Layout";
import { ScreenHeader } from "../../components/ScreenHeader";
import { MERCATI, MatchesGrid, chiave, type Market, type SortBy } from "../../components/MatchesGrid";
import { ModelPicks } from "../../components/ModelPicks";
import { EmptyState, ErrorState, Loading, SoloAbbonati } from "../../components/States";
import { useApi } from "../../hooks/useApi";
import { ROUND_LABELS, localDateTime } from "../../lib/format";
import { scorrimento } from "../../lib/motion";


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

/**
 * La giornata: griglia delle partite, selezioni e analisi del modello.
 *
 * E' la prima schermata della dashboard (`/dashboard`). Testata, barra
 * laterale e piede li mette `DashboardShell`; classifica e track record stanno
 * in "Stagione", la squadra preferita in "La mia squadra" — qui resta solo
 * l'evidenziazione delle sue partite nella griglia.
 */
export function Giornata() {
  // deep-linking: la giornata scelta sta nell'URL (`#/dashboard?g=5`). Prima
  // viveva solo nello stato del componente: un link mandato a qualcuno apriva
  // sempre la giornata corrente, e tornando indietro dal browser si perdeva.
  // `replace` e non `push`: cambiare giornata non e' una pagina nuova, e
  // riempire la cronologia di dieci passi per tornare alla landing sarebbe
  // peggio del problema.
  const [params, setParams] = useSearchParams();
  const dallUrl = Number(params.get("g")) || null;
  const [selected, setSelectedState] = useState<number | null>(dallUrl);
  const setSelected = (giornata: number) => {
    setSelectedState(giornata);
    setParams({ g: String(giornata) }, { replace: true });
  };
  const [market, setMarket] = useState<Market>("all");
  const { team: preferita } = useFavorite();
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

  // CHI PUO' LEGGERE LO STATO, non chiunque sia entrato. `/api/status` dice
  // quando ha girato lo scheduler e quanto e' vecchio lo snapshot: e'
  // manutenzione, non contenuto, e finiva in pagina come "Nessuna esecuzione
  // registrata di predict_round" — una frase che parla al manutentore e a un
  // cliente dice solo che qualcosa non va.
  const { phase, can } = useAuth();
  const operatore = can("system:manage");
  const status = useApi(
    () => (operatore ? api.status() : Promise.resolve(null)),
    [operatore],
  );
  const rounds = useApi(() => api.rounds(), []);

  useEffect(() => {
    if (selected !== null || !rounds.data) return;
    const list = rounds.data;
    // LA PROSSIMA DA GIOCARE, prevista o no: chiusa la 7, si apre sulla 8 anche
    // prima delle quote, perche' e' li' che si fanno i pronostici. A stagione
    // finita, l'ultima.
    const prossima = list.find((r) => !r.closed && r.status !== "played");
    const iniziale = prossima ?? list.at(-1);
    if (iniziale) setSelected(iniziale.matchday);
  }, [rounds.data, status.data, selected]);

  const matches = useApi<Match[] | null>(
    () => (selected === null ? Promise.resolve(null) : api.roundMatches(selected)),
    [selected],
  );
  const calendario = useApi<Fixture[]>(
    () => (selected === null ? Promise.resolve([]) : api.fixtures(selected)),
    [selected],
  );
  const senzaPrevisione = (calendario.data ?? []).filter((f) => !f.predicted);

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
    <>
        <ScreenHeader
          title={selected === null ? "Giornata" : `Giornata ${selected}`}
          badge={stato.testo ? (
            <span className={`badge${stato.forte ? "" : " badge--quiet"}`}>{stato.testo}</span>
          ) : undefined}
          subtitle={
            <>
              {round?.first_date && round?.last_date
                ? `${giorno(round.first_date)} – ${giorno(round.last_date)}`
                : "Serie A"}
              {round?.status ? ` · ${ROUND_LABELS[round.status] ?? ""}` : ""}
            </>
          }
          aside={
            <>
            <div className="dash-chip">
              <span className="dash-chip__glyph">
                <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
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
                <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
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
            </>
          }
        />

        {operatore && (status.data?.warnings ?? []).length > 0 && (
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
            {/* OGNI GIORNATA SI APRE. Prima solo quelle con una previsione:
                le altre erano un clic che non dava niente. Ora una giornata
                senza previsioni mostra il suo calendario — chi gioca, quando,
                e che i pronostici sono aperti — che e' esattamente cio' che
                serve fra la chiusura della precedente e le quote. */}
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
              calendario.data && calendario.data.length > 0 ? (
                <FixturesList fixtures={calendario.data} favorite={preferita} />
              ) : (
                !calendario.loading && (
                  <EmptyState>Nessuna partita in calendario per la giornata {selected}.</EmptyState>
                )
              )
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
                  favorite={preferita}
                />
              </ErrorBoundary>
            )}

            {matches.data && matches.data.length > 0 && senzaPrevisione.length > 0 && (
              <FixturesList
                fixtures={senzaPrevisione}
                favorite={preferita}
                title="Ancora senza quote"
              />
            )}

            {/* ANALISI DEL MODELLO: le selezioni della giornata, con il
                pulsante per passare dal modello classico (M1, quello del track
                record) al modello AI (M5) con la stessa regola. */}
            <section className="analysis" aria-labelledby="analisi-titolo">
              <header className="analysis__head">
                <Eyebrow>giornata {selected ?? ""}</Eyebrow>
                <h2 id="analisi-titolo">Analisi del modello</h2>
              </header>

              {/* La linea dichiarata del modello: e' la principale, ed e'
                  la prima cosa del blocco. */}
              <ErrorBoundary
                label="selezioni del modello"
                fallback={<p className="state">Selezioni non disponibili.</p>}
              >
                <ModelPicks matchday={selected} />
              </ErrorBoundary>
              <ErrorBoundary
                label="modello AI partita per partita"
                fallback={<p className="state">Modello AI non disponibile.</p>}
              >
                <AiMatchCards matchday={selected} />
              </ErrorBoundary>
            </section>

            {/* Detto una volta sola e nel punto in cui si nota l'assenza. Le
                partite gia' giocate sono tutte qui sopra: quello che manca a
                chi non e' entrato e' la giornata non ancora giocata, ed e'
                l'unica cosa che manca. Dirlo dove il vuoto si vede evita sia
                il banner che accompagna ogni schermata sia il silenzio. */}
            {phase === "anonymous" && <SoloAbbonati />}
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

    </>
  );
}
