"""Paper Lead KB API - read-only FastAPI service over the SQLite knowledge base.

Run from repo root:
    python3 -m uvicorn src.api:app --host 0.0.0.0 --port 8000

Env:
    KB_API_KEY         client key (checked as X-API-Key header);
                       empty/unset = auth disabled (local dev mode)
    PAPER_LEAD_DB_PATH sqlite path (default: data/paper_lead.sqlite3)

The DB is opened read-only (mode=ro). The daily cron (digest + parse) is the
only writer, so the API can never corrupt or block the ingest pipeline.
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
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

_REPO = Path(__file__).parent.parent
load_dotenv(_REPO / ".env")

DB_PATH = os.getenv("PAPER_LEAD_DB_PATH", str(_REPO / "data" / "paper_lead.sqlite3"))
DIGEST_DIR = Path(os.getenv("PAPER_LEAD_DIGEST_DIR", str(_REPO / "digest")))
API_KEY = os.getenv("KB_API_KEY", "").strip()

app = FastAPI(
    title="Paper Lead KB API",
    version="1.0.0",
    description=(
        "Read-only knowledge-base API over the Paper Lead SQLite DB: "
        "paper metadata (papers), daily Thai digest summaries (digest_entries), "
        "and full-text search (FTS5). Auth: X-API-Key header when KB_API_KEY is set."
    ),
)

SECTIONS = ("must_read", "worth_reading", "tools")

# Paths reachable without the API key (docs + liveness only; no data)
_OPEN_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    if API_KEY and request.url.path not in _OPEN_PATHS:
        if request.headers.get("X-API-Key", "") != API_KEY:
            return JSONResponse(
                {"detail": "missing or invalid X-API-Key header"}, status_code=401
            )
    return await call_next(request)


# ---------------------------------------------------------------- DB helpers


@contextmanager
def connect_ro() -> Iterator[sqlite3.Connection]:
    if not Path(DB_PATH).exists():
        raise HTTPException(status_code=503, detail=f"database not found: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _validate_date(value: str, name: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"invalid {name}: {value!r} (expected YYYY-MM-DD)")
    return value


def _fts_query(q: str) -> str:
    """Sanitize user input into a safe FTS5 query (quoted tokens, implicit AND)."""
    tokens = re.findall(r"\w+", q, re.UNICODE)
    if not tokens:
        raise HTTPException(400, "query has no searchable tokens")
    return " ".join(f'"{t}"' for t in tokens)


def _authors(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [str(raw)]


def _fts_error(exc: sqlite3.OperationalError) -> None:
    raise HTTPException(400, f"invalid query: {exc}")


# ------------------------------------------------------------------- endpoints


@app.get("/health")
def health():
    """Liveness + quick DB stats. No auth required."""
    if not Path(DB_PATH).exists():
        return {"status": "degraded", "db": DB_PATH, "detail": "database not found"}
    with connect_ro() as conn:
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        try:
            days = conn.execute("SELECT COUNT(*) FROM digest_days").fetchone()[0]
            entries = conn.execute("SELECT COUNT(*) FROM digest_entries").fetchone()[0]
        except sqlite3.OperationalError:
            days = entries = 0  # KB tables not parsed yet
    return {
        "status": "ok",
        "db": DB_PATH,
        "papers": papers,
        "digest_days": days,
        "digest_entries": entries,
        "auth": "enabled" if API_KEY else "disabled",
    }


@app.get("/stats")
def stats():
    """Global KB counters."""
    with connect_ro() as conn:
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        try:
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
            first, last = rng["first"], rng["last"]
        except sqlite3.OperationalError:
            days, entries, by_section, first, last = 0, 0, {}, None, None
        score = conn.execute(
            "SELECT MIN(score) AS lo, MAX(score) AS hi, AVG(score) AS avg FROM papers"
        ).fetchone()
    return {
        "papers_total": papers,
        "digest_days": days,
        "entries_total": entries,
        "entries_by_section": by_section,
        "first_digest": first,
        "last_digest": last,
        "score": {"min": score["lo"], "max": score["hi"], "avg": round(score["avg"], 4)},
    }


@app.get("/papers")
def list_papers(
    date_: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    from_: str | None = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_: str | None = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    q: str | None = Query(None, description="FTS search over title+abstract"),
    with_abstract: bool = Query(False, description="include full abstracts in list results"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List papers. Set with_abstract=1 to get full abstracts inline."""
    for value, name in ((date_, "date"), (from_, "from"), (to_, "to")):
        if value:
            _validate_date(value, name)

    clauses, params = ["1=1"], []
    if date_:
        clauses.append("digest_date = ?")
        params.append(date_)
    if from_:
        clauses.append("digest_date >= ?")
        params.append(from_)
    if to_:
        clauses.append("digest_date <= ?")
        params.append(to_)
    if min_score > 0:
        clauses.append("score >= ?")
        params.append(min_score)
    where = " AND ".join(clauses)

    cols = "id, title, score, digest_date, url, published_date, authors"
    if with_abstract:
        cols += ", abstract"

    with connect_ro() as conn:
        if q:
            fts = _fts_query(q)
            base = (
                "FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid "
                "WHERE papers_fts MATCH ?"
            )
            try:
                total = _count_papers_fts(conn, fts, clauses, params)
                real = [c for c in clauses if c != "1=1"]
                where_part = (" AND " + " AND ".join("p." + c for c in real)) if real else ""
                rows = conn.execute(
                    f"SELECT p.id, p.title, p.score, p.digest_date, p.url, "
                    f"p.published_date, p.authors{', p.abstract' if with_abstract else ''} "
                    f"{base}{where_part} "
                    "ORDER BY papers_fts.rank LIMIT ? OFFSET ?",
                    [fts, *params, limit, offset],
                ).fetchall()
            except sqlite3.OperationalError as exc:
                _fts_error(exc)
        else:
            total = conn.execute(
                f"SELECT COUNT(*) FROM papers WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {cols} "
                f"FROM papers WHERE {where} "
                "ORDER BY digest_date DESC, score DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

    def to_item(r):
        item = {
            "id": r["id"],
            "title": r["title"],
            "score": r["score"],
            "digest_date": r["digest_date"],
            "url": r["url"],
            "published_date": r["published_date"],
            "authors": _authors(r["authors"]),
        }
        if with_abstract:
            item["abstract"] = r["abstract"]
        return item

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [to_item(r) for r in rows],
    }


