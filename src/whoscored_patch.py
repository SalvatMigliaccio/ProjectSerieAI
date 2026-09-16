"""
Rende utilizzabile lo scraper WhoScored di soccerdata da un IP non inglese.

IL PROBLEMA
WhoScored serve le pagine nella lingua dedotta dalla GEOLOCALIZZAZIONE di chi
chiama. Da un IP italiano la regione si chiama "Italia" e il link al calendario
"Calendario". Lo scraper di soccerdata 1.9.1 cerca invece testi inglesi in due
punti, e fallisce con errori che non dicono la verita':

    KeyError: "None of [Index(['ITA-Serie A'])] are in the [index]"
        -> sembra una lega non supportata. La lega c'e', si chiama "Italia".
           Si risolve in config.WHOSCORED_LEAGUE_OVERRIDE.

    IndexError: list index out of range   (whoscored.py:290)
        -> il link "Fixtures" non esiste: sulla pagina italiana e' "Calendario".
           Si risolve qui.

Tutto il resto dello scraper usa id e classi CSS (`missing-players`, `pn`,
`reason`, `confirmed`), che non sono tradotti: il problema e' piccolo e
circoscritto, non strutturale.

PERCHE' UNA PATCH E NON UNA COPIA DEL METODO
Ricopiare `read_season_stages` significherebbe portarsi dentro cinquanta righe
di logica altrui, che a ogni aggiornamento di soccerdata divergono in silenzio.
Qui si intercetta il SOLO letterale che da' problemi, e solo quando la ricerca
inglese non trova niente. Se soccerdata cambia quel letterale la patch smette
semplicemente di agganciare, l'errore originale ricompare, ed e' visibile.

PERCHE' L'HREF E' PIU' ROBUSTO DEL TESTO
Il ripiego non cerca "Calendario", che sarebbe la stessa fragilita' in un'altra
lingua: cerca gli href che contengono `/Fixtures`. Gli URL di WhoScored restano
in inglese in ogni localizzazione — e' solo il testo visibile a cambiare.

Uso: `applica()` una volta, prima di costruire `sd.WhoScored`.
"""

from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("whoscored_patch")

# Il letterale esatto che soccerdata 1.9.1 cerca, e il ripiego indipendente
# dalla lingua. Se la versione di soccerdata cambia questa stringa, la patch
# non aggancia piu' nulla e l'errore originale torna a vedersi: e' voluto.
XPATH_ATTESO = "//a[text()='Fixtures']/@href"

# `translate` fa da minuscolatore: XPath 1.0 non ha `lower-case()` e distingue
# le maiuscole. Sulla pagina italiana l'href e' `/fixtures/italia-serie-a-...`,
# quindi cercare `/Fixtures` non trova niente — ed e' un fallimento muto, che
# assomiglia in tutto a "la patch non funziona".
XPATH_RIPIEGO = (
    "//a[contains(translate(@href,'FIXTURES','fixtures'),'/fixtures')]/@href"
)

# Testi del bottone di consenso ai cookie, per le lingue che WhoScored serve
# ai Big 5. Il primo e' quello che soccerdata prova da solo.
CONSENSO = ["AGREE", "ACCETTO", "ACCETTA", "ACEPTAR", "ZUSTIMMEN", "ACCEPTER"]

_applicata = False


def applica() -> None:
    """Installa le due patch. Idempotente: rilanciarla non raddoppia niente."""
    global _applicata
    if _applicata:
        return
    _patch_xpath()
    _patch_banner()
    _applicata = True


class _AlberoTradotto:
    """
    L'albero HTML di lxml con una sola query in piu'.

    Delega tutto all'originale tranne `xpath`, e anche li' interviene solo
    quando la ricerca inglese non trova niente. Serve un proxy perche'
    `lxml.etree._Element` e' un tipo C immutabile: non si possono sostituire i
    suoi metodi, e va intercettato prima, quando l'albero viene costruito.
    """

    def __init__(self, albero) -> None:
        self._albero = albero

    def __getattr__(self, nome):
        return getattr(self._albero, nome)

    def xpath(self, path, **kwargs):
        risultato = self._albero.xpath(path, **kwargs)
        if not risultato and path == XPATH_ATTESO:
            risultato = self._albero.xpath(XPATH_RIPIEGO, **kwargs)
            if risultato:
                log.info("link 'Fixtures' assente (pagina non in inglese): "
                         "trovato per href")
        return risultato


def _patch_xpath() -> None:
    """Il link al calendario, cercato per href invece che per testo."""
    import types

    import soccerdata.whoscored as ws

    parse_originale = ws.html.parse

    def parse(*args, **kwargs):
        return _AlberoTradotto(parse_originale(*args, **kwargs))

    # Si sostituisce il riferimento `html` nel namespace di soccerdata, non il
    # modulo lxml: cosi' la patch vale solo per questo scraper e non tocca
    # nient'altro nel processo. `fromstring` resta l'originale, perche' e'
    # usato solo per la diagnostica dei blocchi IP.
    ws.html = types.SimpleNamespace(
        parse=parse, fromstring=ws.html.fromstring,
    )


def _patch_banner() -> None:
    """
    Il bottone di consenso ai cookie, in tutte le lingue che ci servono.

    L'originale cerca solo 'AGREE' e, se non lo trova, solleva
    `ElementClickInterceptedException` — che poi viene interpretata a monte
    come "pagina bloccata", mandando a cercare un blocco IP che non c'e'.
    """
    import time

    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By
    import soccerdata.whoscored as ws

    def _handle_banner(self) -> None:
        time.sleep(2)
        for testo in CONSENSO:
            try:
                self._driver.find_element(
                    By.XPATH, f"//button[./span[text()='{testo}']]"
                ).click()
                time.sleep(2)
                return
            except NoSuchElementException:
                continue
        # Nessun bottone trovato: puo' voler dire che il banner non c'era.
        # Non si solleva niente — l'originale sollevava, e trasformava
        # l'assenza del banner in un finto blocco.
        log.info("nessun banner di consenso trovato: proseguo")

    ws.WhoScored._handle_banner = _handle_banner
