from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from job_agent.models import JobOffer

log = logging.getLogger(__name__)


class Collector(ABC):
    name: str = "base"

    def collect_safe(self) -> list[JobOffer]:
        """Wrap collect() so a failing source never tumbles the run."""
        try:
            offers = self.collect()
            log.info("collector=%s collected=%d", self.name, len(offers))
            return offers
        except Exception:
            log.exception("collector=%s failed; continuing with empty result", self.name)
            return []

    @abstractmethod
    def collect(self) -> list[JobOffer]:
        ...
