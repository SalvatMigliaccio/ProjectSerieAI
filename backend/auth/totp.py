"""
The authenticator-app second factor: TOTP, RFC 6238 (ADR 0008).

Why it exists next to the emailed code: the email code shares a mailbox with
the password reset, so whoever holds the mailbox passes both steps. The app
code lives on the phone, which the mailbox does not reach.

FOUR DECISIONS, each closing a specific hole:

1. **The secret is encrypted at rest** (AES-GCM, key `mfa_key`). It has to be
   reversible — verifying a code needs it — so hashing is not an option; and
   in the clear, a database dump would let anyone generate valid codes.
2. **A code is accepted once.** `users.totp_last_step` remembers the 30-second
   step of the last accepted code; that step and earlier ones are refused. A
   code is otherwise valid for its window plus one step either side (clock
   drift), about a minute and a half for whoever read it over a shoulder.
3. **Recovery codes, no email fallback.** Ten one-time codes, shown once.
   Falling back to the emailed code would reopen the hole this closes.
4. **Setting it up needs the password** (or a fresh Google confirmation for an
   account without one). Otherwise a stolen session could install its own app
   and lock the owner out of their account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import time
import uuid

import pyotp
import segno
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import MfaRecoveryCode, User, now
from .settings import Settings

STEP_SECONDS = 30
DRIFT_STEPS = 1
FORMAT = "v1"
# Recovery codes: Crockford-style alphabet without 0/O, 1/I/L, so a code read
# off paper is not mistyped. 10 characters of 31 symbols: ~49 bits.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
RECOVERY_LENGTH = 10


def _clock() -> float:
    """The time TOTP is computed at. A function so tests can move it 30 seconds on."""
    return time.time()


class SecretUnreadable(Exception):
    """The stored secret cannot be decrypted: the key changed or the row is corrupt."""


def _key(cfg: Settings, purpose: bytes) -> bytes:
    """`mfa_key` if set, else derived from `secret_key` (see Settings.mfa_key)."""
    base = cfg.mfa_key.get_secret_value() or cfg.secret_key.get_secret_value()
    return hmac.new(base.encode(), purpose, hashlib.sha256).digest()


# --- the secret -----------------------------------------------------------------

def new_secret() -> str:
    """160 random bits in base32, the size RFC 4226 recommends."""
    return pyotp.random_base32(length=32)


def encrypt(secret: str, cfg: Settings) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_key(cfg, b"ainaples/totp-secret/v1")).encrypt(nonce, secret.encode(), None)
    return f"{FORMAT}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"


def decrypt(stored: str, cfg: Settings) -> str:
    try:
        version, payload = stored.split(":", 1)
        if version != FORMAT:
            raise SecretUnreadable(f"unknown format {version}")
        raw = base64.urlsafe_b64decode(payload)
        return AESGCM(_key(cfg, b"ainaples/totp-secret/v1")).decrypt(raw[:12], raw[12:], None).decode()
    except (ValueError, InvalidTag) as exc:
        raise SecretUnreadable("TOTP secret cannot be decrypted: was AI_NAPLES_MFA_KEY changed?") from exc


def provisioning_uri(secret: str, email: str, cfg: Settings) -> str:
    """The `otpauth://` link the QR code carries."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=cfg.totp_issuer)


def qr_data_uri(uri: str) -> str:
    """
    The QR code as an SVG data URI, for an `<img>`.

    Generated here and not by a frontend library: one dependency fewer in the
    browser, and the production CSP already allows `img-src data:`. It is
    rendered server-side from the URI only, so it contains nothing the JSON
    next to it does not.
    """
    out = io.BytesIO()
    segno.make(uri, error="m").save(out, kind="svg", scale=5, border=2,
                                    dark="#0b1220", light="#ffffff")
    return "data:image/svg+xml;base64," + base64.b64encode(out.getvalue()).decode()


def readable(secret: str) -> str:
    """`ABCD EFGH ...`: for typing by hand when the camera does not cooperate."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# --- checking a code -------------------------------------------------------------

def matching_step(secret: str, code: str, at: float | None = None) -> int | None:
    """The time step the code belongs to, within the drift window, or None."""
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    at = _clock() if at is None else at
    current = int(at // STEP_SECONDS)
    found = None
    # Every step is compared, not just until the first match: constant work
    # whatever the answer.
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            found = step
    return found


def check(user: User, code: str, cfg: Settings, *, secret: str | None = None,
          at: float | None = None) -> bool:
    """
    True and the step recorded if the code is right and not already used.

    `secret` overrides the stored one: during setup the account is not on
    TOTP yet, and the code is checked against the secret being enrolled.
    """
    if secret is None:
        if not user.totp_secret:
            return False
        secret = decrypt(user.totp_secret, cfg)
    step = matching_step(secret, code, at)
    if step is None:
        return False
    if user.totp_last_step is not None and step <= user.totp_last_step:
        return False      # replay: this code, or an older one, was already accepted
    user.totp_last_step = step
    return True


# --- recovery codes ---------------------------------------------------------------

def normalise_recovery(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def looks_like_recovery(code: str) -> bool:
    return len(normalise_recovery(code)) == RECOVERY_LENGTH


def _recovery_hash(cfg: Settings, user_id: uuid.UUID, code: str) -> str:
    return hmac.new(_key(cfg, b"ainaples/recovery-code/v1"),
                    f"{user_id}:{normalise_recovery(code)}".encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(db: Session, user: User, cfg: Settings) -> list[str]:
    """Replace the user's recovery codes. Returns them in the clear, ONCE."""
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = ["".join(secrets.choice(_ALPHABET) for _ in range(RECOVERY_LENGTH))
             for _ in range(cfg.recovery_codes_count)]
    db.add_all(MfaRecoveryCode(user_id=user.id, code_hash=_recovery_hash(cfg, user.id, c))
               for c in codes)
    db.flush()
    return [f"{c[:5]}-{c[5:]}" for c in codes]


def use_recovery_code(db: Session, user: User, code: str, cfg: Settings) -> bool:
    row = db.scalar(select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == user.id,
        MfaRecoveryCode.code_hash == _recovery_hash(cfg, user.id, code),
        MfaRecoveryCode.used_at.is_(None)))
    if row is None:
        return False
    row.used_at = now()
    return True


def recovery_codes_left(db: Session, user: User) -> int:
    return len(db.scalars(select(MfaRecoveryCode.id).where(
        MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.used_at.is_(None))).all())


def clear(db: Session, user: User) -> None:
    """Everything TOTP leaves on the account: secret, replay guard, recovery codes."""
    user.totp_secret = None
    user.totp_last_step = None
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
