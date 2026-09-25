"""
Authentication configuration, read from the environment.

WHY THE ENVIRONMENT AND NOT A FILE IN THE REPOSITORY. Security rule 1: no
secret is ever versioned, not even in an example file. In development the
values come from `.env`, which git ignores; in production from the service
environment, which never touches a file.

WHY AN OBJECT AND NOT `os.environ` SCATTERED AROUND. A variable read in six
places is six places to forget a default, and two different defaults do not
raise — they produce two behaviours. Here it is read once, validated once, and
callers get a value that has already been checked.

THE VALIDATION THAT ACTUALLY MATTERS is `production_problems()`: it refuses to
start on an insecure configuration instead of just working. A signing key left
at its example value breaks nothing — that is precisely the problem.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value shipped in `.env.example`. If it reaches here, nobody changed it.
EXAMPLE_KEY = "generate-one-with-secrets-token-urlsafe-64"


class Settings(BaseSettings):
    """Everything authentication needs to know from outside."""

    model_config = SettingsConfigDict(
        env_prefix="AI_NAPLES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://ainaples:ainaples@127.0.0.1:5432/ainaples"

    # Signs the verification and reset tokens. Rotating it invalidates them all
    # at once, which is also how you revoke them in a hurry.
    secret_key: SecretStr = SecretStr(EXAMPLE_KEY)

    # --- sessions -----------------------------------------------------------
    # Two deadlines, not one. The idle one evicts whoever left the laptop open;
    # the absolute one caps the damage of a stolen cookie, which would
    # otherwise stay valid as long as someone keeps using it.
    session_idle_minutes: int = Field(default=60 * 12, ge=5)
    session_max_hours: int = Field(default=24 * 14, ge=1)
    cookie_name: str = "ainaples_session"
    cookie_secure: bool = False
    cookie_domain: str | None = None

    # --- login defences -----------------------------------------------------
    # The lockout is per ACCOUNT, not per IP: a distributed attack rotates
    # addresses on every attempt and a per-IP limit would never see it.
    max_attempts: int = Field(default=8, ge=3)
    lockout_minutes: int = Field(default=15, ge=1)

    # --- emailed tokens -----------------------------------------------------
    # Reset is short-lived: it is a temporary opening into the account, and an
    # email sits in a mailbox forever.
    verify_valid_hours: int = Field(default=48, ge=1)
    reset_valid_minutes: int = Field(default=30, ge=5)

    # --- mail ---------------------------------------------------------------
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: str = "none"          # none | starttls | ssl
    smtp_from: str = "no-reply@ainaples.local"
    smtp_from_name: str = "AI Naples"

    # Where the frontend lives: used to build the links inside emails.
    public_url: str = "http://127.0.0.1:5173"

    @field_validator("smtp_security")
    @classmethod
    def _known_security(cls, v: str) -> str:
        allowed = {"none", "starttls", "ssl"}
        if v not in allowed:
            raise ValueError(f"smtp_security must be one of {sorted(allowed)}, not '{v}'")
        return v

    @property
    def sender(self) -> str:
        """`Name <address>`, as the From header wants it."""
        return f"{self.smtp_from_name} <{self.smtp_from}>" if self.smtp_from_name else self.smtp_from

    def production_problems(self) -> list[str]:
        """
        The problems that are not a matter of taste in production.

        Returns a list instead of raising: the caller decides whether to stop
        (API startup) or merely warn (a development command). Returning the
        COMPLETE list rather than the first problem is deliberate — whoever is
        configuring a deployment wants all of it now, not one per restart.
        """
        problems = []
        if self.secret_key.get_secret_value() == EXAMPLE_KEY:
            problems.append(
                "AI_NAPLES_SECRET_KEY is still the example value. Generate one: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("AI_NAPLES_SECRET_KEY is shorter than 32 characters")
        if not self.cookie_secure:
            problems.append(
                "AI_NAPLES_COOKIE_SECURE=0: the session cookie would travel "
                "over plain http too. It must be 1 in production."
            )
        if self.smtp_security == "none" and self.smtp_host not in ("127.0.0.1", "localhost", "mail"):
            problems.append(
                f"SMTP to {self.smtp_host} without encryption: credentials and "
                f"reset links would cross the network in the clear. Use starttls or ssl."
            )
        if self.public_url.startswith("http://") and "127.0.0.1" not in self.public_url:
            problems.append(
                f"AI_NAPLES_PUBLIC_URL is http: verification and reset links "
                f"would be emailed in the clear ({self.public_url})"
            )
        return problems


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Read once per process."""
    return Settings()
