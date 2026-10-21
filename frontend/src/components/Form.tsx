/**
 * I mattoni dei moduli di accesso e dell'area account.
 *
 * PERCHE' UN FILE A PARTE. Accesso, registrazione, reset e cambio password
 * sono lo stesso campo ripetuto: prima ognuno si scriveva etichetta, input e
 * messaggio a mano, e l'errore stava UNO per modulo, in fondo, senza dire a
 * quale campo si riferiva. Qui ogni campo porta il proprio errore sotto di se',
 * legato con `aria-describedby` e segnato con `aria-invalid`: lo legge chi
 * guarda e lo annuncia lo screen reader quando il fuoco arriva sul campo.
 *
 * COSA NON FANNO, DI PROPOSITO. Non impediscono di incollare e non spengono il
 * completamento automatico: i gestori di password sono il modo piu' sicuro di
 * usare una password lunga, e bloccarli spinge verso password corte (WCAG 2.2,
 * autenticazione accessibile).
 */

import { type ReactNode, type Ref, useId, useRef, useState } from "react";

import { ApiError } from "../api/client";

export const MIN_PASSWORD = 12;

// ---------------------------------------------------------------------------
// Validazione: poche regole, scritte per dire cosa fare, non solo cosa manca.
// ---------------------------------------------------------------------------

/**
 * Un controllo di forma, non di esistenza: serve a intercettare la svista
 * ("mario@gmail" senza dominio) prima del giro sul server. Il server valida
 * comunque, ed e' lui l'arbitro.
 */
export function emailError(value: string): string | null {
  const v = value.trim();
  if (!v) return "Inserisci l'indirizzo email.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) {
    return "L'indirizzo non sembra completo: controlla la parte dopo la @ (es. nome@gmail.com).";
  }
  return null;
}

export function newPasswordError(value: string): string | null {
  if (!value) return "Scegli una password.";
  if (value.length < MIN_PASSWORD) {
    return `Servono almeno ${MIN_PASSWORD} caratteri (ne hai ${value.length}). Una frase di più parole va benissimo.`;
  }
  if (new Set(value.toLowerCase()).size < 5) {
    return "Troppi caratteri ripetuti: prova con una frase di più parole.";
  }
  return null;
}

/**
 * Quanto regge una password nuova, in quattro gradini.
 *
 * CONTA LA LUNGHEZZA, NON LE CATEGORIE. E' la stessa regola del server e del
 * suggerimento in pagina: nessun obbligo di maiuscole o simboli, perche'
 * spingono verso «Password1!» e non verso una frase lunga. L'unica eccezione
 * e' la ripetizione: «aaaaaaaaaaaa» ha dodici caratteri e nessuna robustezza,
 * e premiarla insegnerebbe la cosa sbagliata.
 */
export type Robustezza = 0 | 1 | 2 | 3;

export function robustezza(value: string): Robustezza {
  if (value.length < MIN_PASSWORD) return 0;
  if (new Set(value.toLowerCase()).size < 5) return 0;
  if (value.length < 16) return 1;
  if (value.length < 20) return 2;
  return 3;
}

function gradino(value: string): string {
  if (value.length >= MIN_PASSWORD && robustezza(value) === 0) return "troppi caratteri ripetuti";
  const nomi: Record<Robustezza, string> = { 0: "troppo corta", 1: "sufficiente", 2: "buona", 3: "ottima" };
  return nomi[robustezza(value)];
}

export function requiredError(value: string, what: string): string | null {
  return value ? null : `Inserisci ${what}.`;
}

/**
 * Il messaggio da mostrare per un errore del server, in italiano e con il
 * rimedio.
 *
 * L'API risponde in inglese ed e' giusto cosi': e' un contratto, e i suoi
 * messaggi li leggono anche i log e i test. Ma "invalid credentials" in una
 * pagina italiana e' un messaggio a meta': dice che qualcosa non va e non cosa
 * fare. La traduzione sta qui, una volta sola, per stato e per testo noto;
 * quello che non si riconosce passa com'e', che e' meglio di inventare.
 */
