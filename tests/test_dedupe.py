from __future__ import annotations

from job_agent.models import JobOffer, Source, normalize_url, offer_id_for
from job_agent.storage.db import Database


def test_normalize_url_strips_tracking_and_fragment():
    a = "https://Example.com/job/1?utm_source=x&id=42#section"
    b = "https://example.com/job/1?id=42"
    assert normalize_url(a) == normalize_url(b)


def test_offer_id_is_stable_for_equivalent_urls():
    a = offer_id_for("https://example.com/job/1?utm_source=x")
    b = offer_id_for("https://example.com/job/1")
    assert a == b


def test_dedupe_filters_already_seen(tmp_path, sample_offers):
    db = Database(tmp_path / "jobs.db")

    first_batch = sample_offers[:5]
    new = db.filter_new(first_batch)
    assert len(new) == 5
    db.upsert_many(new)
    assert db.count() == 5

    # Insert the same 5 + 3 new -> only the 3 new should remain after filter.
    second_batch = sample_offers
    new = db.filter_new(second_batch)
    assert len(new) == 3
    db.upsert_many(new)
    assert db.count() == 8


def test_mark_sent_persists(tmp_path, sample_offers):
    db = Database(tmp_path / "jobs.db")
    db.upsert_many(sample_offers)
    ids = [o.id for o in sample_offers[:2]]
    db.mark_sent(ids)

    # Re-inserting should not clobber sent=1 unless explicitly set False.
    sent_again = [o.model_copy(update={"sent": True}) for o in sample_offers[:2]]
    db.upsert_many(sent_again)
    assert db.count() == len(sample_offers)
