"""
UUID v7 come chiave primaria, con una via d'uscita per Python < 3.14.

PERCHE' v7 E NON v4. Un v4 e' casuale, quindi ogni inserimento cade in un
punto qualsiasi dell'indice B-tree: le pagine si riempiono a meta' e l'indice
di una tabella che cresce si frammenta. Un v7 ha il tempo nei primi 48 bit,
quindi gli inserimenti sono quasi sempre in coda — lo stesso comportamento di
una chiave seriale, ma senza rivelare quanti utenti ci sono ne' permettere di
indovinare l'id del prossimo.

PERCHE' NON UN INTERO SERIALE. Un id sequenziale esposto in un URL dice al
mondo quanti utenti hai e rende enumerabile ogni risorsa. Su una tabella di
account e' proprio l'informazione da non regalare.

PERCHE' NON `uuidv7()` DI POSTGRES. Arriva con Postgres 18; qui gira la 17.
Generarlo nell'applicazione ha comunque un vantaggio che si tiene anche dopo:
l'id esiste PRIMA dell'INSERT, quindi si puo' scrivere un evento di audit che
lo nomina nella stessa transazione, senza un giro di RETURNING.
"""

from __future__ import annotations

import os
import time
import uuid

_NATIVA = hasattr(uuid, "uuid7")


def uuid7() -> uuid.UUID:
    """Un UUID v7. Usa quella della libreria standard dove c'e' (3.14+)."""
    if _NATIVA:
        return uuid.uuid7()

    # RFC 9562 sezione 5.7: 48 bit di millisecondi Unix, 4 di versione, 12 di
    # casuale, 2 di variante, 62 di casuale. La monotonia dentro lo stesso
    # millisecondo non e' garantita da questa versione di ripiego, e va bene:
    # serve che gli id crescano nel tempo, non che siano un contatore.
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF          # 48 bit
    caso = int.from_bytes(os.urandom(10), "big")            # 80 bit di entropia
    rand_a = (caso >> 62) & 0xFFF                           # 12 bit
    rand_b = caso & ((1 << 62) - 1)                         # 62 bit
    valore = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=valore)
