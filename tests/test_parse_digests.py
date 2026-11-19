"""Tests for src.parse_digests — KB parser."""

import sqlite3
import tempfile
from pathlib import Path

from src import parse_digests
from src.parse_digests import parse_digest_text, parse_all, write_day

SAMPLE = """# Paper Lead - 2026-11-18
*TL;DR: Test day with agent memory focus*

---

**Must Read**

1. **[Test Paper: Agent Memory Utility](http://arxiv.org/abs/2611.21478v1)**
   Summary: สรุปภาษาไทยเรื่อง agent memory retrieval
   Why: Because utility-based retrieval beats similarity

**Worth Reading**

1. **[Second Paper on Routing](https://arxiv.org/abs/2611.00001)**
   Summary: Another summary that continues
   on a second wrapped line
   *Opinion: มีประเด็นน่าสนใจ*

**Tools & Frameworks**

1. **TestTool**
   What: A benchmark tool

---
Stats: 16 papers scanned, 11 relevant, 3 must read

---
Stats: 16 scanned, 16 relevant, 0 must read
"""


def _fresh_db():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript(parse_digests.SCHEMA_SQL)
    conn.commit()
    return conn, f.name


def test_parse_digest_text_fields():
    day = parse_digest_text(SAMPLE, "2026-11-18")
    assert day.date == "2026-11-18"
    assert day.tldr is not None and "Test day" in day.tldr
    assert len(day.entries) == 3

    must = day.entries[0]
    assert must.section == "must_read"
    assert must.arxiv_id == "2611.21478"
    assert "agent memory" in must.summary
    assert must.why is not None

    worth = day.entries[1]
    assert worth.section == "worth_reading"
    assert "wrapped line" in worth.summary  # continuation line merged
    assert worth.opinion is not None

    tool = day.entries[2]
    assert tool.section == "tools"
    assert tool.what is not None and tool.url is None

    # stats parsed from the footer
    assert day.papers_scanned == 16
    assert day.papers_relevant == 11
    assert day.must_read_count == 3


def test_write_day_idempotent_and_fts():
    conn, path = _fresh_db()
    try:
        day = parse_digest_text(SAMPLE, "2026-11-18")
        write_day(conn, day)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM digest_entries").fetchone()[0] == 3

        # re-parse same date: replace, not duplicate
        write_day(conn, day)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM digest_entries").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM digest_days").fetchone()[0] == 1

        # FTS index reflects rows after rebuild
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        hits = conn.execute(
            "SELECT COUNT(*) FROM entries_fts WHERE entries_fts MATCH 'memory'"
        ).fetchone()[0]
        assert hits >= 1
    finally:
        conn.close()
        Path(path).unlink(missing_ok=True)


def test_parse_all_on_real_digests():
    """Full run against the real digest dir + a throwaway copy of the DB."""
    src = Path(__file__).parent.parent / "data" / "paper_lead.sqlite3"
    if not src.exists():
        return  # DB not built yet in this checkout; skip
    f = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    f.close()
    import shutil

    shutil.copy(src, f.name)
    try:
        n = parse_all(db_path=f.name)
        assert n >= 100  # 118 digest files as of 2026-11
    finally:
        Path(f.name).unlink(missing_ok=True)