export function serverError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message ? error.message : fallback;
  }
  const text = error.message.toLowerCase();
  if (error.status === null || error.status === 0) {
    return "Il server non risponde. Controlla la connessione e riprova tra poco.";
  }
  if (error.status === 429 || text.includes("too many")) {
    return "Troppi tentativi in poco tempo. Aspetta qualche minuto e riprova.";
  }
  if (text.includes("invalid credentials")) {
    return "Email o password non corrette. Controllale e riprova, oppure reimposta la password.";
  }
  if (text.includes("current password")) {
    return "La password attuale non è corretta.";
  }
  if (text.includes("invalid or expired token")) {
    return "Il collegamento non è più valido: è scaduto o è già stato usato. Richiedine uno nuovo.";
  }
  if (text.includes("at least") && text.includes("characters")) {
    return `La password deve avere almeno ${MIN_PASSWORD} caratteri.`;
  }
  if (text.includes("cannot exceed")) {
    return "La password è troppo lunga.";
  }
  if (error.status === 422) {
    return "Alcuni dati non sono validi: controlla i campi e riprova.";
  }
  if (error.status >= 500) {
    return "Qualcosa non ha funzionato dalla nostra parte. Riprova tra poco.";
  }
  return error.message || fallback;
}

// ---------------------------------------------------------------------------
// Campi
// ---------------------------------------------------------------------------

interface FieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  error?: string | null;
  hint?: ReactNode;
  /** L'aiuto e' una conferma ("le due password coincidono"): spunta e verde. */
  hintOk?: boolean;
  autoComplete?: string;
  inputRef?: Ref<HTMLInputElement>;
  autoFocus?: boolean;
}

/** L'id del testo che descrive il campo: prima l'errore, poi l'aiuto. */
function describedBy(id: string, error?: string | null, hint?: ReactNode): string | undefined {
  const ids = [error ? `${id}-errore` : null, hint ? `${id}-aiuto` : null].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
}

function FieldMessages({ id, error, hint, hintOk }: { id: string; error?: string | null; hint?: ReactNode; hintOk?: boolean }) {
  return (
    <>
      {error && (
        <p className="field__error" id={`${id}-errore`}>
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7.5v5.5M12 16.5h.01" strokeLinecap="round" />
          </svg>
          {error}
        </p>
      )}
      {hint && (
        <p className={`field__hint${hintOk ? " field__hint--ok" : ""}`} id={`${id}-aiuto`}>
          {hintOk && (
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
              <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
          {hint}
        </p>
      )}
    </>
  );
}

export function EmailField(props: FieldProps) {
  const { id, label, value, onChange, onBlur, error, hint, autoComplete = "email", inputRef, autoFocus } = props;
  return (
    <div className={`field${error ? " field--invalid" : ""}`}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        ref={inputRef}
        type="email"
        inputMode="email"
        autoComplete={autoComplete}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        required
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
      />
      <FieldMessages id={id} error={error} hint={hint} />
    </div>
  );
}

/**
 * Password con "mostra/nascondi".
 *
 * Il pulsante sta DENTRO il riquadro del campo ma e' un pulsante vero: si
 * raggiunge con il tasto Tab, dice il proprio stato (`aria-pressed`) e il suo
 * nome cambia con l'azione che compie. Vedere quello che si scrive dimezza gli
 * errori di battitura su una password lunga, che e' quella che chiediamo.
 *
 * `counter` mostra la robustezza: solo dove si sceglie una password nuova, non
 * dove la si ripete a memoria. La barra e' decorazione; quello che conta e' il
 * testo accanto, che dice il gradino e i caratteri contro il minimo — il colore
 * da solo non lo legge chi non lo distingue, e nemmeno uno screen reader.
 */
export function PasswordField(props: FieldProps & { counter?: boolean }) {
  const { id, label, value, onChange, onBlur, error, hint, hintOk, autoComplete = "current-password", inputRef, counter, autoFocus } = props;
  const [visible, setVisible] = useState(false);
  const livello = robustezza(value);
  const counterId = `${id}-conteggio`;
  const described = [describedBy(id, error, hint), counter ? counterId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`field${error ? " field--invalid" : ""}`}>
      <label htmlFor={id}>{label}</label>
      <div className="field__password">
        <input
          id={id}
          ref={inputRef}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          required
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          aria-invalid={error ? true : undefined}
          aria-describedby={described}
        />
        <button
          type="button"
          className="field__toggle"
          aria-controls={id}
          aria-pressed={visible}
          aria-label={visible ? "Nascondi la password" : "Mostra la password"}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? (
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
              <path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.4 5.2A10.8 10.8 0 0 1 12 5c5 0 9 4.5 10 7-.4 1-1.3 2.5-2.7 3.9M6.1 6.6C4.2 8 2.8 10 2 12c1 2.5 5 7 10 7 1.7 0 3.3-.5 4.6-1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
              <path d="M2 12c1-2.5 5-7 10-7s9 4.5 10 7c-1 2.5-5 7-10 7S3 14.5 2 12z" strokeLinejoin="round" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          )}
        </button>
      </div>
      {counter && (
        <div className="strength" data-level={value ? livello : undefined}>
          <div className="strength__bar" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <span key={i} className={value && livello > i ? "is-on" : undefined} />
            ))}
          </div>
          <p className="strength__text" id={counterId}>
            {value ? (
              <>
                <strong>{gradino(value)}</strong> · {value.length} caratteri, minimo {MIN_PASSWORD}
              </>
            ) : (
              `Almeno ${MIN_PASSWORD} caratteri`
            )}
          </p>
        </div>
      )}
      <FieldMessages id={id} error={error} hint={hint} hintOk={hintOk} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feedback del modulo
