"""Paper Lead - Summarizer (Track B)

Receives a list of papers, summarizes them via LLM in batches,
categorizes, and produces a digest markdown.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Import shared type from Track A
from src.ranker import RankedPaper

logger = logging.getLogger(__name__)

# Load .env
load_dotenv(Path(__file__).parent.parent / ".env")

# Max papers per LLM call to avoid timeout
BATCH_SIZE = 10
# Seconds to wait between batch calls (rate-limit safety)
BATCH_DELAY = 2
# Seconds before falling back to next model
FALLBACK_TIMEOUT = 60


@dataclass
class DigestResult:
    digest: str  # markdown content
    stats: dict = field(default_factory=dict)


def load_prompt_template(path: str = None) -> str:
    """Load prompt template from prompts/summarize.md"""
    if path is None:
        path = Path(__file__).parent.parent / "prompts" / "summarize.md"
    return Path(path).read_text()


def init_llm_client(config: dict) -> OpenAI:
    """Create OpenAI client from config. Raises ValueError if no API key found."""
    api_key = os.environ.get("SIAM_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Missing API key: set SIAM_AI_API_KEY or OPENAI_API_KEY in .env")
    kwargs = {"api_key": api_key}
    if base_url := config.get("base_url"):
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def categorize_papers(papers: list[RankedPaper]) -> dict:
    """Categorize papers by score. A paper can belong to multiple categories."""
    categories = {
        "must_read": [],      # score > 0.8
        "worth_reading": [],  # 0.5-0.8
        "tools": [],          # topics contain tool/framework/benchmark
    }

    for p in papers:
        is_tool = any(t in p.topics for t in ["tool", "framework", "benchmark", "library", "release"])

        if p.score > 0.8:
            categories["must_read"].append(p)
        elif p.score >= 0.5:
            categories["worth_reading"].append(p)

        if is_tool:
            categories["tools"].append(p)

    return categories


def _build_papers_text(papers: list[RankedPaper], offset: int = 0) -> str:
    """Build text representation of papers for LLM prompt."""
    papers_text = ""
    for i, p in enumerate(papers, offset + 1):
        abstract_text = p.abstract[:500]
        if len(p.abstract) > 500:
            abstract_text += "..."
        papers_text += f"\n---\nPaper {i}:\n"
        papers_text += f"Title: {p.title}\n"
        papers_text += f"Abstract: {abstract_text}\n"
        papers_text += f"URL: {p.url}\n"
        papers_text += f"Relevance Score: {p.score}\n"
        papers_text += f"Topics: {', '.join(p.topics)}\n"
    return papers_text


def _call_llm(client: OpenAI, model: str, system_prompt: str, user_prompt: str, timeout: int = FALLBACK_TIMEOUT, fallbacks: list[str] | None = None) -> str | None:
    """Call LLM with timeout and fallback models. Returns content or None on failure."""
    models_to_try = [model]
    if fallbacks:
        models_to_try.extend(fallbacks)

    for i, m in enumerate(models_to_try):
        try:
            logger.info(f"Calling LLM: {m} (attempt {i+1}/{len(models_to_try)})")
            response = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                timeout=timeout,
            )
            choice = response.choices[0]
            if choice.finish_reason == "length":
                logger.warning("LLM response was truncated (finish_reason=length). Digest may be incomplete.")
            logger.info(f"LLM responded successfully with {m}")
            return choice.message.content
        except Exception as e:
            logger.warning(f"LLM call with {m} failed: {e}")
            if i < len(models_to_try) - 1:
                logger.info(f"Falling back to {models_to_try[i+1]}...")
            continue

    logger.error("All LLM models failed")
    return None


def summarize_batch(
    papers: list[dict],
    llm_config: dict,
    prompt_template: str = None,
) -> DigestResult:
    """
    Summarize a batch of papers via LLM, splitting into sub-batches
    if there are too many papers for a single call.

    Args:
        papers: list of paper dicts (from Track A interface via to_dict())
        llm_config: {provider, model, base_url?}
        prompt_template: custom prompt (defaults to prompts/summarize.md)

    Returns:
        DigestResult with markdown digest + stats
    """
    if prompt_template is None:
        prompt_template = load_prompt_template()

    # Convert dicts to RankedPaper using the shared type
    ranked_papers: list[RankedPaper] = []
    for p in papers:
        ranked_papers.append(RankedPaper(
            id=p.get("id", ""),
            title=p.get("title", ""),
            abstract=p.get("abstract") or "",
            authors=p.get("authors", []),
            url=p.get("url", ""),
            score=p.get("score", 0.0),
            topics=p.get("topics", []),
            published_date=p.get("published_date", ""),
        ))

    # Sort by score
    ranked_papers.sort(key=lambda p: p.score, reverse=True)

    # Categorize
    categories = categorize_papers(ranked_papers)

    today = date.today().isoformat()
    model = llm_config.get("model", "gpt-4o-mini")
    fallbacks = llm_config.get("fallbacks", [])

    # Split into sub-batches
    total = len(ranked_papers)
    if total <= BATCH_SIZE:
        # Small enough for a single call
        papers_text = _build_papers_text(ranked_papers)
        digest_content = _summarize_single_batch(llm_config, model, fallbacks, prompt_template, papers_text, today)
    else:
        # Split into multiple batches
        logger.info(f"Splitting {total} papers into batches of {BATCH_SIZE}")
        digest_content = _summarize_multi_batch(ranked_papers, llm_config, model, fallbacks, prompt_template, today)

    # Fallback: manual digest if LLM fails completely
    if digest_content is None:
        logger.info("Using manual digest fallback")
        digest_content = _manual_digest(categories, today)

    stats = {
        "total_fetched": len(papers),
        "total_filtered": len(ranked_papers),
        "must_read": len(categories["must_read"]),
        "worth_reading": len(categories["worth_reading"]),
        "tools": len(categories["tools"]),
    }

    return DigestResult(digest=digest_content, stats=stats)


def _summarize_single_batch(
    llm_config: dict,
    model: str,
    fallbacks: list[str],
    system_prompt: str,
    papers_text: str,
    today: str,
) -> str | None:
    """Summarize a single batch of papers via LLM."""
    try:
        client = init_llm_client(llm_config)
    except ValueError as e:
        logger.error(f"LLM client init failed: {e}")
        return None

    user_prompt = f"Date: {today}\n\nPapers to summarize:\n\n{papers_text}\n\nCreate the digest now."
    return _call_llm(client, model, system_prompt, user_prompt, fallbacks=fallbacks)


def _summarize_multi_batch(
    ranked_papers: list[RankedPaper],
    llm_config: dict,
    model: str,
    fallbacks: list[str],
    system_prompt: str,
    today: str,
) -> str | None:
    """Summarize papers in multiple batches, then merge results."""
    try:
        client = init_llm_client(llm_config)
    except ValueError as e:
        logger.error(f"LLM client init failed: {e}")
        return None

    batch_summaries: list[str] = []
    total = len(ranked_papers)

    for i in range(0, total, BATCH_SIZE):
        batch = ranked_papers[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"Summarizing batch {batch_num}/{total_batches} ({len(batch)} papers)...")

        papers_text = _build_papers_text(batch, offset=i)
        user_prompt = (
            f"Date: {today}\n"
            f"This is batch {batch_num} of {total_batches}.\n\n"
            f"Papers to summarize:\n\n{papers_text}\n\n"
            f"Create the digest for this batch."
        )

        content = _call_llm(client, model, system_prompt, user_prompt, fallbacks=fallbacks)
        if content:
            batch_summaries.append(content)
            logger.info(f"Batch {batch_num}/{total_batches} done")
        else:
            logger.warning(f"Batch {batch_num}/{total_batches} failed, skipping")

        # Rate-limit delay between batches
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)

    if not batch_summaries:
        return None

    # If only one batch succeeded, return it directly
    if len(batch_summaries) == 1:
        return batch_summaries[0]

    # Merge multiple batch summaries into one digest
    logger.info(f"Merging {len(batch_summaries)} batch summaries...")
    merged_text = "\n\n".join(
        f"--- Batch {i+1} ---\n{summary}"
        for i, summary in enumerate(batch_summaries)
    )

    merge_prompt = (
        f"Date: {today}\n\n"
        f"The following are digest summaries from {len(batch_summaries)} batches of papers.\n"
        f"Merge them into a single cohesive digest. Remove duplicates, "
        f"consolidate categories (Must Read, Worth Reading, Tools), and keep the best insights.\n\n"
        f"{merged_text}"
    )

    merged = _call_llm(client, model, system_prompt, merge_prompt, timeout=90, fallbacks=fallbacks)
    return merged or merged_text  # Fall back to raw concatenation if merge fails


def _manual_digest(categories: dict, today: str) -> str:
    """Fallback: generate digest without LLM"""
    lines = [f"# Paper Lead - {today}", ""]

    if categories["must_read"]:
        lines.append("## Must Read")
        for p in categories["must_read"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    if categories["worth_reading"]:
        lines.append("## Worth Reading")
        for p in categories["worth_reading"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    if categories["tools"]:
        lines.append("## Tools & Frameworks")
        for p in categories["tools"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    return "\n".join(lines)


# --- CLI for testing ---
if __name__ == "__main__":
    import sys

    import yaml

    # Read papers from file or use sample data
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            papers = json.load(f).get("papers", [])
    else:
        # Sample data for testing
        papers = [
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

    # Load config
    config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(level=logging.INFO)
    result = summarize_batch(papers, config.get("llm", {}))
    print(result.digest)
    print(f"\nStats: {json.dumps(result.stats, indent=2)}")
