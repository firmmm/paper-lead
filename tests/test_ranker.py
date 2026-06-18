from src.fetcher import Paper
from src.ranker import rank_papers


def test_rank():
    paper = Paper(
        id="1",
        title="Agent Planning with RAG",
        abstract="A new retrieval augmented generation method.",
        authors=["A"],
        url="x",
        published_date="2026-06-18",
    )

    ranked = rank_papers(
        [paper],
        interests={
            "agents": ["agent"],
            "rag": ["rag"]
        },
        min_score=0.0,
    )

    assert len(ranked) == 1
    assert ranked[0].score > 0
