from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src import db, fetcher, ranker
from src.fetcher import Paper
from src.ranker import RankedPaper

logger = logging.getLogger(__name__)


@dataclass
class Config:
    categories: list[str] = field(default_factory=lambda: ["cs.AI", "cs.LG", "cs.CL"])
    max_results: int = 100
    date_from: str = field(default_factory=lambda: date.today().isoformat())
    hf_date: str = field(default_factory=lambda: date.today().isoformat())

    interests: dict[str, list[str]] = field(
        default_factory=lambda: {
            "architecture": ["transformer", "attention", "mixture of experts", "moe"],
            "efficiency": ["quantization", "distillation", "pruning", "efficiency", "compression"],
            "rag": ["retrieval augmented generation", "rag", "retrieval", "reranking", "vector database"],
            "agents": ["agent", "tool use", "planning", "workflow", "function calling"],
            "multimodal": ["multimodal", "vision language", "image", "audio"],
            "training": ["fine-tuning", "pretraining", "alignment", "rlhf", "dpo"],
            "inference": ["inference", "serving", "latency", "throughput", "kv cache"],
        }
    )
    min_score: float = 0.30
    db_path: str = "data/paper_lead.sqlite3"


@dataclass
class PipelineResult:
    fetched: int
    unseen: int
    ranked: int
    papers: list[RankedPaper]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "unseen": self.unseen,
            "ranked": self.ranked,
            "papers": [p.to_dict() for p in self.papers],
        }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def load_config(path: str | None = None) -> Config:
    if not path:
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cfg = Config()
    for key, value in raw.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def fetch_all(config: Config) -> list[Paper]:
    arxiv_papers = fetcher.fetch_arxiv(
        categories=config.categories,
        max_results=config.max_results,
        date_from=_parse_date(config.date_from),
    )

    hf_papers = fetcher.fetch_huggingface_daily(_parse_date(config.hf_date) or date.today())

    merged: dict[str, Paper] = {p.id: p for p in arxiv_papers}
    for p in hf_papers:
        merged.setdefault(p.id, p)

    return list(merged.values())


def run_pipeline(config: Config) -> PipelineResult:
    db.init_db(config.db_path)

    all_papers = fetch_all(config)
    unseen_papers = db.filter_seen(all_papers, db_path=config.db_path)

    ranked_papers = ranker.rank_papers(
        papers=unseen_papers,
        interests=config.interests,
        min_score=config.min_score,
    )

    for paper in ranked_papers:
        db.mark_seen(
            paper_or_id=paper.id,
            score=paper.score,
            title=paper.title,
            digest_date=None,
            db_path=config.db_path,
        )

    return PipelineResult(
        fetched=len(all_papers),
        unseen=len(unseen_papers),
        ranked=len(ranked_papers),
        papers=ranked_papers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Lead pipeline")
    parser.add_argument("--config", type=str, default=None, help="Path to config JSON")
    parser.add_argument("--output", type=str, default=None, help="Optional path to save ranked results JSON")
    parser.add_argument("--log-level", type=str, default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    config = load_config(args.config)
    result = run_pipeline(config)

    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved output to %s", out_path)


if __name__ == "__main__":
    main()
