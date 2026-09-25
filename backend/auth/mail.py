"""
Sending email, behind a single interface.

WHY AN INTERFACE AND NOT `smtplib` CALLED WHERE NEEDED. Three concrete
reasons, none of them stylistic:

1. **Tests must not open sockets.** With `InMemorySender` a test can ASK what
   was sent and to whom, instead of trusting that sending did not raise.
2. **Changing provider must not touch code.** Mailpit today, SES or a
   corporate relay tomorrow: only `AI_NAPLES_SMTP_*` changes.
3. **An unsent email must not fail a signup.** If the relay is down the
   account was still created; discarding the transaction would leave the user
   with neither an account nor an email.

WHAT IS NOT HERE: retries, queues, per-recipient rate limiting. Those matter
once volume exists; today they would be infrastructure for a problem nobody
has. The seam is `Sender.send`, so adding them later does not touch callers.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote

from .settings import Settings, settings

log = logging.getLogger("auth.mail")


class NotSent(RuntimeError):
    """The message did not leave. The account may well exist anyway."""


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


class Sender(Protocol):
    def send(self, message: Message) -> None: ...


@dataclass
class InMemorySender:
    """
    Collects messages instead of sending them. For tests.

    NOT a duplicate of Mailpit: Mailpit exercises the real SMTP path, this lets
    a unit test read the contents without a container.
    """

    sent: list[Message] = field(default_factory=list)

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def last_to(self, address: str) -> Message | None:
        for m in reversed(self.sent):
            if m.to.lower() == address.lower():
                return m
        return None


@dataclass
class SmtpSender:
    """Real SMTP: Mailpit in development, a provider in production."""

    cfg: Settings

    def send(self, message: Message) -> None:
        msg = EmailMessage()
        msg["From"] = self.cfg.sender
        msg["To"] = message.to
        msg["Subject"] = message.subject
        # `Auto-Submitted` tells auto-responders not to reply. Without it an
        # out-of-office bounces back on every signup.
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")

        context = ssl.create_default_context()
        try:
            if self.cfg.smtp_security == "ssl":
                with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port,
                                      context=context, timeout=15) as s:
                    self._auth_and_send(s, msg)
            else:
                with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=15) as s:
                    if self.cfg.smtp_security == "starttls":
                        s.starttls(context=context)
                    self._auth_and_send(s, msg)
        except (OSError, smtplib.SMTPException) as exc:
            # Deliberately narrow: a TypeError in our own code must not
            # disguise itself as "relay unreachable" (security rule 6).
            raise NotSent(
                f"sending to {message.to} failed via "
                f"{self.cfg.smtp_host}:{self.cfg.smtp_port}: {exc}"
            ) from exc

    def _auth_and_send(self, s: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.cfg.smtp_user:
            s.login(self.cfg.smtp_user, self.cfg.smtp_password.get_secret_value())
        s.send_message(msg)


def sender(cfg: Settings | None = None) -> Sender:
    return SmtpSender(cfg or settings())


# ---------------------------------------------------------------------------
# The messages
# ---------------------------------------------------------------------------
# Plain text plus minimal HTML, no template engine: two emails do not justify a
# dependency, and HTML email cannot use modern CSS anyway.

def _link(cfg: Settings, path: str, token: str) -> str:
    # `quote` on the token: it is base64url and contains nothing that needs
    # encoding, but building a URL by concatenation without escaping is the
    # habit not to form — the day the format changes, nobody revisits this.
    return f"{cfg.public_url.rstrip('/')}{path}?token={quote(token, safe='')}"


def verification_message(cfg: Settings, to: str, token: str) -> Message:
    url = _link(cfg, "/verify-email", token)
    hours = cfg.verify_valid_hours
    text = (
        "Welcome.\n\n"
        "Confirm your address by opening this link:\n"
        f"{url}\n\n"
        f"The link is valid for {hours} hours.\n"
        "If you did not create this account, ignore this message: without the "
        "confirmation the account stays inactive.\n"
    )
    html = (
        "<p>Welcome.</p>"
        f'<p>Confirm your address: <a href="{url}">activate the account</a></p>'
        f"<p>The link is valid for {hours} hours.</p>"
        "<p>If you did not create this account, ignore this message: without "
        "the confirmation the account stays inactive.</p>"
    )
    return Message(to, "Confirm your address", text, html)


def reset_message(cfg: Settings, to: str, token: str) -> Message:
    url = _link(cfg, "/reset-password", token)
    minutes = cfg.reset_valid_minutes
    text = (
        "You asked to reset your password.\n\n"
        f"{url}\n\n"
        f"The link is valid for {minutes} minutes and can be used ONCE.\n"
        "If this was not you, do nothing: your current password stays valid "
        "and nobody has gained access to the account.\n"
    )
    html = (
        "<p>You asked to reset your password.</p>"
        f'<p><a href="{url}">Choose a new password</a></p>'
        f"<p>The link is valid for {minutes} minutes and can be used "
        "<strong>once</strong>.</p>"
        "<p>If this was not you, do nothing: your current password stays valid "
        "and nobody has gained access to the account.</p>"
    )
    return Message(to, "Reset your password", text, html)
