from __future__ import annotations

from datetime import datetime, timezone

import pytest

from job_agent.models import JobOffer, Source


def _offer(
    *,
    title: str = "Junior Full Stack Developer",
    company: str = "Acme",
    source: Source = Source.LINKEDIN,
    url: str = "https://example.com/job/1",
    location: str | None = "Madrid, Spain",
    is_remote: bool | None = True,
    description: str = "Junior role with React and Node.js.",
) -> JobOffer:
    return JobOffer.build(
        title=title,
        company=company,
        source=source,
        url=url,
        location=location,
        is_remote=is_remote,
        description=description,
        published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def make_offer():
    return _offer


@pytest.fixture
def sample_offers():
    return [
        _offer(title="Junior Full Stack Developer", url="https://ex.com/a"),
        _offer(title="Senior Backend Engineer", url="https://ex.com/b"),
        _offer(title="Frontend React Developer", url="https://ex.com/c"),
        _offer(title="Tech Lead Java", url="https://ex.com/d"),
        _offer(title=".NET Backend Developer", url="https://ex.com/e"),
        _offer(
            title="Full Stack Developer",
            location="London, UK",
            is_remote=False,
            url="https://ex.com/f",
        ),
        _offer(title="AI Engineer Junior", url="https://ex.com/g"),
        _offer(
            title="Backend Developer",
            description="Required: fluent English, 6+ years of experience.",
            url="https://ex.com/h",
        ),
    ]
