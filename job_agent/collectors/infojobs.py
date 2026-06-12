from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from job_agent.collectors.base import Collector
from job_agent.models import JobOffer, Source

log = logging.getLogger(__name__)

_BASE = "https://www.infojobs.net/ofertas-trabajo"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_REMOTE_RE = re.compile(r"\b(remoto|teletrabajo|100% remoto|remote)\b", re.IGNORECASE)


class InfoJobsCollector(Collector):
    name = "infojobs"

    def __init__(self, search_slugs: list[str], sleep_between_calls: float = 2.0):
        self.search_slugs = search_slugs
        self.sleep_between_calls = sleep_between_calls
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": _UA,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        })

    def collect(self) -> list[JobOffer]:
        offers: list[JobOffer] = []
        for slug in self.search_slugs:
            url = f"{_BASE}/{slug}"
            try:
                html = self._fetch(url)
            except _Blocked:
                log.error(
                    "infojobs blocked by anti-bot (Cloudflare?) for %s — see README "
                    "for curl_cffi/Playwright fallback",
                    url,
                )
                return offers  # bail out: all slugs share the same defence
            except Exception:
                log.exception("infojobs: error fetching %s", url)
                continue

            parsed = _parse_listings(html)
            log.info("infojobs slug=%s -> %d", slug, len(parsed))
            offers.extend(parsed)
            time.sleep(self.sleep_between_calls)

        seen: dict[str, JobOffer] = {}
        for o in offers:
            seen.setdefault(o.id, o)
        return list(seen.values())

    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=20)
        if resp.status_code in (403, 429, 503):
            raise _Blocked(resp.status_code)
        # Cloudflare interstitial usually returns 200 with a JS challenge page.
        if "Just a moment" in resp.text or "challenge-platform" in resp.text:
            raise _Blocked(resp.status_code)
        resp.raise_for_status()
        return resp.text


class _Blocked(Exception):
    def __init__(self, status: int = 0):
        super().__init__(f"infojobs blocked status={status}")
        self.status = status


def _parse_listings(html: str) -> list[JobOffer]:
    soup = BeautifulSoup(html, "html.parser")
    offers: list[JobOffer] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _iter_job_postings(data):
            try:
                offers.append(_node_to_offer(node))
            except Exception:
                log.exception("infojobs: skip bad JSON-LD node")
    return offers


def _iter_job_postings(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_job_postings(item)
        return
    if not isinstance(data, dict):
        return
    if data.get("@type") == "JobPosting":
        yield data
        return
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_job_postings(item)


def _node_to_offer(node: dict) -> JobOffer:
    title = (node.get("title") or "").strip()
    url = (node.get("url") or "").strip()
    if not url:
        identifier = node.get("identifier") or {}
        if isinstance(identifier, dict):
            url = (identifier.get("value") or "").strip()
    if not url:
        raise ValueError("infojobs: JobPosting node without url")

    org = node.get("hiringOrganization") or {}
    company = (org.get("name") if isinstance(org, dict) else "") or ""

    location_str = _location_from_node(node)
    description = re.sub(r"<[^>]+>", "", node.get("description") or "").strip()
    is_remote = _infer_remote(node, title, description)
    published_at = _parse_date(node.get("datePosted"))

    return JobOffer.build(
        title=title,
        company=company,
        source=Source.INFOJOBS,
        url=url,
        location=location_str,
        is_remote=is_remote,
        description=description,
        published_at=published_at,
    )


def _location_from_node(node: dict) -> str | None:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address") or {}
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
        parts = [p for p in parts if p]
        if parts:
            return ", ".join(str(p) for p in parts)
    return None


def _infer_remote(node: dict, title: str, description: str) -> bool | None:
    if node.get("jobLocationType") == "TELECOMMUTE":
        return True
    if _REMOTE_RE.search(title) or _REMOTE_RE.search(description):
        return True
    return None


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