// ---------------------------------------------------------------------------

/** Errore del server, annunciato. Sta sopra il pulsante: e' li' che si guarda. */
export function FormAlert({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="form-alert" role="alert">
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3l9.5 16.5h-19L12 3z" strokeLinejoin="round" />
        <path d="M12 10v4M12 17h.01" strokeLinecap="round" />
      </svg>
      <div>{children}</div>
    </div>
  );
}

/** Conferma di un'azione riuscita. `status` e non `alert`: non interrompe. */
export function FormSuccess({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="form-success" role="status">
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
        <circle cx="12" cy="12" r="9" />
        <path d="M8 12.5l2.7 2.7L16 9.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div>{children}</div>
    </div>
  );
}

/**
 * Il pulsante principale. Durante l'invio resta largo uguale e mostra una
 * rotella con il testo dell'azione in corso: un pulsante che cambia
 * larghezza sposta tutto quello che c'e' sotto.
 */
export function SubmitButton({
  busy,
  children,
  busyText,
  variant = "primary",
}: {
  busy: boolean;
  children: ReactNode;
  busyText: string;
  variant?: "primary" | "ghost";
}) {
  return (
    <button
      className={`btn btn--block${variant === "ghost" ? " btn--ghost" : ""}`}
      type="submit"
      disabled={busy}
      aria-busy={busy || undefined}
    >
      {busy && <span className="spinner" aria-hidden="true" />}
      {busy ? busyText : children}
    </button>
  );
}

/** Un id stabile per i campi di un componente usato piu' volte nella pagina. */
export function useFieldId(name: string): string {
  return `${name}-${useId().replace(/:/g, "")}`;
}

/**
 * Quando mostrare gli errori dei campi, e dove portare il fuoco.
 *
 * NON MENTRE SI SCRIVE: un "indirizzo non valido" alla terza lettera e' un
 * rimprovero per qualcosa che non e' ancora finito. L'errore compare quando si
 * esce dal campo (se si e' scritto qualcosa: uscire da un campo vuoto con Tab
 * non e' un errore) oppure al tentativo di invio. Al tentativo di invio il
 * fuoco va sul primo campo sbagliato, cosi' chi usa la tastiera o uno screen
 * reader arriva dritto a cosa correggere.
 */
export function useValidation<K extends string>(
  errors: Record<K, string | null>,
  values: Record<K, string>,
) {
  const [touched, setTouched] = useState<Partial<Record<K, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const refs = useRef<Partial<Record<K, HTMLInputElement | null>>>({});

  return {
    error: (k: K) => (submitted || touched[k] ? errors[k] : null),
    blur: (k: K) => () => {
      if (values[k]) setTouched((t) => ({ ...t, [k]: true }));
    },
    ref: (k: K) => (el: HTMLInputElement | null) => {
      refs.current[k] = el;
    },
    /** true se si puo' inviare; altrimenti mostra gli errori e sposta il fuoco. */
    check: (): boolean => {
      setSubmitted(true);
      const first = (Object.keys(errors) as K[]).find((k) => errors[k]);
      if (first) {
        refs.current[first]?.focus();
        return false;
      }
      return true;
    },
    reset: () => {
      setSubmitted(false);
      setTouched({});
    },
  };
}
