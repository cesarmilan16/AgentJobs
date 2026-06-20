from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from job_agent.collectors.base import Collector
from job_agent.models import REMOTE_RE, JobOffer, Source

log = logging.getLogger(__name__)

_BASE = "https://www.tecnoempleo.com/ofertas-trabajo/"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RF_RE = re.compile(r"/rf-[0-9a-f]+")
_RE_RE = re.compile(r"/re-\d+")
# El span de meta es "<prefijo> - DD/MM/YYYY". El prefijo puede ser
# "Madrid (Presencial)", "100% remoto", "Barcelona (Híbrido)", etc.
# (la fecha puede venir pegada al salario, de ahí que no anclemos el final).
_META_RE = re.compile(r"(.+?)\s*-\s*(\d{2}/\d{2}/\d{4})")
_MODALITY_RE = re.compile(r"\(([^)]+)\)")


class TecnoempleoCollector(Collector):
    """Scrapea la página de búsqueda HTML de Tecnoempleo.

    Tecnoempleo retiró su RSS público; este collector consulta el buscador
    (``/ofertas-trabajo/?palabra=...``) y parsea las tarjetas de oferta.
    """

    name = "tecnoempleo"

    def __init__(self, search_terms: list[str], sleep_between_calls: float = 1.5):
        self.search_terms = search_terms
        self.sleep_between_calls = sleep_between_calls
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": _UA,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        })

    def collect(self) -> list[JobOffer]:
        offers: list[JobOffer] = []
        for term in self.search_terms:
            url = f"{_BASE}?te={quote_plus(term)}"
            try:
                resp = self.session.get(url, timeout=20)
                resp.raise_for_status()
            except Exception:
                log.exception("tecnoempleo: error fetching %s", url)
                continue
            parsed = _parse_listings(resp.text)
            log.info("tecnoempleo term=%r -> %d", term, len(parsed))
            offers.extend(parsed)
            time.sleep(self.sleep_between_calls)

        return offers


def _parse_listings(html: str) -> list[JobOffer]:
    soup = BeautifulSoup(html, "html.parser")
    offers: list[JobOffer] = []

    for h3 in soup.find_all("h3"):
        title_a = h3.find("a", href=_RF_RE)
        if not title_a:
            continue
        try:
            offer = _card_to_offer(title_a)
        except Exception:
            log.exception("tecnoempleo: skip bad card")
            continue
        if offer is not None:
            offers.append(offer)
    return offers


def _card_to_offer(title_a) -> JobOffer | None:
    url = title_a.get("href", "").strip()
    title = title_a.get("title") or title_a.get_text()
    title = re.sub(r"\s+", " ", title).strip()
    if not url or not title:
        return None

    # La tarjeta es el ancestro que también contiene el enlace de empresa (/re-).
    card = title_a
    company = ""
    for _ in range(6):
        card = card.parent
        if card is None:
            break
        company_a = card.find("a", href=_RE_RE)
        if company_a:
            company = re.sub(r"\s+", " ", company_a.get_text()).strip()
            break

    location = None
    modality = ""
    published_at = None
    if card is not None:
        meta = card.find("span", class_=re.compile(r"d-block"))
        if meta:
            m = _META_RE.search(re.sub(r"\s+", " ", meta.get_text()))
            if m:
                prefix, date_str = m.group(1).strip(), m.group(2)
                published_at = _parse_date(date_str)
                mod_match = _MODALITY_RE.search(prefix)
                if mod_match:
                    # "Madrid (Presencial)" -> location="Madrid", modality="Presencial"
                    modality = mod_match.group(1).strip()
                    location = prefix[: mod_match.start()].strip() or None
                elif REMOTE_RE.search(prefix):
                    # "100% remoto" -> sin ciudad, solo modalidad.
                    modality = prefix
                else:
                    location = prefix or None

    is_remote = bool(REMOTE_RE.search(f"{modality} {title}"))

    return JobOffer.build(
        title=title,
        company=company or "Tecnoempleo",
        source=Source.TECNOEMPLEO,
        url=url,
        location=location,
        is_remote=is_remote,
        description="",
        published_at=published_at,
    )


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def enrich_descriptions(offers: list[JobOffer], *, sleep_between: float = 0.4) -> list[JobOffer]:
    """Rellena la descripción de las ofertas de Tecnoempleo que llegan vacías.

    El listado no trae descripción; está en la página de detalle (JSON-LD).
    Llamar SOLO sobre ofertas nuevas (tras el dedupe de BD) para no descargar
    cientos de páginas en cada ejecución.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    })

    out: list[JobOffer] = []
    enriched = 0
    for o in offers:
        if o.source is Source.TECNOEMPLEO and not o.description:
            desc = _fetch_description(str(o.url), session)
            if desc:
                o = o.model_copy(update={"description": desc})
                enriched += 1
            time.sleep(sleep_between)
        out.append(o)
    if enriched:
        log.info("tecnoempleo: descripciones enriquecidas=%d", enriched)
    return out


def _fetch_description(url: str, session: requests.Session) -> str:
    try:
        html = session.get(url, timeout=20).text
    except Exception:
        log.warning("tecnoempleo: no se pudo bajar detalle %s", url)
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                raw = item.get("description", "") or ""
                return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
    return ""
