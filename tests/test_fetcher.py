from datetime import date

from src.fetcher import fetch_arxiv


def test_arxiv_fetch():
    papers = fetch_arxiv(
        categories=["cs.AI"],
        max_results=5,
        date_from=date(2026, 1, 1),
    )

    assert isinstance(papers, list)