def _count_papers_fts(
    conn: sqlite3.Connection, fts: str, clauses: list[str], params: list
) -> int:
    """COUNT for the FTS join with the same filters as list_papers."""
    real = [c for c in clauses if c != "1=1"]
    where_part = (" AND " + " AND ".join("p." + c for c in real)) if real else ""
    return conn.execute(
        "SELECT COUNT(*) FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid "
        f"WHERE papers_fts MATCH ?{where_part}",
        [fts, *params],
    ).fetchone()[0]


@app.get("/papers/{arxiv_id}")
def get_paper(arxiv_id: str):
    """One paper: full metadata + abstract + any digest entries (Thai summaries)."""
    with connect_ro() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (arxiv_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"paper not found: {arxiv_id}")
        entries = conn.execute(
            "SELECT date, section, title, url, summary, why, opinion "
            "FROM digest_entries WHERE arxiv_id = ? ORDER BY date",
            (arxiv_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "score": row["score"],
        "fetched_date": row["fetched_date"],
        "digest_date": row["digest_date"],
        "published_date": row["published_date"],
        "url": row["url"],
        "authors": _authors(row["authors"]),
        "abstract": row["abstract"],
        "digest_entries": [dict(e) for e in entries],
    }


@app.get("/digests")
def list_digests(
    from_: str | None = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_: str | None = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List available digest days (newest first)."""
    clauses, params = ["1=1"], []
    if from_:
        _validate_date(from_, "from")
        clauses.append("date >= ?")
        params.append(from_)
    if to_:
        _validate_date(to_, "to")
        clauses.append("date <= ?")
        params.append(to_)
    where = " AND ".join(clauses)

    with connect_ro() as conn:
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM digest_days WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT date, tldr, papers_scanned, papers_relevant, must_read_count, "
                "n_entries, n_must_read, n_worth_reading, n_tools, analyst_note "
                f"FROM digest_days WHERE {where} "
                "ORDER BY date DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(503, "KB tables missing - run: python3 -m src.parse_digests")

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [dict(r) for r in rows],
    }


@app.get("/digests/{day}")
def get_digest(day: str):
    """One digest day: TL;DR + entries grouped by section."""
    _validate_date(day, "date")
    with connect_ro() as conn:
        row = conn.execute("SELECT * FROM digest_days WHERE date = ?", (day,)).fetchone()
        if not row:
            raise HTTPException(404, f"no digest for {day}")
        entries = conn.execute(
            "SELECT section, title, url, arxiv_id, summary, why, what, opinion "
            "FROM digest_entries WHERE date = ? "
            "ORDER BY CASE section WHEN 'must_read' THEN 0 WHEN 'worth_reading' THEN 1 ELSE 2 END, id",
            (day,),
        ).fetchall()

    grouped = {s: [] for s in SECTIONS}
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


@app.get("/search")
def search(
    q: str = Query(..., min_length=1, description="FTS query, e.g. 'agent memory'"),
    section: str | None = Query(None, description="must_read | worth_reading | tools"),
    limit: int = Query(20, ge=1, le=100),
):
    """Full-text search over digest entries (Thai+English) AND paper abstracts."""
    fts = _fts_query(q)
    if section and section not in SECTIONS:
        raise HTTPException(400, f"invalid section: {section!r} (one of {SECTIONS})")

    sec_clause = "AND e.section = ?" if section else ""
    sec_params = [section] if section else []

    with connect_ro() as conn:
        try:
            entries = conn.execute(
                "SELECT e.date, e.section, e.title, e.url, e.arxiv_id, e.summary, e.why, e.what, e.opinion "
                "FROM entries_fts JOIN digest_entries e ON e.rowid = entries_fts.rowid "
                f"WHERE entries_fts MATCH ? {sec_clause} "
                "ORDER BY entries_fts.rank LIMIT ?",
                [fts, *sec_params, limit],
            ).fetchall()
            papers = conn.execute(
                "SELECT p.id, p.title, p.score, p.digest_date, p.url "
                "FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid "
                "WHERE papers_fts MATCH ? "
                "ORDER BY papers_fts.rank LIMIT ?",
                [fts, limit],
            ).fetchall()
        except sqlite3.OperationalError as exc:
            _fts_error(exc)

    return {
        "query": q,
        "entries": [dict(e) for e in entries],
        "papers": [dict(p) for p in papers],
    }


# ---------------------------------------------------------------- raw / export


def _export_filters(from_: str | None, to_: str | None, min_score: float):
    """Shared WHERE for export endpoints. Returns (where, params)."""
    clauses, params = ["1=1"], []
    if from_:
        _validate_date(from_, "from")
        clauses.append("digest_date >= ?")
        params.append(from_)
    if to_:
        _validate_date(to_, "to")
        clauses.append("digest_date <= ?")
        params.append(to_)
    if min_score > 0:
        clauses.append("score >= ?")
        params.append(min_score)
    return " AND ".join(clauses), params


@app.get("/export/papers.jsonl")
def export_papers_jsonl(
    from_: str | None = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_: str | None = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
):
    """Raw dump of ALL papers (metadata + abstracts) as JSON Lines."""
    where, params = _export_filters(from_, to_, min_score)

    def gen():
        with connect_ro() as conn:
            cur = conn.execute(
                "SELECT id, title, score, fetched_date, digest_date, published_date, "
                f"url, authors, abstract FROM papers WHERE {where} "
                "ORDER BY digest_date, id",
                params,
            )
            for row in cur:
                yield json.dumps(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "score": row["score"],
                        "fetched_date": row["fetched_date"],
                        "digest_date": row["digest_date"],
                        "published_date": row["published_date"],
                        "url": row["url"],
                        "authors": _authors(row["authors"]),
                        "abstract": row["abstract"],
                    },
                    ensure_ascii=False,
                ) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="papers.jsonl"'},
    )


@app.get("/export/papers.csv")
def export_papers_csv(
    from_: str | None = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_: str | None = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
):
    """Raw dump of ALL papers as CSV (abstract included as last column)."""
    import csv
    import io

    where, params = _export_filters(from_, to_, min_score)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "title", "score", "fetched_date", "digest_date", "published_date", "url", "authors", "abstract"]
    )
    with connect_ro() as conn:
        cur = conn.execute(
            "SELECT id, title, score, fetched_date, digest_date, published_date, "
            f"url, authors, abstract FROM papers WHERE {where} "
            "ORDER BY digest_date, id",
            params,
        )
        for row in cur:
            writer.writerow(
                [
                    row["id"],
                    row["title"],
                    row["score"],
                    row["fetched_date"],
                    row["digest_date"],
                    row["published_date"],
                    row["url"],
                    row["authors"] or "",
                    row["abstract"] or "",
                ]
            )
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="papers.csv"'},
    )


@app.get("/digests/{day}/raw")
def get_digest_raw(day: str):
    """Raw markdown of a daily digest file (as published)."""
    _validate_date(day, "date")
    path = DIGEST_DIR / f"{day}.md"
    if not path.exists():
        raise HTTPException(404, f"no digest file for {day}")
    return Response(
        path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )
