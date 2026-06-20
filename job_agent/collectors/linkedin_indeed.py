from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
from jobspy import scrape_jobs

from job_agent.collectors.base import Collector
from job_agent.models import JobOffer, Source

log = logging.getLogger(__name__)


class JobSpyCollector(Collector):
    name = "jobspy"

    def __init__(
        self,
        *,
        sites: list[str],
        search_terms: list[str],
        hours_old: int = 14,
        results_wanted: int = 50,
        country_indeed: str = "spain",
        proxies: list[str] | None = None,
        sleep_between_calls: float = 2.0,
    ):
        self.sites = sites
        self.search_terms = search_terms
        self.hours_old = hours_old
        self.results_wanted = results_wanted
        self.country_indeed = country_indeed
        self.proxies = proxies or None
        self.sleep_between_calls = sleep_between_calls

    def collect(self) -> list[JobOffer]:
        # Two passes per term: nationwide + remote, and Sevilla on-site.
        passes = [
            {"location": "Spain", "is_remote": True},
            {"location": "Sevilla, Spain", "is_remote": False},
        ]

        all_offers: list[JobOffer] = []
        for term in self.search_terms:
            for p in passes:
                offers = self._one_call(term, p["location"], p["is_remote"])
                all_offers.extend(offers)
                time.sleep(self.sleep_between_calls)

        return all_offers

    def _one_call(self, term: str, location: str, is_remote: bool) -> list[JobOffer]:
        try:
            df: pd.DataFrame = scrape_jobs(
                site_name=self.sites,
                search_term=term,
                location=location,
                is_remote=is_remote,
                results_wanted=self.results_wanted,
                hours_old=self.hours_old,
                country_indeed=self.country_indeed,
                proxies=self.proxies,
                description_format="markdown",
                # LinkedIn no incluye la descripción en el listado: jobspy debe
                # visitar cada oferta (1 petición extra por oferta). Sin proxies
                # esto puede provocar rate-limit/bloqueo temporal de la IP.
                linkedin_fetch_description=True,
                verbose=0,
            )
        except Exception:
            log.exception(
                "jobspy call failed term=%r location=%r remote=%s",
                term, location, is_remote,
            )
            return []

        if df is None or df.empty:
            log.info("jobspy term=%r location=%r remote=%s -> 0", term, location, is_remote)
            return []

        offers: list[JobOffer] = []
        for _, row in df.iterrows():
            try:
                offers.append(self._row_to_offer(row))
            except Exception:
                log.exception("jobspy: skip bad row %s", dict(row))
        log.info(
            "jobspy term=%r location=%r remote=%s -> %d",
            term, location, is_remote, len(offers),
        )
        return offers

    @staticmethod
    def _row_to_offer(row) -> JobOffer:
        site = str(row.get("site", "")).lower()
        if site == "linkedin":
            source = Source.LINKEDIN
        elif site == "indeed":
            source = Source.INDEED
        else:
            # Bucket other sites under indeed for storage purposes; keep raw site in title would
            # be noisy. We accept linkedin/indeed only via config so this is unreachable normally.
            source = Source.INDEED

        job_url = row.get("job_url")
        job_url_direct = row.get("job_url_direct")
        if job_url and not pd.isna(job_url):
            url = str(job_url).strip()
        elif job_url_direct and not pd.isna(job_url_direct):
            url = str(job_url_direct).strip()
        else:
            url = ""
        if not url:
            raise ValueError("missing url in jobspy row")

        location = _format_location(row)

        is_remote_val = row.get("is_remote")
        is_remote: bool | None
        if pd.isna(is_remote_val):
            is_remote = None
        else:
            is_remote = bool(is_remote_val)

        published_at = None
        date_posted = row.get("date_posted")
        if date_posted and not pd.isna(date_posted):
            try:
                if isinstance(date_posted, str):
                    published_at = datetime.fromisoformat(date_posted)
                else:
                    published_at = pd.Timestamp(date_posted).to_pydatetime()
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except Exception:
                published_at = None

        return JobOffer.build(
            title=str(row.get("title", "")).strip(),
            company=str(row.get("company", "")).strip(),
            source=source,
            url=url,
            location=location,
            is_remote=is_remote,
            description=str(row.get("description", "") or ""),
            published_at=published_at,
        )


def _format_location(row) -> str | None:
    parts = []
    for key in ("city", "state", "country"):
        v = row.get(key)
        if v and not pd.isna(v):
            parts.append(str(v).strip())
    if parts:
        return ", ".join(parts)
    loc = row.get("location")
    if loc and not pd.isna(loc):
        return str(loc).strip()
    return None
