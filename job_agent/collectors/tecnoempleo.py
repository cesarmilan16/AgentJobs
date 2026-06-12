from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from time import mktime

import feedparser

from job_agent.collectors.base import Collector
from job_agent.models import JobOffer, Source

log = logging.getLogger(__name__)

_REMOTE_RE = re.compile(r"\b(remoto|teletrabajo|100% remoto|remote)\b", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG_RE.sub("", s or "").strip()


class TecnoempleoCollector(Collector):
    name = "tecnoempleo"

    def __init__(self, rss_urls: list[str]):
        self.rss_urls = rss_urls

    def collect(self) -> list[JobOffer]:
        offers: list[JobOffer] = []
        for url in self.rss_urls:
            try:
                feed = feedparser.parse(url)
            except Exception:
                log.exception("tecnoempleo: error parsing feed %s", url)
                continue
            if feed.bozo and not feed.entries:
                log.warning("tecnoempleo: empty/bozo feed %s", url)
                continue
            for entry in feed.entries:
                try:
                    offers.append(self._entry_to_offer(entry))
                except Exception:
                    log.exception("tecnoempleo: skip bad entry from %s", url)
        # Dedupe within this run by id (same offer can appear in several feeds).
        seen: dict[str, JobOffer] = {}
        for o in offers:
            seen.setdefault(o.id, o)
        return list(seen.values())

    @staticmethod
    def _entry_to_offer(entry) -> JobOffer:
        title_raw = entry.get("title", "").strip()
        # Tecnoempleo titles usually look like "Title (Madrid) - Company"
        company = ""
        location = None
        title = title_raw
        m = re.search(r"^(.*?)\s*\(([^)]+)\)\s*-\s*(.*)$", title_raw)
        if m:
            title = m.group(1).strip()
            location = m.group(2).strip()
            company = m.group(3).strip()

        summary = _strip_html(entry.get("summary", ""))
        is_remote = bool(_REMOTE_RE.search(title_raw + " " + summary))

        published_at = None
        if entry.get("published_parsed"):
            published_at = datetime.fromtimestamp(
                mktime(entry["published_parsed"]), tz=timezone.utc
            )

        return JobOffer.build(
            title=title,
            company=company or "Tecnoempleo",
            source=Source.TECNOEMPLEO,
            url=entry.get("link", ""),
            location=location,
            is_remote=is_remote,
            description=summary,
            published_at=published_at,
        )
