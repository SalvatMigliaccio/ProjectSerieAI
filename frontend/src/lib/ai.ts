/**
 * Il modello AI (M5) nella stessa forma delle partite del modello classico.
 *
 * La griglia delle partite sa leggere un `Match`: per mostrarle quello che ha
 * detto M5 si sostituiscono le probabilita' con quelle di M5, lasciando tutto
 * il resto (squadre, orario, risultato, quote del book) com'e'. Cosi' le due
 * letture stanno nella stessa griglia, colonna per colonna, e cambia solo il
 * numero. Una partita su cui M5 non ha girato resta con le probabilita' vuote
 * invece di ereditare quelle del classico: un trattino, non un numero
 * dell'altro modello con l'etichetta sbagliata.
 */

import type { MLMatch, Match, Outcome } from "../api/types";

export const chiavePartita = (m: { home_team: string; away_team: string }) =>
  `${m.home_team}|${m.away_team}`;

export function conModelloAi(match: Match, ml: MLMatch | undefined): Match {
  const p = (nome: string) => ml?.markets.find((m) => m.market === nome)?.p_ml ?? null;
  const pH = p("1");
  const pD = p("X");
  const pA = p("2");
  let previsto: Outcome | null = null;
  if (pH !== null && pD !== null && pA !== null) {
    previsto = pH >= pD && pH >= pA ? "H" : pD >= pA ? "D" : "A";
  }
  return {
    ...match,
    p_home: pH,
    p_draw: pD,
    p_away: pA,
    p_over25: p("over 2.5"),
    p_btts: p("gol-gol"),
    lambda_home: ml?.lambda_home_ml ?? null,
    lambda_away: ml?.lambda_away_ml ?? null,
    predicted_outcome: previsto,
    correct:
      previsto !== null && match.actual_outcome !== null ? previsto === match.actual_outcome : null,
    // L'RPS archiviato e' del modello classico: per M5 non c'e', e non si
    // mostra quello dell'altro.
    rps: null,
  };
}
