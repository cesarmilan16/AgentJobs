from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from job_agent.models import JobOffer


@dataclass(frozen=True)
class HardFilterConfig:
    exclude_title_keywords: list[str]
    exclude_text_keywords: list[str]
    exclude_stack_in_title: list[str]
    exclude_locations_non_spain: list[str]

    @classmethod
    def from_dict(cls, raw: dict) -> "HardFilterConfig":
        return cls(
            exclude_title_keywords=[s.lower() for s in raw.get("exclude_title_keywords", [])],
            exclude_text_keywords=[s.lower() for s in raw.get("exclude_text_keywords", [])],
            exclude_stack_in_title=[s.lower() for s in raw.get("exclude_stack_in_title", [])],
            exclude_locations_non_spain=[
                s.lower() for s in raw.get("exclude_locations_non_spain", [])
            ],
        )


def apply(offer: JobOffer, cfg: HardFilterConfig) -> tuple[bool, str | None]:
    """Return (keep, discard_reason). Pure function — no side effects."""
    title_lc = offer.title.lower()
    desc_lc = (offer.description or "").lower()
    loc_lc = (offer.location or "").lower()
    full_lc = f"{title_lc}\n{desc_lc}"

    for kw in cfg.exclude_title_keywords:
        if _contains_word(title_lc, kw):
            return False, f"title contains '{kw}'"

    for kw in cfg.exclude_stack_in_title:
        if _contains_word(title_lc, kw):
            return False, f"unwanted stack in title: '{kw}'"

    for kw in cfg.exclude_text_keywords:
        if kw in full_lc:
            return False, f"text contains '{kw}'"

    # Location: discard if it mentions a country other than Spain AND is_remote
    # is not explicitly True. Unknown locations are kept (AI will decide).
    if loc_lc and offer.is_remote is not True:
        for country in cfg.exclude_locations_non_spain:
            if country in loc_lc:
                return False, f"location outside Spain: '{country}'"

    return True, None


def partition(
    offers: Iterable[JobOffer], cfg: HardFilterConfig
) -> tuple[list[JobOffer], list[JobOffer]]:
    """Split offers into (kept, discarded). Mutates discarded_reason on rejects."""
    kept: list[JobOffer] = []
    discarded: list[JobOffer] = []
    for o in offers:
        keep, reason = apply(o, cfg)
        if keep:
            kept.append(o)
        else:
            discarded.append(o.model_copy(update={"discarded_reason": reason}))
    return kept, discarded


# --- helpers ---

@lru_cache(maxsize=None)
def _word_pattern(needle: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")


def _contains_word(haystack: str, needle: str) -> bool:
    """Word-ish containment: 'senior' should match 'Senior Developer' but not
    'senioritis'. For multi-word/punctuated needles (e.g. '.net', 'tech lead',
    '5+ years') we fall back to substring."""
    if not needle:
        return False
    if any(ch in needle for ch in (" ", "+", ".", "/", "-")):
        return needle in haystack
    return _word_pattern(needle).search(haystack) is not None
