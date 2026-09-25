"""
Le tabelle dell'autenticazione.

DUE SCELTE CHE VALE LA PENA MOTIVARE, perche' sembrano complicazioni e non lo
sono.

1. **I segreti si salvano hashati, mai in chiaro.** Vale per le password, ed
   e' ovvio; vale anche per il token di sessione e per i token di verifica e
   di reset, e questo lo si dimentica. Un dump del database — un backup finito
   nel posto sbagliato, una SQL injection in sola lettura — con i token in
   chiaro permette di impersonare chiunque sia collegato in quel momento, senza
   sapere nessuna password. Con gli hash no. Il token integrale esiste solo nel
   cookie del browser e nel link dentro la mail.

2. **I ruoli sono righe, non un enum.** Un `users.ruolo` con tre valori e'
   piu' semplice finche' i valori restano tre. La fase 4 aggiunge i livelli di
   abbonamento, e con un enum ogni livello nuovo e' una migrazione dello
   schema; con delle righe e' un INSERT. Il costo e' due tabelle di
   associazione.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import uuid7


def adesso() -> dt.datetime:
    """UTC esplicito. Un datetime naive qui e' un bug che si vede fra sei mesi."""
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


# Ogni colonna temporale e' `timestamptz`. Con `timestamp` senza fuso, un
# server che cambia TZ sposta silenziosamente ogni scadenza gia' scritta.
Quando = DateTime(timezone=True)


class Utente(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)

    # CITEXT e non String: il confronto ignora maiuscole e minuscole, quindi
    # l'unicita' vale sull'indirizzo reale. Con String, `Mario@x.it` e
    # `mario@x.it` sono due account e l'utente non riesce piu' a entrare
    # perche' non ricorda quale grafia aveva usato.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # pending -> non ha ancora verificato la mail; active -> puo' entrare;
    # suspended -> bloccato da un amministratore; deleted -> cancellato in modo
    # logico, perche' gli eventi di audit devono continuare a puntare a
    # qualcosa anche dopo.
    stato: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    email_verificata_il: Mapped[dt.datetime | None] = mapped_column(Quando)

    # Predisposizione MFA: la colonna c'e', il flusso non ancora. Aggiungerla
    # dopo significherebbe migrare una tabella con account veri.
    totp_secret: Mapped[str | None] = mapped_column(Text)

    tentativi_falliti: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bloccato_fino: Mapped[dt.datetime | None] = mapped_column(Quando)

    creato_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False, server_default=func.now())
    aggiornato_il: Mapped[dt.datetime] = mapped_column(
        Quando, nullable=False, server_default=func.now(), onupdate=adesso
    )

    ruoli: Mapped[list[Ruolo]] = relationship(
        secondary="user_roles", back_populates="utenti", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "stato IN ('pending', 'active', 'suspended', 'deleted')",
            name="ck_users_stato",
        ),
        # Il pannello di amministrazione cerca per email parziale. Senza questo
        # indice e' una scansione completa su ogni ricerca.
        Index("ix_users_email_trgm", "email", postgresql_using="gin",
              postgresql_ops={"email": "gin_trgm_ops"}),
    )

    def attivo(self) -> bool:
        return self.stato == "active"


class Ruolo(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    nome: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Un ruolo di sistema non si cancella dal pannello: togliere `superadmin`
    # mentre e' l'unico che puo' assegnarlo chiude fuori tutti, e non esiste
    # un modo di rientrare che non sia una query a mano sul database.
    di_sistema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permessi: Mapped[list[Permesso]] = relationship(
        secondary="role_permissions", back_populates="ruoli", lazy="selectin"
    )
    utenti: Mapped[list[Utente]] = relationship(
        secondary="user_roles", back_populates="ruoli"
    )


class Permesso(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Forma `risorsa:azione`, per esempio `picks:read`.
    nome: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    descrizione: Mapped[str] = mapped_column(Text, nullable=False, default="")

    ruoli: Mapped[list[Ruolo]] = relationship(
        secondary="role_permissions", back_populates="permessi"
    )


class RuoloPermesso(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UtenteRuolo(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    assegnato_il: Mapped[dt.datetime] = mapped_column(
        Quando, nullable=False, server_default=func.now())
    # Chi ha concesso il ruolo. `SET NULL` e non `CASCADE`: se l'amministratore
    # viene cancellato, la concessione resta — serve a ricostruire come un
    # utente ha ottenuto un privilegio.
    assegnato_da: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))


class Sessione(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # SHA-256 del token che sta nel cookie. Il token in chiaro non tocca mai il
    # database: chi legge questa tabella non puo' impersonare nessuno.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    creata_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False, server_default=func.now())
    vista_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False, server_default=func.now())
    # Scadenza assoluta, decisa alla creazione: limita la finestra di un cookie
    # rubato, che altrimenti resterebbe valido finche' qualcuno lo usa.
    scade_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False)
    revocata_il: Mapped[dt.datetime | None] = mapped_column(Quando)

    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    utente: Mapped[Utente] = relationship(lazy="joined")

    def valida(self, ora: dt.datetime, inattivita: dt.timedelta) -> bool:
        if self.revocata_il is not None:
            return False
        return ora < self.scade_il and ora - self.vista_il < inattivita


class TokenEmail(Base):
    """Token di verifica dell'indirizzo e di reset della password."""

    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)   # verify | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    creato_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False, server_default=func.now())
    scade_il: Mapped[dt.datetime] = mapped_column(Quando, nullable=False)
    # Riga in tabella e non token firmato autocontenuto: un token firmato non
    # si puo' rendere monouso senza tenere traccia di chi l'ha gia' speso, e
    # un link di reset riutilizzabile e' un secondo modo di entrare che resta
    # nella casella di posta per sempre.
    usato_il: Mapped[dt.datetime | None] = mapped_column(Quando)

    __table_args__ = (
        CheckConstraint("tipo IN ('verify', 'reset')", name="ck_email_tokens_tipo"),
        Index("ix_email_tokens_user_tipo", "user_id", "tipo"),
    )

    def spendibile(self, ora: dt.datetime) -> bool:
        return self.usato_il is None and ora < self.scade_il


class EventoAuth(Base):
    """
    Il registro degli eventi di autenticazione: append-only.

    Stesso principio del registro delle previsioni — si scrive, non si
    riscrive. Un tentativo di accesso fallito che sparisce dal log e' un
    attacco che non si riesce piu' a ricostruire.
    """

    __tablename__ = "auth_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # NULL quando l'evento riguarda un'email che non esiste: va registrato
    # comunque, perche' e' il segnale tipico dell'enumerazione degli account.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True)

    tipo: Mapped[str] = mapped_column(String(32), nullable=False)
    esito: Mapped[str] = mapped_column(String(16), nullable=False)   # ok | ko
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    # Mai credenziali qui dentro: contesto, non segreti.
    dettagli: Mapped[dict | None] = mapped_column(JSONB)
    quando: Mapped[dt.datetime] = mapped_column(Quando, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("esito IN ('ok', 'ko')", name="ck_auth_events_esito"),
        Index("ix_auth_events_tipo_quando", "tipo", "quando"),
    )
