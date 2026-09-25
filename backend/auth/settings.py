"""
La configurazione dell'autenticazione, letta dall'ambiente.

PERCHE' DALL'AMBIENTE E NON DA UN FILE NEL REPOSITORY. E' la regola di
sicurezza n.1: nessun segreto versionato, nemmeno in un file di esempio. In
sviluppo le variabili arrivano da `.env`, che e' ignorato da git; in
produzione dall'ambiente del servizio, che non passa mai da un file.

PERCHE' UN OGGETTO E NON `os.environ` SPARSO. Una variabile letta in sei punti
e' sei posti da cui dimenticarsi un default, e un default diverso in due punti
non da' errore: da' due comportamenti. Qui si legge una volta, si valida, e
chi la usa riceve un valore gia' controllato.

LA VALIDAZIONE CHE CONTA DAVVERO e' `controlla_produzione()`: dice di no
all'avvio quando la configurazione e' insicura, invece di funzionare e basta.
Una chiave di firma lasciata al valore di esempio non rompe niente — e' questo
il problema.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Il valore che sta in `.env.example`. Se arriva fin qui, nessuno l'ha cambiato.
CHIAVE_DI_ESEMPIO = "generane-una-con-secrets-token-urlsafe-64"


class Impostazioni(BaseSettings):
    """Tutto cio' che l'autenticazione ha bisogno di sapere dall'esterno."""

    model_config = SettingsConfigDict(
        env_prefix="AI_NAPLES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://ainaples:ainaples@127.0.0.1:5432/ainaples"

    # Firma i token di verifica e di reset. Cambiarla li invalida tutti in
    # blocco, ed e' anche il modo di annullarli in fretta se serve.
    secret_key: SecretStr = SecretStr(CHIAVE_DI_ESEMPIO)

    # --- sessioni -----------------------------------------------------------
    # Due scadenze, non una. Quella di inattivita' butta fuori chi lascia il
    # portatile aperto; quella assoluta limita il danno di un cookie rubato,
    # che altrimenti resterebbe valido finche' qualcuno continua a usarlo.
    sessione_inattivita_minuti: int = Field(default=60 * 12, ge=5)
    sessione_durata_massima_ore: int = Field(default=24 * 14, ge=1)
    cookie_nome: str = "ainaples_sessione"
    cookie_secure: bool = False
    cookie_domain: str | None = None

    # --- difese sul login ---------------------------------------------------
    # Il blocco e' sull'ACCOUNT, non sull'IP: un attacco distribuito cambia IP
    # a ogni tentativo e un limite per IP non lo vedrebbe nemmeno.
    tentativi_massimi: int = Field(default=8, ge=3)
    blocco_minuti: int = Field(default=15, ge=1)

    # --- token via mail -----------------------------------------------------
    # Il reset dura poco: e' un'apertura temporanea sull'account, e una mail
    # resta nella casella per sempre.
    verifica_validita_ore: int = Field(default=48, ge=1)
    reset_validita_minuti: int = Field(default=30, ge=5)

    # --- posta --------------------------------------------------------------
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: str = "none"          # none | starttls | ssl
    smtp_from: str = "no-reply@ainaples.local"
    smtp_from_name: str = "AI Naples"

    # Dove vive il frontend: serve a costruire i link dentro le mail.
    public_url: str = "http://127.0.0.1:5173"

    @field_validator("smtp_security")
    @classmethod
    def _sicurezza_nota(cls, v: str) -> str:
        ammessi = {"none", "starttls", "ssl"}
        if v not in ammessi:
            raise ValueError(f"smtp_security deve essere uno di {sorted(ammessi)}, non '{v}'")
        return v

    @property
    def mittente(self) -> str:
        """`Nome <indirizzo>`, come lo vuole l'intestazione From."""
        return f"{self.smtp_from_name} <{self.smtp_from}>" if self.smtp_from_name else self.smtp_from

    def controlla_produzione(self) -> list[str]:
        """
        I problemi che in produzione non sono opinioni.

        Restituisce una lista invece di sollevare: chi chiama decide se
        fermarsi (l'avvio dell'API) o solo avvisare (un comando di sviluppo).
        Restituire l'elenco COMPLETO e non il primo problema e' voluto — chi
        sta configurando un deploy vuole sapere tutto adesso, non scoprirne
        uno per riavvio.
        """
        problemi = []
        if self.secret_key.get_secret_value() == CHIAVE_DI_ESEMPIO:
            problemi.append(
                "AI_NAPLES_SECRET_KEY e' ancora quella di esempio. Generane una: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if len(self.secret_key.get_secret_value()) < 32:
            problemi.append("AI_NAPLES_SECRET_KEY e' piu' corta di 32 caratteri")
        if not self.cookie_secure:
            problemi.append(
                "AI_NAPLES_COOKIE_SECURE=0: il cookie di sessione viaggerebbe "
                "anche su http. In produzione va 1."
            )
        if self.smtp_security == "none" and self.smtp_host not in ("127.0.0.1", "localhost", "mail"):
            problemi.append(
                f"SMTP verso {self.smtp_host} senza cifratura: credenziali e "
                f"link di reset passerebbero in chiaro. Usa starttls o ssl."
            )
        if self.public_url.startswith("http://") and "127.0.0.1" not in self.public_url:
            problemi.append(
                f"AI_NAPLES_PUBLIC_URL e' http: i link di verifica e di reset "
                f"finirebbero in chiaro nelle mail ({self.public_url})"
            )
        return problemi


@lru_cache(maxsize=1)
def impostazioni() -> Impostazioni:
    """Lette una volta sola per processo."""
    return Impostazioni()
