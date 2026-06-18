from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from src.fetcher import Paper

DEFAULT_DB_PATH = os.getenv("PAPER_LEAD_DB_PATH", "data/paper_lead.sqlite3")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    fetched_date TEXT NOT NULL,
    score REAL NOT NULL,
    digest_date TEXT
);
"""


def _coerce_db_path(db_path: str | os.PathLike | None = None) -> Path:
    return Path(db_path or DEFAULT_DB_PATH)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect(db_path: str | os.PathLike | None = None):
    path = _coerce_db_path(db_path)
    _ensure_parent(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | os.PathLike | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def _today_iso() -> str:
    return date.today().isoformat()


def is_seen(paper_id: str, db_path: str | os.PathLike | None = None) -> bool:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM papers WHERE id = ? LIMIT 1", (paper_id,)).fetchone()
        return row is not None


def mark_seen(
    paper_or_id,
    score: float | None = None,
    title: str | None = None,
    fetched_date: str | date | datetime | None = None,
    digest_date: str | date | datetime | None = None,
    db_path: str | os.PathLike | None = None,
) -> None:
    init_db(db_path)

    if hasattr(paper_or_id, "id"):
        paper_id = getattr(paper_or_id, "id")
        title = title or getattr(paper_or_id, "title", "")
        if score is None:
            score = float(getattr(paper_or_id, "score", 0.0))
    else:
        paper_id = str(paper_or_id)

    if score is None:
        score = 0.0

    if fetched_date is None:
        fetched_date_str = _today_iso()
    elif isinstance(fetched_date, datetime):
        fetched_date_str = fetched_date.date().isoformat()
    elif isinstance(fetched_date, date):
        fetched_date_str = fetched_date.isoformat()
    else:
        fetched_date_str = str(fetched_date)

    if digest_date is None:
        digest_date_str = None
    elif isinstance(digest_date, datetime):
        digest_date_str = digest_date.date().isoformat()
    elif isinstance(digest_date, date):
        digest_date_str = digest_date.isoformat()
    else:
        digest_date_str = str(digest_date)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (id, title, fetched_date, score, digest_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                fetched_date = excluded.fetched_date,
                score = excluded.score,
                digest_date = COALESCE(excluded.digest_date, papers.digest_date)
            """,
            (paper_id, title or "", fetched_date_str, float(score), digest_date_str),
        )


def filter_seen(
    papers: Sequence[Paper],
    db_path: str | os.PathLike | None = None,
) -> list[Paper]:
    """Filter out already-seen papers using a single batch query."""
    init_db(db_path)
    if not papers:
        return []

    ids = [p.id for p in papers]
    with _connect(db_path) as conn:
        # Batch query with IN clause
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id FROM papers WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        seen_ids = {row["id"] for row in rows}

    return [p for p in papers if p.id not in seen_ids]


def mark_seen_many(
    papers: Sequence,
    db_path: str | os.PathLike | None = None,
) -> None:
    """Mark multiple papers as seen in a single transaction."""
    init_db(db_path)
    with _connect(db_path) as conn:
        for paper in papers:
            paper_id = getattr(paper, "id", str(paper))
            title = getattr(paper, "title", "")
            score = float(getattr(paper, "score", 0.0))
            fetched_date_str = _today_iso()
            digest_date_str = _today_iso()

            conn.execute(
                """
                INSERT INTO papers (id, title, fetched_date, score, digest_date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    fetched_date = excluded.fetched_date,
                    score = excluded.score,
                    digest_date = COALESCE(excluded.digest_date, papers.digest_date)
                """,
                (paper_id, title or "", fetched_date_str, score, digest_date_str),
            )
