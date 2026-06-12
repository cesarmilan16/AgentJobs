from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from job_agent.models import JobOffer, Source

_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    company          TEXT NOT NULL,
    source           TEXT NOT NULL,
    url              TEXT NOT NULL,
    location         TEXT,
    is_remote        INTEGER,
    description      TEXT,
    published_at     TEXT,
    captured_at      TEXT NOT NULL,
    score            INTEGER,
    score_reason     TEXT,
    remote_per_ai    TEXT,
    discarded_reason TEXT,
    sent             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_offers_captured_at ON offers(captured_at);
CREATE INDEX IF NOT EXISTS idx_offers_sent        ON offers(sent);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def exists(self, offer_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("SELECT 1 FROM offers WHERE id = ? LIMIT 1", (offer_id,))
            return cur.fetchone() is not None

    def filter_new(self, offers: Iterable[JobOffer]) -> list[JobOffer]:
        offers = list(offers)
        if not offers:
            return []
        ids = [o.id for o in offers]
        with self._conn() as conn:
            placeholders = ",".join("?" for _ in ids)
            cur = conn.execute(
                f"SELECT id FROM offers WHERE id IN ({placeholders})", ids
            )
            existing = {row["id"] for row in cur.fetchall()}
        return [o for o in offers if o.id not in existing]

    def upsert_many(self, offers: Iterable[JobOffer]) -> int:
        rows = [self._to_row(o) for o in offers]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO offers (id, title, company, source, url, location,
                                    is_remote, description, published_at, captured_at,
                                    score, score_reason, remote_per_ai,
                                    discarded_reason, sent)
                VALUES (:id, :title, :company, :source, :url, :location,
                        :is_remote, :description, :published_at, :captured_at,
                        :score, :score_reason, :remote_per_ai,
                        :discarded_reason, :sent)
                ON CONFLICT(id) DO UPDATE SET
                    score            = excluded.score,
                    score_reason     = excluded.score_reason,
                    remote_per_ai    = excluded.remote_per_ai,
                    discarded_reason = excluded.discarded_reason,
                    sent             = excluded.sent
                """,
                rows,
            )
        return len(rows)

    def mark_sent(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._conn() as conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE offers SET sent = 1 WHERE id IN ({placeholders})", ids
            )

    def count(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("SELECT COUNT(*) AS n FROM offers")
            return int(cur.fetchone()["n"])

    @staticmethod
    def _to_row(o: JobOffer) -> dict:
        return {
            "id": o.id,
            "title": o.title,
            "company": o.company,
            "source": o.source.value if isinstance(o.source, Source) else str(o.source),
            "url": str(o.url),
            "location": o.location,
            "is_remote": None if o.is_remote is None else int(o.is_remote),
            "description": o.description,
            "published_at": _iso(o.published_at),
            "captured_at": _iso(o.captured_at),
            "score": o.score,
            "score_reason": o.score_reason,
            "remote_per_ai": o.remote_per_ai,
            "discarded_reason": o.discarded_reason,
            "sent": int(o.sent),
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
