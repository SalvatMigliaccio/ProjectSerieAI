/**
 * Il sistema chiede meno movimento?
 *
 * Il CSS copre le transizioni e le animazioni (vedi `prefers-reduced-motion`
 * in styles.css), ma non gli scorrimenti avviati dal codice: `scrollIntoView`
 * e `scrollBy` con `behavior: "smooth"` ignorano qualunque foglio di stile.
 * Chi li chiama passa da qui.
 */
export function movimentoRidotto(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export const scorrimento = (): ScrollBehavior => (movimentoRidotto() ? "auto" : "smooth");
