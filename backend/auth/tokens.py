"""
Token di sessione e token via mail: generazione e confronto.

UN SOLO PRINCIPIO, APPLICATO OVUNQUE: **quello che finisce nel database e' un
hash; il valore in chiaro esiste solo nel cookie del browser o nel link dentro
la mail.** Chi legge la tabella non puo' impersonare nessuno.

PERCHE' SHA-256 QUI E argon2 PER LE PASSWORD. Sembra un'incoerenza e non lo e'.
argon2 e' lento di proposito perche' una password ha poca entropia e va difesa
dal brute force. Questi token hanno 256 bit da `secrets`: non c'e' niente da
indovinare, e un hash lento verrebbe eseguito a ogni richiesta autenticata
soltanto per rallentare il sito.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 32 byte = 256 bit. Anche potendo provare mille miliardi di token al secondo,
# lo spazio non si esaurisce prima della fine del sistema solare.
BYTE_DI_ENTROPIA = 32


def genera() -> tuple[str, str]:
    """
    Un token nuovo: `(in_chiaro, hash)`.

    Il chiamante manda `in_chiaro` all'utente e salva `hash`. Non esiste una
    funzione che riporti indietro il valore in chiaro, ed e' voluto: se
    servisse, vorrebbe dire che e' stato conservato da qualche parte.
    """
    chiaro = secrets.token_urlsafe(BYTE_DI_ENTROPIA)
    return chiaro, impronta(chiaro)


def impronta(token: str) -> str:
    """SHA-256 esadecimale, 64 caratteri — quanto la colonna `token_hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def combacia(token: str, hash_salvato: str) -> bool:
    """
    Confronto a tempo costante.

    `==` su stringhe esce al primo byte diverso, quindi il tempo di risposta
    dipende da quanti caratteri iniziali sono corretti. Su una rete rumorosa
    e' difficile da sfruttare, ma il rimedio costa una riga.
    """
    return hmac.compare_digest(impronta(token), hash_salvato)
