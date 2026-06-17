from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from job_agent.collectors.base import Collector
from job_agent.models import JobOffer, Source

log = logging.getLogger(__name__)

_SEARCH = "https://www.infojobs.net/jobsearch/search-results/list.xhtml?keyword={kw}"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_REMOTE_RE = re.compile(r"\b(remoto|teletrabajo|100% remoto|remote|h[ií]brido)\b", re.IGNORECASE)
_MODALITY_RE = re.compile(r"(solo teletrabajo|teletrabajo|h[ií]brido|presencial|remoto)", re.IGNORECASE)

# Se ejecuta dentro de la página: devuelve las ofertas del listado.
# InfoJobs ya no expone JSON-LD; los datos están en el DOM renderizado.
_EXTRACT_JS = r"""() => {
  const cards = document.querySelectorAll('.ij-OfferList-offerCardItem, .sui-AtomCard');
  const seen = new Set();
  const out = [];
  cards.forEach(card => {
    const link = card.querySelector("a.ij-OfferCardContent-description-title-link, a[href*='/of-i']");
    if (!link) return;
    const url = link.href.split('?')[0];
    if (seen.has(url)) return;
    seen.add(url);
    const titleEl = card.querySelector('.ij-OfferCardContent-description-title') || link;
    const compEl = card.querySelector('.ij-OfferCardContent-description-subtitle');
    const items = [...card.querySelectorAll('.ij-OfferCardContent-description-list-item')]
      .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim());
    out.push({
      url,
      title: (titleEl.innerText || '').replace(/\s+/g, ' ').trim(),
      company: compEl ? (compEl.innerText || '').replace(/\s+/g, ' ').trim() : '',
      items,
    });
  });
  return out;
}"""


class InfoJobsCollector(Collector):
    """Scrapea el buscador de InfoJobs con un navegador real (Playwright).

    InfoJobs está protegido por Distil/Imperva, que bloquea ``requests`` y los
    navegadores *headless* (sirve una página captcha en ``/distil/captcha``).
    Solo un navegador *headed* lo sortea; en servidores sin pantalla hay que
    lanzarlo bajo ``xvfb-run`` (ver Dockerfile).
    """

    name = "infojobs"

    def __init__(
        self,
        search_terms: list[str],
        *,
        headless: bool = False,
        scrolls: int = 4,
        nav_timeout_ms: int = 40000,
    ):
        self.search_terms = search_terms
        self.headless = headless
        self.scrolls = scrolls
        self.nav_timeout_ms = nav_timeout_ms

    def collect(self) -> list[JobOffer]:
        # Import diferido: Playwright es pesado y opcional.
        from playwright.sync_api import sync_playwright

        offers: list[JobOffer] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(locale="es-ES", user_agent=_UA)
            page = ctx.new_page()
            try:
                for term in self.search_terms:
                    offers.extend(self._collect_term(page, term))
            except _Blocked:
                log.error(
                    "infojobs blocked by Distil/Imperva captcha — un navegador "
                    "headless no basta; en servidor usa xvfb-run (ver README)."
                )
            finally:
                browser.close()

        seen: dict[str, JobOffer] = {}
        for o in offers:
            seen.setdefault(o.id, o)
        return list(seen.values())

    def _collect_term(self, page, term: str) -> list[JobOffer]:
        url = _SEARCH.format(kw=quote_plus(term))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            page.wait_for_timeout(5000)
        except Exception:
            log.exception("infojobs: error fetching term=%r", term)
            return []

        if "distil/captcha" in page.content():
            raise _Blocked()  # todos los términos comparten la misma defensa

        try:
            page.wait_for_selector(".ij-OfferList-offerCardItem, .sui-AtomCard", timeout=10000)
        except Exception:
            log.warning("infojobs: no cards for term=%r", term)
            return []

        for _ in range(self.scrolls):
            page.mouse.wheel(0, 6000)
            page.wait_for_timeout(500)

        raw = page.evaluate(_EXTRACT_JS)
        parsed = [o for o in (_record_to_offer(r) for r in raw) if o is not None]
        log.info("infojobs term=%r -> %d", term, len(parsed))
        return parsed


class _Blocked(Exception):
    def __init__(self) -> None:
        super().__init__("infojobs blocked by Distil/Imperva captcha")


def _record_to_offer(rec: dict) -> JobOffer | None:
    url = (rec.get("url") or "").strip()
    title = (rec.get("title") or "").strip()
    if not url or not title:
        return None

    items: list[str] = rec.get("items") or []
    location = items[0] if items else None
    modality = ""
    for it in items:
        m = _MODALITY_RE.search(it)
        if m:
            modality = m.group(1)
            break
    is_remote = bool(_REMOTE_RE.search(f"{modality} {title}"))

    # InfoJobs no expone la descripción completa en el listado, pero las viñetas
    # de la tarjeta (ubicación, modalidad, experiencia, salario...) sí dan
    # contexto suficiente para que la IA puntúe. Las unimos como descripción
    # mínima en vez de descartarlas.
    description = "\n".join(it for it in items if it)

    return JobOffer.build(
        title=title,
        company=(rec.get("company") or "").strip(),
        source=Source.INFOJOBS,
        url=url,
        location=location,
        is_remote=is_remote,
        description=description,
        published_at=None,
    )
