/**
 * Informativa privacy e cookie policy (SPEC_PRIVACY_COOKIE R1, R2; ADR 0005 T1, T3).
 *
 * NESSUN FATTO E' SCRITTO QUI. Cookie, periodi di conservazione, trattamenti,
 * destinatari e titolare vengono tutti da `legal/dati.ts`, che
 * `tests/test_privacy.py` confronta con il backend: se cambia un periodo in
 * `settings.py` o compare un cookie nuovo, e' il test a fallire, non questa
 * pagina a diventare falsa in silenzio. Qui c'e' solo il modo di dirlo.
 *
 * LA TABELLA PRIMA DELLA PROSA (R1): chi apre un'informativa cerca "cosa
 * tenete e per quanto", non un preambolo.
 *
 * Non e' consulenza legale (vedi la spec): e' il testo che descrive con
 * esattezza quello che il sistema fa.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Colophon, Masthead } from "../components/Layout";
import {
  AGGIORNATA_IL,
  CONSERVAZIONE_GIORNI,
  COOKIE,
  type Conservazione,
  DESTINATARI,
  ETA_MINIMA,
  GARANTE_URL,
  GOOGLE_PRIVACY_URL,
  RISPOSTA_DIRITTI_GIORNI,
  TITOLARE,
  TRATTAMENTI,
} from "../legal/dati";

function data(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("it-IT", {
    day: "numeric", month: "long", year: "numeric",
  });
}

/** Un periodo detto in mesi o in giorni, oppure la frase stessa quando non e' un periodo. */
function durata(c: Conservazione): string {
  if (c in CONSERVAZIONE_GIORNI) {
    const giorni = CONSERVAZIONE_GIORNI[c as keyof typeof CONSERVAZIONE_GIORNI];
    return giorni % 365 === 0 ? `${(giorni / 365) * 12} mesi` : `${giorni} giorni`;
  }
  return c;
}

function Pagina({ titolo, lead, children }: { titolo: string; lead: ReactNode; children: ReactNode }) {
  return (
    <>
      <Masthead current="hero" />
      <main id="contenuto" className="legale" tabIndex={-1}>
        <article className="wrap legale__testo">
          <p className="legale__eyebrow">Aggiornata il {data(AGGIORNATA_IL)}</p>
          <h1>{titolo}</h1>
          <p className="legale__lead">{lead}</p>
          {children}
        </article>
      </main>
      <Colophon modelVersion={null} />
    </>
  );
}

const MAIL = <a href={`mailto:${TITOLARE.email}`}>{TITOLARE.email}</a>;

export function Privacy() {
  return (
    <Pagina
      titolo="Informativa privacy"
      lead={<>Quali dati personali tratta MatchPoint, perché, per quanto tempo e come
        puoi chiederne conto. In breve: solo quello che serve a farti entrare e a
        proteggere il tuo account.</>}
    >
      <section aria-labelledby="p-titolare">
        <h2 id="p-titolare">Chi tratta i tuoi dati</h2>
        <p>
          Il titolare del trattamento è <strong>{TITOLARE.nominativo}</strong>, persona
          fisica, che gestisce MatchPoint come progetto personale. Per qualsiasi
          domanda o richiesta sui tuoi dati scrivi a {MAIL}.
        </p>
      </section>

      <section aria-labelledby="p-cosa">
        <h2 id="p-cosa">Quali dati, perché, per quanto</h2>
        <div className="legale__tabella">
          <table>
            <thead>
              <tr>
                <th scope="col">Dati</th>
                <th scope="col">Perché</th>
                <th scope="col">Base giuridica</th>
                <th scope="col">Per quanto</th>
              </tr>
            </thead>
            <tbody>
              {TRATTAMENTI.map((t) => (
                <tr key={t.dati}>
                  <td>{t.dati}</td>
                  <td>{t.perche}</td>
                  <td>{t.base}</td>
                  <td>{durata(t.quanto)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          Le password non sono mai conservate leggibili, nemmeno da noi: solo una
          loro impronta cifrata. Lo stesso vale per i collegamenti inviati per email
          e per i codici di verifica. I periodi indicati sono applicati da una
          pulizia automatica, non solo dichiarati.
        </p>
      </section>

      <section aria-labelledby="p-non">
        <h2 id="p-non">Cosa non facciamo</h2>
        <ul>
          <li>Nessuna profilazione e nessun marketing: non ti mandiamo pubblicità e non costruiamo un profilo di te.</li>
          <li>Nessuna decisione automatizzata su di te: le previsioni riguardano le partite, non le persone.</li>
          <li>Nessuna vendita o cessione dei dati a terzi.</li>
          <li>Nessun dato particolare (salute, opinioni, ecc.): non ne chiediamo.</li>
          <li>Nessun cookie di profilazione e nessuno strumento di statistica di terze parti.</li>
        </ul>
      </section>

      <section aria-labelledby="p-google">
        <h2 id="p-google">Se entri con Google</h2>
        <p>
          L'accesso con Google è facoltativo. Se lo scegli, Google ci comunica solo
          tre cose: un identificativo del tuo account Google, il suo indirizzo email
          e se Google lo considera verificato. <strong>Non</strong> riceviamo nome,
          foto, contatti né accesso ad altri servizi Google.
        </p>
        <p>
          Per questo passaggio Google è un titolare autonomo: tratta i dati secondo
          la propria <a href={GOOGLE_PRIVACY_URL} target="_blank" rel="noreferrer noopener">informativa</a>,
          e da quel momento sa che usi MatchPoint. Puoi scollegare Google in qualsiasi
          momento dal tuo <Link to="/account/sicurezza">profilo</Link>.
        </p>
      </section>

      <section aria-labelledby="p-chi">
        <h2 id="p-chi">Chi altro vede i dati</h2>
        <div className="legale__tabella">
          <table>
            <thead>
              <tr>
                <th scope="col">Chi</th>
                <th scope="col">Per cosa</th>
                <th scope="col">Dove</th>
              </tr>
            </thead>
            <tbody>
              {DESTINATARI.map((d) => (
                <tr key={d.chi}>
                  <td>{d.chi}</td>
                  <td>{d.ruolo}</td>
                  <td>{d.dove}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="p-eta">
        <h2 id="p-eta">Età minima</h2>
        <p>
          Per creare un account devi avere almeno <strong>{ETA_MINIMA} anni</strong>: i
          contenuti riguardano le scommesse sportive.
        </p>
      </section>

      <section aria-labelledby="p-diritti">
        <h2 id="p-diritti">I tuoi diritti</h2>
        <p>
          Puoi chiedere di accedere ai tuoi dati, di correggerli, di cancellarli, di
          limitarne il trattamento, di riceverli in un formato leggibile da un
          computer (portabilità) e di opporti al trattamento basato sul legittimo
          interesse.
        </p>
        <ul>
          <li>
            <strong>Cancellazione</strong>: la fai da solo, dal{" "}
            <Link to="/account/sicurezza">profilo</Link>, con «Elimina l'account». Restano
            soltanto i registri degli accessi, senza alcun collegamento a te, fino alla
            loro scadenza ({durata("registroAccessi")}).
          </li>
          <li>
            <strong>Tutto il resto</strong>: scrivi a {MAIL} dall'indirizzo del tuo
            account. Rispondiamo entro {RISPOSTA_DIRITTI_GIORNI} giorni.
          </li>
          <li>
            Se ritieni che il trattamento non rispetti la legge, puoi presentare
            reclamo al <a href={GARANTE_URL} target="_blank" rel="noreferrer noopener">Garante
            per la protezione dei dati personali</a>.
          </li>
        </ul>
      </section>

      <section aria-labelledby="p-cookie">
        <h2 id="p-cookie">Cookie</h2>
        <p>
          Il sito usa solo cookie tecnici, necessari per farti entrare: l'elenco
          completo è nella <Link to="/cookie">cookie policy</Link>.
        </p>
      </section>
    </Pagina>
  );
}

export function CookiePolicy() {
  return (
    <Pagina
      titolo="Cookie policy"
      lead={<>MatchPoint usa <strong>solo cookie tecnici</strong>, necessari a farti
        entrare e a tenere al sicuro l'accesso. Nessun cookie di profilazione, nessun
        cookie di terze parti, nessuno strumento di statistica.</>}
    >
      <section aria-labelledby="c-elenco">
        <h2 id="c-elenco">L'elenco completo</h2>
        <div className="legale__tabella">
          <table>
            <thead>
              <tr>
                <th scope="col">Nome</th>
                <th scope="col">Durata</th>
                <th scope="col">A cosa serve</th>
              </tr>
            </thead>
            <tbody>
              {COOKIE.map((c) => (
                <tr key={c.nome}>
                  <td><code>{c.nome}</code></td>
                  <td>{c.durata}</td>
                  <td>{c.scopo}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          Sono tutti illeggibili dagli script della pagina (HttpOnly) e non vengono
          mandati ad altri siti. Gli ultimi tre esistono solo per pochi minuti, e
          solo se usi l'accesso con Google o la verifica in due passaggi.
        </p>
      </section>

      <section aria-labelledby="c-banner">
        <h2 id="c-banner">Perché non c'è un banner</h2>
        <p>
          Per i cookie tecnici la legge chiede di informarti, non di chiederti il
          consenso. Un banner ti chiederebbe di accettare qualcosa che non ha
          alternative e abitua a cliccare «accetta» senza leggere. Il giorno in cui
          MatchPoint usasse un cookie non tecnico, statistiche comprese, il banner
          arriverebbe, con il rifiuto facile quanto l'accettazione.
        </p>
      </section>

      <section aria-labelledby="c-google">
        <h2 id="c-google">E l'accesso con Google?</h2>
        <p>
          Il pulsante «Continua con Google» è un semplice collegamento: nessuno script
          di Google viene caricato nelle nostre pagine. I cookie di Google li imposta
          Google sul proprio sito, quando ci vai, secondo la sua{" "}
          <a href={GOOGLE_PRIVACY_URL} target="_blank" rel="noreferrer noopener">informativa</a>.
        </p>
      </section>

      <section aria-labelledby="c-cancella">
        <h2 id="c-cancella">Come cancellarli</h2>
        <p>
          Esci dall'account: i cookie di accesso vengono cancellati. Puoi anche
          eliminarli dalle impostazioni del browser (di solito «Privacy» → «Cookie e
          dati dei siti»): non succede niente di male, dovrai solo accedere di nuovo.
        </p>
        <p>
          Per tutto il resto, vedi l'<Link to="/privacy">informativa privacy</Link>.
        </p>
      </section>
    </Pagina>
  );
}
