import { useEffect, useState } from "react";

import { api } from "../api/client";
import { useFavorite } from "../auth/Favorite";
import { FormAlert, FormSuccess, serverError } from "./Form";

/**
 * La squadra da seguire: evidenziata nella dashboard e messa in cima ai
 * pronostici. Le squadre vengono dalla classifica della stagione, cioe' dai
 * nomi esatti del track record — gli stessi che il server accetta.
 */
export function FavoritePicker({ compatto = false }: { compatto?: boolean }) {
  const { team, scegli } = useFavorite();
  const [squadre, setSquadre] = useState<string[] | null>(null);
  const [inCorso, setInCorso] = useState(false);
  const [esito, setEsito] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    let annullato = false;
    api
      .standings()
      .then((s) => {
        if (!annullato) setSquadre(s.table.map((r) => r.team).sort((a, b) => a.localeCompare(b, "it")));
      })
      .catch(() => {
        if (!annullato) setSquadre([]);
      });
    return () => {
      annullato = true;
    };
  }, []);

  async function cambia(valore: string) {
    setInCorso(true);
    setEsito(null);
    setErrore(null);
    try {
      await scegli(valore || null);
      setEsito(valore ? `Squadra salvata: ${valore}.` : "Nessuna squadra selezionata.");
    } catch (e) {
      setErrore(serverError(e, "Non riesco a salvare la squadra. Riprova."));
    } finally {
      setInCorso(false);
    }
  }

  // COMPATTO: solo etichetta e menu, per la testata di "La mia squadra". Gli
  // avvisi restano, ma l'errore e' l'unico che serve davvero li': che la
  // scelta sia stata salvata lo dice gia' la pagina, che cambia squadra.
  if (compatto) {
    return (
      <div className="fav-picker">
        <label htmlFor="squadra-testata">Squadra</label>
        <select
          id="squadra-testata"
          value={team ?? ""}
          disabled={inCorso || squadre === null}
          onChange={(e) => void cambia(e.target.value)}
        >
          <option value="">Nessuna</option>
          {(squadre ?? []).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {errore && <p className="field__error" role="alert">{errore}</p>}
      </div>
    );
  }

  return (
    <section className="profilo__blocco" aria-labelledby="squadra-titolo">
      <h2 id="squadra-titolo">La tua squadra</h2>
      <p className="profilo__hint">
        Le sue partite compaiono evidenziate nella dashboard e in cima ai tuoi pronostici.
      </p>
      <div className="field profilo__squadra">
        <label htmlFor="squadra">Squadra preferita</label>
        <select
          id="squadra"
          value={team ?? ""}
          disabled={inCorso || squadre === null}
          onChange={(e) => void cambia(e.target.value)}
        >
          <option value="">Nessuna</option>
          {(squadre ?? []).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>
      <FormAlert>{errore}</FormAlert>
      <FormSuccess>{esito}</FormSuccess>
    </section>
  );
}
