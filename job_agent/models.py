from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl


class Source(str, Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    INFOJOBS = "infojobs"
    TECNOEMPLEO = "tecnoempleo"


_TRACKING_PARAM_RE = re.compile(r"(^|&)(utm_[^=&]+|trk|trackingId|refId)=[^&]*")

REMOTE_RE = re.compile(r"\b(remoto|teletrabajo|100% remoto|remote)\b", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """URL normalisation for deduplication: lowercase host, drop fragment,
    strip tracking query params, drop trailing slash on path."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query = parts.query
    if query:
        query = _TRACKING_PARAM_RE.sub("", "&" + query).lstrip("&")
    return urlunsplit((scheme, netloc, path, query, ""))


def offer_id_for(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


class JobOffer(BaseModel):
    id: str
    title: str
    company: str
    source: Source
    url: HttpUrl
    location: str | None = None
    is_remote: bool | None = None
    description: str = ""
    published_at: datetime | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    score: int | None = None
    score_reason: str | None = None  # caveat: pega corta o None si no hay aviso
    remote_per_ai: str | None = None  # "remoto"|"presencial"|"hibrido"|"desconocido"
    discarded_reason: str | None = None
    sent: bool = False

    @classmethod
    def build(
        cls,
        *,
        title: str,
        company: str,
        source: Source,
        url: str,
        location: str | None = None,
        is_remote: bool | None = None,
        description: str = "",
        published_at: datetime | None = None,
    ) -> "JobOffer":
        return cls(
            id=offer_id_for(url),
            title=title.strip(),
            company=(company or "").strip() or "—",
            source=source,
            url=url,  # type: ignore[arg-type]
            location=(location or "").strip() or None,
            is_remote=is_remote,
            description=(description or "")[:4000],
            published_at=published_at,
        )
