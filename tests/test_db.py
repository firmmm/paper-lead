import json
import sqlite3
import tempfile

from src import db
from src.fetcher import Paper


def test_seen():
    with tempfile.NamedTemporaryFile() as f:
        db.init_db(f.name)

        assert not db.is_seen("123", f.name)

        db.mark_seen(
            paper_or_id="123",
            score=0.9,
            title="test",
            db_path=f.name
        )

        assert db.is_seen("123", f.name)


def test_mark_seen_stores_metadata():
    with tempfile.NamedTemporaryFile() as f:
        db.init_db(f.name)

        paper = Paper(
            id="2611.22947",
            title="The Kernel Reality Check",
            abstract="Benchmarking efficient attention kernels.",
            authors=["Alice Smith", "Bob Jones"],
            url="https://arxiv.org/abs/2611.22947",
            published_date="2026-11-18",
        )
        db.mark_seen(
            paper_or_id=paper,
            score=0.55,
            digest_date="2026-11-19",
            db_path=f.name,
        )

        conn = sqlite3.connect(f.name)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", ("2611.22947",)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["title"] == "The Kernel Reality Check"
        assert row["abstract"] == "Benchmarking efficient attention kernels."
        assert json.loads(row["authors"]) == ["Alice Smith", "Bob Jones"]
        assert row["url"] == "https://arxiv.org/abs/2611.22947"
        assert row["published_date"] == "2026-11-18"
        assert row["score"] == 0.55
        assert row["digest_date"] == "2026-11-19"


def test_migration_adds_columns_to_old_schema():
    """Old DBs (pre-metadata schema) must migrate in place without data loss."""
    with tempfile.NamedTemporaryFile() as f:
        # Create an old-style DB (original 5-column schema)
        conn = sqlite3.connect(f.name)
        conn.execute(
            """
            CREATE TABLE papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                fetched_date TEXT NOT NULL,
                score REAL NOT NULL,
                digest_date TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO papers (id, title, fetched_date, score, digest_date) "
            "VALUES ('2606.20506', 'FreeStyle', '2026-06-19', 0.4, '2026-06-19')"
        )
        conn.commit()
        conn.close()

        # init_db should migrate without touching existing rows
        db.init_db(f.name)

        conn = sqlite3.connect(f.name)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM papers WHERE id = '2606.20506'").fetchone()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
        conn.close()

        assert row["title"] == "FreeStyle"  # old data survived
        assert row["abstract"] is None       # new columns exist, empty
        assert {"abstract", "authors", "url", "published_date"} <= cols
