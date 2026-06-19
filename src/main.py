"""Paper Lead - Main Orchestrator

Connects Track A (fetcher + ranker) with Track B (summarizer + publisher)
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from src import db, fetcher, ranker
from src.fetcher import Paper
from src.ranker import RankedPaper
from src.summarizer import summarize_batch
from src.publisher import publish_digest

logger = logging.getLogger(__name__)


@dataclass
class Config:
    categories: list[str] = field(default_factory=lambda: ["cs.AI", "cs.LG", "cs.CL"])
    max_results: int = 100
    date_from: str = ""
    hf_date: str = ""
    min_score: float = 0.1
    max_papers: int = 20
    db_path: str = "data/paper_lead.sqlite3"
    llm: dict = field(default_factory=lambda: {
        "provider": "openai",
        "model": "GEMMA-4",
        "base_url": "https://gateway-llm.siam.ai",
        "fallbacks": ["GLM-5"],
    })
    delivery: dict = field(default_factory=dict)
    digest: dict = field(default_factory=dict)
    interests_file: str = "interests.yaml"
    interests: dict = field(default_factory=dict)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def load_config(path: str | None = None) -> Config:
    """Load config from YAML or JSON"""
    cfg = Config()
    if not path:
        return cfg

    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        if p.suffix == ".json":
            raw = json.load(f)
        else:
            raw = yaml.safe_load(f)

    for key, value in raw.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    # Load interests from separate file if interests dict is empty
    if not cfg.interests:
        cfg.interests = load_interests(cfg.interests_file)

    return cfg


def load_interests(path: str = "interests.yaml") -> dict:
    """Load interest keywords from a separate YAML file.
    Falls back to built-in defaults if file not found.
    """
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            interests = yaml.safe_load(f)
            if isinstance(interests, dict):
                logger.info(f"Loaded interests from {p}")
                return interests

    # Built-in fallback
    logger.warning(f"Interests file {p} not found, using built-in defaults")
    return {
        "agents": ["agent", "tool use", "planning", "workflow", "function calling"],
        "architecture": ["transformer", "attention", "moe", "mamba"],
        "efficiency": ["quantization", "distillation", "pruning"],
    }


def fetch_all_papers(config: Config) -> list[Paper]:
    """Fetch papers from arXiv + HuggingFace"""
    date_from = _parse_date(config.date_from) or (date.today() - timedelta(days=1))
    hf_date = _parse_date(config.hf_date) or date.today()

    arxiv_papers = fetcher.fetch_arxiv(
        categories=config.categories,
        max_results=config.max_results,
        date_from=date_from,
    )
    try:
        hf_papers = fetcher.fetch_huggingface_daily(hf_date)
    except Exception as e:
        logger.warning(f"HF fetch failed (skipping): {e}")
        hf_papers = []

    # Merge - deduplicate by paper id
    merged: dict[str, Paper] = {p.id: p for p in arxiv_papers}
    for p in hf_papers:
        merged.setdefault(p.id, p)
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser(description="Paper Lead - AI Research Digest Agent")
    parser.add_argument("--config", default=None, help="Path to config.yaml or config.json")
    parser.add_argument("--date", default=None, help="Date to fetch (YYYY-MM-DD), default: yesterday")
    parser.add_argument("--dry-run", action="store_true", help="Print digest without saving")
    parser.add_argument("--output", default=None, help="Save ranked papers as JSON")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    config = load_config(args.config)

    # Override date if specified
    if args.date:
        config.date_from = args.date

    if not config.date_from:
        config.date_from = (date.today() - timedelta(days=1)).isoformat()
    if not config.hf_date:
        config.hf_date = date.today().isoformat()

    # Step 1: Fetch & Rank (Track A)
    logger.info(f"Fetching papers from {config.date_from}...")
    all_papers = fetch_all_papers(config)
    logger.info(f"   Fetched {len(all_papers)} papers")

    db.init_db(config.db_path)
    unseen_papers = db.filter_seen(all_papers, db_path=config.db_path)
    logger.info(f"   {len(unseen_papers)} new (unseen) papers")

    ranked_papers = ranker.rank_papers(
        papers=unseen_papers,
        interests=config.interests,
        min_score=config.min_score,
    )
    logger.info(f"   {len(ranked_papers)} relevant after ranking")

    if not ranked_papers:
        logger.info("No relevant papers found. Done.")
        return

    # Limit papers sent to LLM to avoid timeout
    max_papers = config.max_papers if hasattr(config, 'max_papers') and config.max_papers else 20
    if len(ranked_papers) > max_papers:
        logger.info(f"   Limiting to top {max_papers} papers for summarization (out of {len(ranked_papers)})")
        ranked_papers = ranked_papers[:max_papers]

    # Save ranked papers as JSON if requested
    if args.output:
        payload = {
            "date": config.date_from,
            "papers": [p.to_dict() for p in ranked_papers],
        }
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Saved ranked papers to {out_path}")

    # Step 2: Summarize (Track B)
    papers_dicts = [p.to_dict() for p in ranked_papers]
    logger.info("Summarizing with LLM...")
    result = summarize_batch(papers_dicts, config.llm)

    # Step 3: Publish (Track B)
    publish_success = False
    if args.dry_run:
        print("\n" + "=" * 60)
        print(result.digest)
        print("=" * 60)
        print(f"Stats: {json.dumps(result.stats, indent=2)}")
        publish_success = True  # dry-run counts as success
    else:
        pub_config = {
            "delivery": config.delivery,
            "digest": config.digest,
        }
        results = publish_digest(result.digest, result.stats, pub_config)
        logger.info(f"Published to: {json.dumps(results, indent=2)}")
        # Consider publish successful if at least one delivery worked
        publish_success = any(v == "sent" or (isinstance(v, str) and v.endswith(".md")) for v in results.values())

    # Step 4: Mark seen in DB ONLY if publish succeeded
    # This prevents papers from being lost if publishing fails
    if publish_success:
        for paper in ranked_papers:
            db.mark_seen(
                paper_or_id=paper.id,
                score=paper.score,
                title=paper.title,
                digest_date=date.today().isoformat(),
                db_path=config.db_path,
            )
        logger.info(f"Marked {len(ranked_papers)} papers as seen")
    else:
        logger.warning("Publish failed - papers NOT marked as seen, will retry next run")


if __name__ == "__main__":
    main()
