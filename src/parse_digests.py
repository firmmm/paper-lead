"""Parse digest markdown files into the knowledge base.

Reads digest/YYYY-MM-DD.md files (118 files, June 2026 -> present), extracts
per-paper entries and per-day metadata, and upserts them into the SQLite
tables used by the KB API.

Tables:
    digest_days    - one row per digest file (date, tldr, stats, entry counts)
    digest_entries - one row per paper/tool entry (summary, why, section, ...)

Idempotent: re-running replaces rows for the dates it parses (delete+insert
per date), so the parser is safe to re-run after every daily digest run.

Usage:
    python3 -m src.parse_digests                    # all files in digest/
    python3 -m src.parse_digests --only 2026-11-18  # single date
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DIGEST_DIR = Path(__file__).parent.parent / "digest"

SECTION_KEYS = ("must_read", "worth_reading", "tools")

# Section headers: "**Must Read**", "## Worth Reading" (2026-10-31 style),
# "🔥 **Must Read**" (emoji-prefixed variants)
SECTION_RE = re.compile(
    r"^\s*[#*]{0,6}\s*(?:[^\w\s*#]+\s*)?\**\s*"
    r"(Must Read|Worth Reading|Tools & Frameworks)\s*\**\s*$",
    re.IGNORECASE,
)

# Numbered entry: "1. **[Title](url)**"  (url optional for tools)
ENTRY_RE = re.compile(r"^\s*\d+\.\s*\*\*(.+?)\*\*\s*$")
# Bullet variants (2026-10-31 style): "- [Title](url)" or "- **Title**"
LINK_BULLET_RE = re.compile(r"^\s*-\s*\[(.+?)\]\((.+?)\)\s*$")
BOLD_BULLET_RE = re.compile(r"^\s*-\s*\*\*(.+?)\*\*\s*$")

# Markdown link inside bold: "[Title](url)"
LINK_RE = re.compile(r"^\[(.+?)\]\((.+?)\)$")

# "   Summary: ..." / "   *Opinion: ...*" (italic) / "   **Why:** ..." (bold key)
FIELD_RE = re.compile(r"^\s+\*{0,2}\s*(Summary|Why|What|Opinion)\s*:\s*(.*)$", re.IGNORECASE)

# Day-level notes: "**Analyst's Note:** ...", "**ความเห็นจาก Analyst:** ..."
DAY_NOTE_RE = re.compile(
    r"^\s*\*{0,2}\s*(Analysts?'?s?\s*Notes?|ความเห็น[^:]*)\s*:\s*(.*)$",
    re.IGNORECASE,
)
# Day-level opinion at column 0 (indented ones are entry fields)
DAY_OPINION_RE = re.compile(r"^\*{1,2}\s*Opinions?\s*:\s*(.*)$", re.IGNORECASE)

# Italic TL;DR line right under the H1
TLDR_RE = re.compile(r"^\*(TL;DR:.+?)\*\s*$", re.MULTILINE)

# Footer stats, two flavors:
#   "Stats: 60 papers scanned, 6 relevant, 2 must read"   (from summarizer)
#   "Stats: 20 scanned, 20 relevant, 0 must read"          (from publisher)
STATS_RE = re.compile(
    r"Stats:\s*(\d+)\s*(papers\s*)?scanned,\s*(\d+)\s*relevant,\s*(\d+)\s*must\s*read"
)

# arxiv id from URL like http://arxiv.org/abs/2611.21478v1
ARXIV_ID_RE = re.compile(r"arxiv\.org/abs/([^/]+?)(?:v\d+)?$", re.IGNORECASE)


@dataclass
class Entry:
    section: str  # must_read | worth_reading | tools
    title: str
    url: str | None = None
    arxiv_id: str | None = None
    summary: str | None = None
    why: str | None = None
    what: str | None = None
    opinion: str | None = None


@dataclass
class DigestDay:
    date: str
    tldr: str | None = None
    analyst_note: str | None = None
    papers_scanned: int | None = None
    papers_relevant: int | None = None
    must_read_count: int | None = None
    entries: list[Entry] = field(default_factory=list)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS digest_days (
    date TEXT PRIMARY KEY,
    tldr TEXT,
    papers_scanned INTEGER,
    papers_relevant INTEGER,
    must_read_count INTEGER,
    n_entries INTEGER,
    n_must_read INTEGER,
    n_worth_reading INTEGER,
    n_tools INTEGER,
    analyst_note TEXT,
    parsed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS digest_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    section TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    arxiv_id TEXT,
    summary TEXT,
    why TEXT,
    what TEXT,
    opinion TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON digest_entries(date);
CREATE INDEX IF NOT EXISTS idx_entries_arxiv ON digest_entries(arxiv_id);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, summary, why, what, opinion,
    content='digest_entries', content_rowid='id'
);
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract,
    content='papers', content_rowid='rowid'
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial KB schema (idempotent)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(digest_days)")}
    if cols and "analyst_note" not in cols:
        conn.execute("ALTER TABLE digest_days ADD COLUMN analyst_note TEXT")


def _section_key(header: str) -> str:
    h = header.lower()
    if "must" in h:
        return "must_read"
    if "worth" in h:
        return "worth_reading"
    return "tools"


def _clean(text: str) -> str:
    return text.strip().strip("*").strip()


def _add_note(day: DigestDay, note: str) -> None:
    note = _clean(note)
    if not note:
        return
    day.analyst_note = f"{day.analyst_note} {note}" if day.analyst_note else note


def parse_digest_text(text: str, date: str) -> DigestDay:
    """Parse one digest markdown file into a DigestDay."""
    day = DigestDay(date=date)

    m = TLDR_RE.search(text)
    if m:
        day.tldr = _clean(m.group(1))

    # Footer stats: prefer the summarizer line ("N papers scanned"); the
    # publisher appends a second footer with inflated "relevant" counts.
    stats = None  # (scanned, relevant, must, is_summarizer_flavor)
    for m in STATS_RE.finditer(text):
        scanned, flavor, relevant, must = m.groups()
        rec = (int(scanned), int(relevant), int(must), flavor is not None)
        if stats is None or rec[3]:
            stats = rec
    if stats is not None:
        day.papers_scanned = stats[0]
        day.papers_relevant = stats[1]
        day.must_read_count = stats[2]

    current_section: str | None = None
    current_entry: Entry | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        m = SECTION_RE.match(line)
        if m:
            current_section = _section_key(m.group(1))
            current_entry = None
            continue

        # --- entry start (numbered or bullet) ---
        title = url = None
        m = ENTRY_RE.match(line) or BOLD_BULLET_RE.match(line)
        if m and current_section:
            body = m.group(1).strip()
            link = LINK_RE.match(body)
            if link:
                title, url = link.group(1).strip(), link.group(2).strip()
            else:
                title = body
        else:
            m = LINK_BULLET_RE.match(line)
            if m and current_section:
                title, url = m.group(1).strip(), m.group(2).strip()
        if title:
            arxiv_id = None
            if url:
                am = ARXIV_ID_RE.search(url)
                if am:
                    arxiv_id = am.group(1)
            current_entry = Entry(section=current_section, title=title, url=url, arxiv_id=arxiv_id)
            day.entries.append(current_entry)
            continue

        # --- entry fields (indented Summary/Why/What/Opinion) ---
        if current_entry:
            m = FIELD_RE.match(raw_line)
            if m:
                key = m.group(1).lower()
                value = _clean(m.group(2))
                if key == "summary":
                    current_entry.summary = value
                elif key == "why":
                    current_entry.why = value
                elif key == "what":
                    current_entry.what = value
                elif key == "opinion":
                    current_entry.opinion = value
                continue

        # --- day-level analyst notes (any indent) ---
        m = DAY_NOTE_RE.match(line)
        if m:
            _add_note(day, m.group(2))
            continue
        # day-level "*Opinion: ...*" at column 0 (indented ones are entry fields)
        if not raw_line[:1].isspace():
            m = DAY_OPINION_RE.match(line)
            if m:
                _add_note(day, m.group(1))
                continue

        # --- wrapped-line continuation of the previous field ---
        if current_entry:
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "---", "*")):
                if current_entry.opinion is None and current_entry.what is None:
                    if current_entry.why is not None:
                        current_entry.why += " " + stripped
                    elif current_entry.summary is not None:
                        current_entry.summary += " " + stripped

    return day


def parse_digest_file(path: Path) -> DigestDay:
    date = path.stem
    text = path.read_text(encoding="utf-8")
    return parse_digest_text(text, date)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def write_day(conn: sqlite3.Connection, day: DigestDay) -> None:
    """Replace rows for this date (idempotent re-parse)."""
    conn.execute("DELETE FROM digest_entries WHERE date = ?", (day.date,))
    conn.execute("DELETE FROM digest_days WHERE date = ?", (day.date,))

    counts = {k: 0 for k in SECTION_KEYS}
    for e in day.entries:
        conn.execute(
            """INSERT INTO digest_entries
               (date, section, title, url, arxiv_id, summary, why, what, opinion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                day.date,
                e.section,
                e.title,
                e.url,
                e.arxiv_id,
                e.summary,
                e.why,
                e.what,
                e.opinion,
            ),
        )
        counts[e.section] += 1

    conn.execute(
        """INSERT INTO digest_days
           (date, tldr, papers_scanned, papers_relevant, must_read_count,
            n_entries, n_must_read, n_worth_reading, n_tools, analyst_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            day.date,
            day.tldr,
            day.papers_scanned,
            day.papers_relevant,
            day.must_read_count,
            len(day.entries),
            counts["must_read"],
            counts["worth_reading"],
            counts["tools"],
            day.analyst_note,
        ),
    )


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS indexes after bulk inserts (external-content tables)."""
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")


def parse_all(db_path: str | Path = "data/paper_lead.sqlite3", digest_dir: Path = DIGEST_DIR) -> int:
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        files = sorted(digest_dir.glob("*.md"))
        n = 0
        for f in files:
            try:
                day = parse_digest_file(f)
            except Exception as e:
                logger.error(f"Failed to parse {f.name}: {e}")
                continue
            write_day(conn, day)
            n += 1
        conn.commit()
        rebuild_fts(conn)
        conn.commit()
        return n
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Parse digest markdown into KB tables")
    parser.add_argument("--config", default=None, help="Unused; kept for CLI symmetry")
    parser.add_argument("--only", default=None, help="Parse a single date (YYYY-MM-DD)")
    parser.add_argument("--db", default="data/paper_lead.sqlite3")
    parser.add_argument("--digest-dir", default=str(DIGEST_DIR))
    args = parser.parse_args()

    conn = _connect(args.db)
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        if args.only:
            files = [Path(args.digest_dir) / f"{args.only}.md"]
        else:
            files = sorted(Path(args.digest_dir).glob("*.md"))
        n = 0
        for f in files:
            if not f.exists():
                logger.warning(f"Not found: {f}")
                continue
            day = parse_digest_file(f)
            write_day(conn, day)
            n += 1
        conn.commit()
        rebuild_fts(conn)
        conn.commit()
        logger.info(f"Parsed {n} digest files into {args.db}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
