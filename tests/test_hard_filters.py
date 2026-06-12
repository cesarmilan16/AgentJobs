from __future__ import annotations

import pytest

from job_agent.filtering.hard_filters import HardFilterConfig, apply, partition
from job_agent.models import JobOffer, Source


@pytest.fixture
def cfg() -> HardFilterConfig:
    return HardFilterConfig.from_dict({
        "exclude_title_keywords": [
            "senior", "lead", "principal", "architect", "tech lead",
            "staff", "head of", "manager", "director",
        ],
        "exclude_text_keywords": [
            "5+ years", "6+ years", "7+ years", "abap", "sap", "cobol", "mainframe",
        ],
        "exclude_stack_in_title": [".net", "php"],
        "exclude_locations_non_spain": [
            "portugal", "uk", "united kingdom", "germany", "france",
        ],
    })


def _o(**kw) -> JobOffer:
    base = dict(
        title="Junior Full Stack Developer",
        company="Acme",
        source=Source.LINKEDIN,
        url="https://ex.com/" + kw.get("title", "x").replace(" ", "-"),
        location="Madrid, Spain",
        is_remote=True,
        description="React + Node.js role.",
    )
    base.update(kw)
    return JobOffer.build(**base)


def test_keeps_junior_remote_spain(cfg):
    o = _o()
    keep, reason = apply(o, cfg)
    assert keep is True
    assert reason is None


def test_discards_senior_in_title(cfg):
    o = _o(title="Senior Backend Engineer")
    keep, reason = apply(o, cfg)
    assert keep is False
    assert "senior" in reason


def test_discards_tech_lead(cfg):
    o = _o(title="Tech Lead Frontend")
    keep, reason = apply(o, cfg)
    assert keep is False
    # Either "lead" or "tech lead" can be the first matching keyword.
    assert "lead" in reason


def test_discards_dotnet_stack_in_title(cfg):
    o = _o(title=".NET Backend Developer")
    keep, reason = apply(o, cfg)
    assert keep is False
    assert ".net" in reason


def test_discards_5_plus_years_in_description(cfg):
    o = _o(description="Looking for someone with 5+ years of experience.")
    keep, reason = apply(o, cfg)
    assert keep is False
    assert "5+ years" in reason


def test_discards_uk_on_site(cfg):
    o = _o(location="London, UK", is_remote=False)
    keep, reason = apply(o, cfg)
    assert keep is False
    assert "uk" in reason


def test_keeps_uk_company_if_remote(cfg):
    # Remote can come from a UK-based company; if is_remote=True, we keep it
    # and let the AI scorer decide.
    o = _o(location="London, UK", is_remote=True)
    keep, _ = apply(o, cfg)
    assert keep is True


def test_keeps_sevilla_onsite(cfg):
    o = _o(location="Sevilla, Spain", is_remote=False)
    keep, _ = apply(o, cfg)
    assert keep is True


def test_keeps_unknown_location(cfg):
    o = _o(location=None, is_remote=None)
    keep, _ = apply(o, cfg)
    assert keep is True


def test_senior_word_boundary_not_a_substring(cfg):
    # Should not match e.g. "senioritis" hypothetically — our keyword 'senior'
    # uses word boundaries. The realistic title 'Senior' is matched.
    o = _o(title="Senioritis Engineer")
    # 'senior' as substring would match; word-boundary fails. Should keep.
    keep, _ = apply(o, cfg)
    assert keep is True


def test_partition_splits_correctly(cfg):
    offers = [
        _o(title="Junior Backend", url="https://ex.com/1"),
        _o(title="Senior Frontend", url="https://ex.com/2"),
        _o(title=".NET Developer", url="https://ex.com/3"),
        _o(title="AI Engineer", url="https://ex.com/4"),
    ]
    kept, discarded = partition(offers, cfg)
    assert len(kept) == 2
    assert len(discarded) == 2
    assert all(o.discarded_reason for o in discarded)
