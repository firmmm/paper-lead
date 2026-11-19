"""Backfill missing metadata (abstract/authors/url/published_date) via the arXiv API.

Only touches rows where abstract IS NULL, so the script is safe to re-run
and resumes where it stopped. Non-null values are never overwritten.

Usage (from repo root):
    python3 -m src.backfill [--db data/paper_lead.sqlite3] [--batch-size 100]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time

import arxiv

from src.fetcher import _clean_text, _extract_arxiv_id

logger = logging.getLogger(__name__)


def fetch_null_ids(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id FROM papers WHERE abstract IS NULL ORDER BY digest_date, id"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def backfill_batch(conn: sqlite3.Connection, ids: list[str], client: arxiv.Client) -> int:
    """Fetch metadata for one batch of ids; update rows. Returns count updated."""
    search = arxiv.Search(id_list=list(ids), max_results=len(ids))
    updated = 0
    for result in client.results(search):
        paper_id = _extract_arxiv_id(getattr(result, "entry_id", None))
        if not paper_id:
            continue
        published = result.published.date().isoformat()
        abstract = _clean_text(result.summary)
        authors = json.dumps([a.name for a in result.authors])
        conn.execute(
            "UPDATE papers SET abstract = ?, authors = ?, url = ?, published_date = ? WHERE id = ?",
            (abstract or None, authors, result.entry_id, published, paper_id),
        )
        updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill paper metadata from arXiv")
    parser.add_argument("--db", default="data/paper_lead.sqlite3")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=3.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    ids = fetch_null_ids(args.db)
    logger.info("Rows missing abstract: %d", len(ids))
    if not ids:
        logger.info("Nothing to backfill. Done.")
        return

    client = arxiv.Client(
        page_size=args.batch_size, delay_seconds=args.delay_seconds, num_retries=5
    )
    conn = sqlite3.connect(args.db)

    total_updated = 0
    batches = [ids[i : i + args.batch_size] for i in range(0, len(ids), args.batch_size)]
    for i, batch in enumerate(batches, 1):
        try:
            updated = backfill_batch(conn, batch, client)
        except Exception as e:
            # Batch stays NULL; a re-run will retry it. Keep going with the rest.
            logger.warning("Batch %d/%d failed (%s) - skipped, retry on next run", i, len(batches), e)
            time.sleep(10)
            continue
        total_updated += updated
        conn.commit()
        logger.info("Batch %d/%d: updated %d/%d", i, len(batches), updated, len(batch))

    conn.close()

    remaining = fetch_null_ids(args.db)
    logger.info(
        "Done. Updated %d/%d rows. Remaining NULL: %d", total_updated, len(ids), len(remaining)
    )
    if remaining:
        logger.info("Still missing: %s", ", ".join(remaining[:20]))


if __name__ == "__main__":
    main()
