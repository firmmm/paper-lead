"""Paper Lead — Main Orchestrator

เชื่อม Track A (fetcher + ranker) กับ Track B (summarizer + publisher)
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

# Track B imports
from summarizer import summarize_batch
from publisher import publish_digest


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.yaml"
        if not config_path.exists():
            config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_papers(config: dict, date_from: str = None) -> list:
    """ดึง papers จาก Track A — ถ้ายังไม่เสร็จจะใช้ sample data"""
    try:
        from fetcher import fetch_arxiv, fetch_huggingface_daily
        from ranker import rank_papers
        from db import filter_seen

        all_papers = []
        if config.get("sources", {}).get("arxiv"):
            arxiv_cfg = config["sources"]["arxiv"]
            all_papers.extend(fetch_arxiv(
                categories=arxiv_cfg.get("categories", ["cs.AI", "cs.CL", "cs.LG"]),
                max_results=arxiv_cfg.get("max_results", 50),
                date_from=date_from,
            ))

        if config.get("sources", {}).get("huggingface", {}).get("daily_papers"):
            all_papers.extend(fetch_huggingface_daily(date=date_from or date.today().isoformat()))

        new_papers = filter_seen(all_papers)
        ranked = rank_papers(new_papers, config.get("interests", []), config.get("min_score", 0.5))
        return [p.__dict__ if hasattr(p, "__dict__") else p for p in ranked]

    except ImportError:
        print("⚠️  Track A not available, using sample data")
        return _sample_papers()


def _sample_papers() -> list:
    return [
        {
            "id": "2306.12345",
            "title": "Mamba-3: Linear Attention at Scale",
            "abstract": "We present Mamba-3, a new architecture that achieves 3x speedup over Transformers on long sequences while maintaining quality.",
            "url": "https://arxiv.org/abs/2306.12345",
            "score": 0.92,
            "topics": ["architecture", "efficiency"],
            "published_date": "2026-06-17",
        },
        {
            "id": "2306.67890",
            "title": "Thai-LLaMA 7B: Fine-tuned for Thai NLU",
            "abstract": "We fine-tune LLaMA 7B on Thai corpora, achieving state-of-the-art results on Thai NLU benchmarks, surpassing GPT-4o-mini.",
            "url": "https://arxiv.org/abs/2306.67890",
            "score": 0.88,
            "topics": ["nlp", "thai"],
            "published_date": "2026-06-17",
        },
        {
            "id": "2306.11111",
            "title": "Efficient LoRA Merging for Multi-Task Learning",
            "abstract": "We propose a method to merge multiple LoRA adapters without catastrophic forgetting.",
            "url": "https://arxiv.org/abs/2306.11111",
            "score": 0.65,
            "topics": ["fine-tuning", "peft"],
            "published_date": "2026-06-16",
        },
        {
            "id": "2306.22222",
            "title": "vLLM 0.8: PagedAttention v2",
            "abstract": "vLLM 0.8 introduces PagedAttention v2 with 2x throughput improvement.",
            "url": "https://arxiv.org/abs/2306.22222",
            "score": 0.55,
            "topics": ["tool", "framework", "inference"],
            "published_date": "2026-06-16",
        },
    ]


def main():
    parser = argparse.ArgumentParser(description="📰 Paper Lead — AI Research Digest Agent")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--date", default=None, help="Date to fetch (YYYY-MM-DD), default: yesterday")
    parser.add_argument("--dry-run", action="store_true", help="Print digest without saving")
    args = parser.parse_args()

    config = load_config(args.config)

    # Default: yesterday's papers
    date_from = args.date or (date.today() - timedelta(days=1)).isoformat()
    print(f"📰 Fetching papers from {date_from}...")

    # Step 1: Fetch & Rank (Track A)
    papers = fetch_papers(config, date_from)
    print(f"   Found {len(papers)} relevant papers")

    if not papers:
        print("   No papers found. Done.")
        return

    # Step 2: Summarize (Track B)
    print("🤖 Summarizing with LLM...")
    result = summarize_batch(papers, config.get("llm", {}))

    # Step 3: Publish (Track B)
    if args.dry_run:
        print("\n" + "=" * 60)
        print(result.digest)
        print("=" * 60)
        print(f"📊 Stats: {json.dumps(result.stats, indent=2)}")
    else:
        results = publish_digest(result.digest, result.stats, config)
        print(f"✅ Published to: {json.dumps(results, indent=2)}")


if __name__ == "__main__":
    main()
