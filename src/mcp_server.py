"""Paper Lead KB — MCP server.

Exposes the knowledge base as tools for AI clients (Claude, Cursor, agents…)
over streamable HTTP. Same read-only SQLite as the REST API.

Run (from repo root / container):
    python3 -m src.mcp_server            # serves MCP on port 9000

Env:
    MCP_HOST=0.0.0.0  MCP_PORT=9000  PAPER_LEAD_DB_PATH=data/paper_lead.sqlite3

Tools:
    kb_stats()                       — overview counters
    search_kb(query, section, limit) — FTS over Thai summaries + abstracts
    get_digest(date)                 — one day's digest (TL;DR + sections)
    list_digest_days(from, to, limit)— available digest days
    get_paper(arxiv_id)              — metadata + abstract + digest entries
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

_REPO = Path(__file__).parent.parent
load_dotenv(_REPO / ".env")

DB_PATH = os.getenv("PAPER_LEAD_DB_PATH", str(_REPO / "data" / "paper_lead.sqlite3"))
SECTIONS = ("must_read", "worth_reading", "tools")

# NOTE: host/port MUST be set at construction time. FastMCP enables DNS-rebind
# protection when constructed with a localhost host (default), and changing
# settings.host afterwards does not disable it -> clients calling via the
# machine IP would get 421 Misdirected Request.
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "9000"))

mcp = FastMCP(
    "paper-lead-kb",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions=(
        "A daily AI-research knowledge base (cs.AI/cs.LG/cs.CL, Thai "
        "summaries, since June 2026).\n\n"
        "Three concepts \u2014 a PAPER is the raw arXiv record (English title, "
        "abstract, authors); a DIGEST is one day's published report (Thai, "
        "with a TL;DR); an ENTRY is one item inside a digest \u2014 a paper or "
        "tool with a Thai summary, a why-it-matters note, and a section: "
        "must_read | worth_reading | tools.\n\n"
        "Typical questions \u2192 tools: \"search for X\" \u2192 search_kb; \"what was "
        "in yesterday's digest\" \u2192 get_digest; \"tell me about paper "
        "2611.21478\" \u2192 get_paper. Unsure? Start with kb_stats."
    ),
)


# ---------------------------------------------------------------- db helpers


@contextmanager
def connect_ro() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _fts_query(q: str) -> str:
    tokens = re.findall(r"\w+", q, re.UNICODE)
    if not tokens:
        raise ValueError("query has no searchable tokens")
    return " ".join(f'"{t}"' for t in tokens)


def _authors(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [str(raw)]


def _validate_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid date {value!r} (expected YYYY-MM-DD)")
    return value


# ---------------------------------------------------------------- tools


@mcp.tool()
def kb_stats() -> dict:
    """Overview of the knowledge base: totals, date range, section counts."""
    with connect_ro() as conn:
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        days = conn.execute("SELECT COUNT(*) FROM digest_days").fetchone()[0]
        entries = conn.execute("SELECT COUNT(*) FROM digest_entries").fetchone()[0]
        by_section = {
            r["section"]: r["n"]
            for r in conn.execute(
                "SELECT section, COUNT(*) AS n FROM digest_entries GROUP BY section"
            )
        }
        rng = conn.execute(
            "SELECT MIN(date) AS first, MAX(date) AS last FROM digest_days"
        ).fetchone()
    return {
        "papers_total": papers,
        "digest_days": days,
        "entries_total": entries,
        "entries_by_section": by_section,
        "first_digest": rng["first"],
        "last_digest": rng["last"],
    }


@mcp.tool()
def search_kb(
    query: str,
    section: str | None = None,
    limit: int = 10,
) -> dict:
    """Free-text search (Thai OR English) across the whole KB at once:
    digest entries (Thai summaries + why-notes) and paper abstracts.
    Optional section filter: must_read | worth_reading | tools.
    Returns matching entries + papers; use get_paper for full details of a hit.
    """
    limit = max(1, min(limit, 50))
    if section and section not in SECTIONS:
        raise ValueError(f"invalid section {section!r} (one of {SECTIONS})")
    fts = _fts_query(query)
    sec_clause = "AND e.section = ?" if section else ""
    sec_params = [section] if section else []

    with connect_ro() as conn:
        entries = conn.execute(
            "SELECT e.date, e.section, e.title, e.arxiv_id, e.summary, e.why "
            "FROM entries_fts JOIN digest_entries e ON e.rowid = entries_fts.rowid "
            f"WHERE entries_fts MATCH ? {sec_clause} "
            "ORDER BY entries_fts.rank LIMIT ?",
            [fts, *sec_params, limit],
        ).fetchall()
        papers = conn.execute(
            "SELECT p.id, p.title, p.score, p.digest_date "
            "FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid "
            "WHERE papers_fts MATCH ? "
            "ORDER BY papers_fts.rank LIMIT ?",
            [fts, limit],
        ).fetchall()

    return {
        "query": query,
        "entries": [dict(e) for e in entries],
        "papers": [dict(p) for p in papers],
    }


@mcp.tool()
def get_digest(day: str) -> dict:
    """One day's digest report (day = YYYY-MM-DD): TL;DR, analyst note, stats,
    and every entry grouped by section \u2014 must_read (don't miss),
    worth_reading (interesting), tools (frameworks/benchmarks).
    Answers "what happened that day?". Use list_digest_days to find days."""
    _validate_date(day)
    with connect_ro() as conn:
        row = conn.execute("SELECT * FROM digest_days WHERE date = ?", (day,)).fetchone()
        if not row:
            raise ValueError(f"no digest for {day}")
        entries = conn.execute(
            "SELECT section, title, url, arxiv_id, summary, why, what, opinion "
            "FROM digest_entries WHERE date = ? "
            "ORDER BY CASE section WHEN 'must_read' THEN 0 WHEN 'worth_reading' THEN 1 ELSE 2 END, id",
            (day,),
        ).fetchall()

    grouped: dict[str, list] = {s: [] for s in SECTIONS}
    for e in entries:
        grouped.setdefault(e["section"], []).append(dict(e))

    return {
        "date": row["date"],
        "tldr": row["tldr"],
        "analyst_note": row["analyst_note"],
        "stats": {
            "papers_scanned": row["papers_scanned"],
            "papers_relevant": row["papers_relevant"],
            "must_read": row["must_read_count"],
            "entries": row["n_entries"],
        },
        "sections": grouped,
    }


@mcp.tool()
def list_digest_days(
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 30,
) -> dict:
    """Available digest days (days with a published digest), newest first,
    with per-day counts. Optional from_date/to_date filters (YYYY-MM-DD).
    Feed a date from here into get_digest."""
    limit = max(1, min(limit, 100))
    clauses, params = ["1=1"], []
    if from_date:
        clauses.append("date >= ?")
        params.append(_validate_date(from_date))
    if to_date:
        clauses.append("date <= ?")
        params.append(_validate_date(to_date))
    where = " AND ".join(clauses)

    with connect_ro() as conn:
        rows = conn.execute(
            "SELECT date, tldr, must_read_count, n_entries "
            f"FROM digest_days WHERE {where} "
            "ORDER BY date DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return {"days": [dict(r) for r in rows]}


@mcp.tool()
def get_paper(arxiv_id: str) -> dict:
    """One paper by arXiv id (e.g. "2611.21478"): the raw record \u2014 English
    title, abstract, authors \u2014 PLUS every digest entry written about it
    (Thai summary, why-note, section). The "raw + how it was judged" view."""
    with connect_ro() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (arxiv_id,)).fetchone()
        if not row:
            raise ValueError(f"paper not found: {arxiv_id}")
        entries = conn.execute(
            "SELECT date, section, title, summary, why, opinion "
            "FROM digest_entries WHERE arxiv_id = ? ORDER BY date",
            (arxiv_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "score": row["score"],
        "digest_date": row["digest_date"],
        "published_date": row["published_date"],
        "url": row["url"],
        "authors": _authors(row["authors"]),
        "abstract": row["abstract"],
        "digest_entries": [dict(e) for e in entries],
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
