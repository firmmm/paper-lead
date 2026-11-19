"""Archive local PDF copies of every paper in the KB.

Files land in papers_pdf/{id}.pdf (host bind mount, flat folder).
Resumable: existing files are skipped, failed downloads retry on the next
run. arXiv etiquette: single connection, >=3s delay between requests,
self-identifying User-Agent.

Usage:
    python3 -m src.fetch_pdfs                    # archive everything missing
    python3 -m src.fetch_pdfs --limit 20         # at most N papers this run
    python3 -m src.fetch_pdfs --only 2026-11-19  # one digest date
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent / "papers_pdf"

HEADERS = {
    "User-Agent": (
        "paper-lead/0.1 (research digest KB; "
        "https://github.com/firmmm/paper-lead)"
    )
}


def pdf_url_for(paper_id: str, url: str | None) -> str:
    """http(s)://arxiv.org/abs/2611.21478v1 -> .../pdf/2611.21478v1"""
    if url and "arxiv.org/abs/" in url:
        return url.replace("arxiv.org/abs/", "arxiv.org/pdf/")
    return f"https://arxiv.org/pdf/{paper_id}"


def fetch_missing(
    db_path: str | Path = "data/paper_lead.sqlite3",
    pdf_dir: Path = PDF_DIR,
    limit: int | None = None,
    only: str | None = None,
    delay: float = 3.0,
) -> tuple[int, int]:
    """Download PDFs for papers that don't have one yet. Returns (ok, fail)."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    if only:
        rows = conn.execute(
            "SELECT id, url FROM papers WHERE digest_date = ? ORDER BY id", (only,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, url FROM papers ORDER BY digest_date DESC, id"
        ).fetchall()
    conn.close()

    todo = [(pid, url) for pid, url in rows if not (pdf_dir / f"{pid}.pdf").exists()]
    logger.info(f"{len(todo)} PDFs to fetch (of {len(rows)} papers known)")

    ok = fail = 0
    session = requests.Session()
    session.headers.update(HEADERS)

    for i, (pid, url) in enumerate(todo, 1):
        dest = pdf_dir / f"{pid}.pdf"
        try:
            r = session.get(pdf_url_for(pid, url), timeout=60)
            if r.status_code == 200 and r.content[:5].startswith(b"%PDF"):
                dest.write_bytes(r.content)
                ok += 1
            else:
                fail += 1
                logger.warning(f"[{i}/{len(todo)}] {pid}: HTTP {r.status_code}, not a PDF")
        except Exception as e:
            fail += 1
            logger.warning(f"[{i}/{len(todo)}] {pid}: {e}")
        if i % 25 == 0:
            logger.info(f"progress: {i}/{len(todo)} (ok {ok}, fail {fail})")
        if i >= len(todo):
            break
        if limit and i >= limit:
            logger.info(f"limit {limit} reached, stopping")
            break
        time.sleep(delay)

    logger.info(f"done: {ok} downloaded, {fail} failed, {len(todo) - ok - fail} left for next run")
    return ok, fail


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Archive paper PDFs into papers_pdf/")
    parser.add_argument("--db", default="data/paper_lead.sqlite3")
    parser.add_argument("--pdf-dir", default=str(PDF_DIR))
    parser.add_argument("--limit", type=int, default=None, help="max papers to try this run")
    parser.add_argument("--only", default=None, help="only papers from this digest date (YYYY-MM-DD)")
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()

    fetch_missing(
        args.db,
        pdf_dir=Path(args.pdf_dir),
        limit=args.limit,
        only=args.only,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
