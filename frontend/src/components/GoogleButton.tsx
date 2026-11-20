/**
 * "Continua con Google" (ADR 0008).
 *
 * UN LINK, NON UN PULSANTE CON fetch. Il giro passa da Google e torna come
 * pagina intera, con i cookie gia' impostati dal server: e' una navigazione, e
 * un <a> e' l'elemento che la fa — funziona con il clic centrale, con la
 * tastiera e senza JavaScript che intercetti niente.
 *
 * SI VEDE SOLO SE IL SERVER LO OFFRE. `/api/auth/providers` dice se Google e'
 * configurato: un pulsante che porta a un 404 sarebbe peggio di nessun
 * pulsante. Finche' la risposta non arriva non si disegna niente, invece di un
 * pulsante che poi sparisce.
 *
 * Il logo e' la "G" a quattro colori delle linee guida di Google per il
 * pulsante di accesso: e' il segno che la gente riconosce, e cambiarlo
 * renderebbe meno chiaro a chi si stanno dando le credenziali.
 */

import { useEffect, useState } from "react";

import * as authApi from "../api/auth";

export function useProviders(): authApi.Providers | null {
  const [p, setP] = useState<authApi.Providers | null>(null);
  useEffect(() => {
    let annullato = false;
    void authApi.providers().then((r) => {
      if (!annullato) setP(r);
    });
    return () => {
      annullato = true;
    };
  }, []);
  return p;
}

export function GoogleLogo({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.2-.1-2.3-.4-3.5z" />
    </svg>
  );
}

/**
 * Il pulsante con il divisore "oppure" sotto, da mettere SOPRA il modulo con
 * la password: e' la strada piu' corta, e chi ha gia' una password scorre
 * comunque fino ai suoi campi.
 */
export function GoogleSignIn({ label = "Continua con Google", next }: { label?: string; next?: string }) {
  const p = useProviders();
  if (!p?.google) return null;
  return (
    <>
      <a className="btn btn--ghost btn--block btn--google"
        href={authApi.oauthStartUrl("google", "sign_in", next)}>
        <GoogleLogo />
        {label}
      </a>
      <p className="auth-divider" role="separator">
        <span>oppure con l'email</span>
      </p>
    </>
  );
}
