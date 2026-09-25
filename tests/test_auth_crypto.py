"""
Password e token: le proprieta' che, se saltano, non danno nessun errore.

Un hash che verifica sempre `True`, un token prevedibile, un confronto che
esce al primo byte diverso: il sistema funziona benissimo in tutti e tre i
casi. E' il motivo per cui servono test espliciti.
"""

from __future__ import annotations

import time

import pytest
from backend.auth import password, tokens

# --- password ---------------------------------------------------------------

def test_cifra_e_verifica() -> None:
    h = password.cifra("una password lunga abbastanza")
    assert password.verifica(h, "una password lunga abbastanza")
    assert not password.verifica(h, "una password lunga abbastanzA")


def test_due_hash_della_stessa_password_sono_diversi() -> None:
    """Il salt e' per hash. Senza, due utenti con la stessa password si vedono."""
    a = password.cifra("la stessa password qui")
    b = password.cifra("la stessa password qui")
    assert a != b
    assert password.verifica(a, "la stessa password qui")
    assert password.verifica(b, "la stessa password qui")


def test_l_hash_non_contiene_la_password() -> None:
    h = password.cifra("segretissima-non-scriverla")
    assert "segretissima" not in h


def test_e_argon2id() -> None:
    """Non bcrypt: bcrypt tronca a 72 byte in silenzio."""
    assert password.cifra("una password lunga abbastanza").startswith("$argon2id$")


def test_le_password_lunghe_non_vengono_troncate() -> None:
    """
    Il difetto specifico di bcrypt, verificato qui perche' non si vede mai.

    Con bcrypt queste due password sono identiche: condividono i primi 72
    byte, e chi conosce la prima entra con la seconda.
    """
    base = "x" * 72
    h = password.cifra(base + "PRIMA")
    assert not password.verifica(h, base + "SECONDA")


def test_password_troppo_corta_rifiutata() -> None:
    with pytest.raises(password.PasswordDebole, match="almeno"):
        password.cifra("corta")


def test_password_assurdamente_lunga_rifiutata() -> None:
    """Senza tetto, una password da megabyte e' un modo di consumare il server."""
    with pytest.raises(password.PasswordDebole, match="superare"):
        password.cifra("x" * (password.LUNGHEZZA_MASSIMA + 1))


def test_hash_corrotto_non_solleva() -> None:
    """Vale come password sbagliata: una riga rovinata non deve far cadere il login."""
    assert not password.verifica("non-e-un-hash", "qualunque cosa")


def test_la_verifica_fittizia_costa_come_quella_vera() -> None:
    """
    Senza, il tempo di risposta rivela quali email sono registrate.

    Il margine e' largo perche' su una macchina condivisa i tempi ballano;
    quello che deve risultare e' lo stesso ordine di grandezza, non l'uguaglianza.
    """
    h = password.cifra("una password lunga abbastanza")

    def quanto(f) -> float:
        inizio = time.perf_counter()
        for _ in range(3):
            f()
        return time.perf_counter() - inizio

    vera = quanto(lambda: password.verifica(h, "sbagliata ma lunga"))
    finta = quanto(password.verifica_fittizia)
    assert 0.25 < finta / vera < 4.0, f"rapporto {finta / vera:.2f}: tempi troppo diversi"


# --- token ------------------------------------------------------------------

def test_i_token_sono_unici() -> None:
    assert len({tokens.genera()[0] for _ in range(2000)}) == 2000


def test_il_token_ha_entropia_sufficiente() -> None:
    chiaro, _ = tokens.genera()
    # base64url: ~4 caratteri ogni 3 byte.
    assert len(chiaro) >= tokens.BYTE_DI_ENTROPIA * 4 // 3


def test_hash_lungo_quanto_la_colonna() -> None:
    """`token_hash` e' String(64). Un hash piu' lungo verrebbe troncato dal db."""
    _, h = tokens.genera()
    assert len(h) == 64


def test_combacia_solo_con_il_token_giusto() -> None:
    chiaro, h = tokens.genera()
    assert tokens.combacia(chiaro, h)
    altro, _ = tokens.genera()
    assert not tokens.combacia(altro, h)


def test_il_chiaro_non_si_ricava_dall_hash() -> None:
    """Banale ma e' l'invariante su cui poggia tutto lo schema."""
    chiaro, h = tokens.genera()
    assert chiaro not in h
