"""
Hashing delle password con argon2id.

PERCHE' argon2id E NON bcrypt. bcrypt tronca in silenzio a 72 byte: due
password lunghe che condividono i primi 72 caratteri sono la stessa password,
e non lo dice nessuno. argon2id inoltre e' *memory-hard* — un attaccante con
GPU o ASIC non guadagna gli ordini di grandezza che guadagna su bcrypt, perche'
il collo di bottiglia e' la RAM per tentativo, non i cicli.

PERCHE' NON SHA-256 CON SALT. Un hash generico e' veloce di proposito, ed e'
esattamente la proprieta' sbagliata qui: serve lentezza calibrata.

IL RE-HASH NON E' UN DETTAGLIO. I parametri si alzano col tempo. Senza
`va_riaggiornata`, chi si e' registrato due anni fa resta sui parametri di
allora per sempre, e l'aggiornamento protegge solo gli iscritti nuovi.
"""

from __future__ import annotations

import contextlib
import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

log = logging.getLogger("auth.password")

# OWASP Password Storage Cheat Sheet, profilo argon2id: 19 MiB, 2 iterazioni,
# parallelismo 1. Costa ~50-100 ms per verifica su hardware comune: abbastanza
# da rendere il brute force costoso, abbastanza poco da non diventare un modo
# di mettere in ginocchio il server mandando richieste di login.
_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# Lunghezza minima e non un regolamento su maiuscole e cifre: le regole di
# composizione spingono verso "Password1!" e riducono l'entropia reale invece
# di aumentarla. NIST SP 800-63B le sconsiglia esplicitamente.
LUNGHEZZA_MINIMA = 12

# argon2 non tronca, ma senza un tetto una password da 10 MB diventa un modo
# di consumare CPU e RAM del server con una sola richiesta.
LUNGHEZZA_MASSIMA = 1024

# Hash di una password che non usera' mai nessuno. Serve a `verifica_fittizia`.
_FITTIZIO = _HASHER.hash("password inesistente per il confronto a vuoto")


class PasswordDebole(ValueError):
    """La password non rispetta i requisiti minimi."""


def controlla_robustezza(password: str) -> None:
    """Solleva `PasswordDebole` con un messaggio che dice cosa fare."""
    if len(password) < LUNGHEZZA_MINIMA:
        raise PasswordDebole(
            f"la password deve essere lunga almeno {LUNGHEZZA_MINIMA} caratteri"
        )
    if len(password) > LUNGHEZZA_MASSIMA:
        raise PasswordDebole(
            f"la password non puo' superare {LUNGHEZZA_MASSIMA} caratteri"
        )


def cifra(password: str) -> str:
    controlla_robustezza(password)
    return _HASHER.hash(password)


def verifica(hash_salvato: str, password: str) -> bool:
    """
    `True` se la password corrisponde. Non solleva sulle password sbagliate.

    Un hash corrotto o in un formato ignoto vale come password sbagliata, ma
    viene registrato: e' un difetto dei dati, non un tentativo fallito, e
    confondere i due casi nasconde una corruzione del database.
    """
    try:
        return _HASHER.verify(hash_salvato, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        log.warning("hash di password illeggibile: trattato come non valido")
        return False


def verifica_fittizia() -> None:
    """
    Brucia lo stesso tempo di una verifica vera, su un hash finto.

    PERCHE' SERVE. Se il login risponde subito quando l'email non esiste e
    dopo 80 ms quando esiste, il tempo di risposta dice a un estraneo quali
    indirizzi sono registrati. E' enumerazione degli account, e non serve
    nessun errore esplicito per ottenerla: basta un cronometro.
    """
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _HASHER.verify(_FITTIZIO, "qualunque cosa")


def va_riaggiornata(hash_salvato: str) -> bool:
    """`True` se l'hash usa parametri superati e va rifatto al prossimo login."""
    try:
        return _HASHER.check_needs_rehash(hash_salvato)
    except InvalidHashError:
        return True
