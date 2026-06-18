"""Paper Lead — Summarizer (Track B)

รับ list of papers → สรุปด้วย LLM → จัดหมวดหมู่ → สร้าง digest markdown
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

# โหลด .env
load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class RankedPaper:
    id: str
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    url: str = ""
    score: float = 0.0
    topics: list[str] = field(default_factory=list)
    published_date: str = ""


@dataclass
class DigestResult:
    digest: str  # markdown content
    stats: dict = field(default_factory=dict)


def load_prompt_template(path: str = None) -> str:
    """โหลด prompt template จาก prompts/summarize.md"""
    if path is None:
        path = Path(__file__).parent.parent / "prompts" / "summarize.md"
    return Path(path).read_text()


def init_llm_client(config: dict) -> OpenAI:
    """สร้าง OpenAI client จาก config — fail ถ้าไม่มี API key"""
    api_key = os.environ.get("SIAM_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Missing API key: set SIAM_AI_API_KEY or OPENAI_API_KEY in .env")
    kwargs = {"api_key": api_key}
    if base_url := config.get("base_url"):
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def categorize_papers(papers: list[RankedPaper]) -> dict:
    """จัดหมวดหมู่ papers ตาม score — paper อยู่ได้หลายหมวด"""
    categories = {
        "must_read": [],    # score > 0.8
        "worth_reading": [],  # 0.5-0.8
        "tools": [],        # topics มี tool/framework/benchmark
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


def summarize_batch(
    papers: list[dict],
    llm_config: dict,
    prompt_template: str = None,
) -> DigestResult:
    """
    สรุป batch ของ papers ด้วย LLM

    Args:
        papers: list of paper dicts (จาก Track A interface)
        llm_config: {provider, model, base_url?}
        prompt_template: custom prompt (ถ้าไม่ใส่จะใช้ default)

    Returns:
        DigestResult with markdown digest + stats
    """
    if prompt_template is None:
        prompt_template = load_prompt_template()

    # Convert to RankedPaper
    ranked_papers = []
    for p in papers:
        abstract = p.get("abstract") or ""
        ranked_papers.append(RankedPaper(
            id=p.get("id", ""),
            title=p.get("title", ""),
            abstract=abstract,
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

    # Build paper summary for LLM — ใส่ ... เฉพาะตอนตัดจริง
    papers_text = ""
    for i, p in enumerate(ranked_papers, 1):
        abstract_text = p.abstract[:500]
        if len(p.abstract) > 500:
            abstract_text += "..."
        papers_text += f"\n---\nPaper {i}:\n"
        papers_text += f"Title: {p.title}\n"
        papers_text += f"Abstract: {abstract_text}\n"
        papers_text += f"URL: {p.url}\n"
        papers_text += f"Relevance Score: {p.score}\n"
        papers_text += f"Topics: {', '.join(p.topics)}\n"

    # Call LLM
    today = date.today().isoformat()
    digest_content = None

    try:
        client = init_llm_client(llm_config)
        response = client.chat.completions.create(
            model=llm_config.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": prompt_template},
                {"role": "user", "content": f"Date: {today}\n\nPapers to summarize:\n\n{papers_text}\n\nCreate the digest now."},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        digest_content = response.choices[0].message.content
    except ValueError as e:
        logger.error(f"LLM client init failed: {e}")
    except Exception as e:
        logger.error(f"LLM summarization failed: {e}")

    # Fallback: สร้าง digest แบบ manual ถ้า LLM ไม่ได้
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


def _manual_digest(categories: dict, today: str) -> str:
    """Fallback: สร้าง digest แบบไม่ใช้ LLM"""
    lines = [f"# 📰 Paper Lead — {today}", ""]

    if categories["must_read"]:
        lines.append("🔥 **Must Read**")
        for p in categories["must_read"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    if categories["worth_reading"]:
        lines.append("📋 **Worth Reading**")
        for p in categories["worth_reading"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    if categories["tools"]:
        lines.append("💡 **Tools & Frameworks**")
        for p in categories["tools"]:
            lines.append(f"- [{p.title}]({p.url})")
        lines.append("")

    return "\n".join(lines)


# --- CLI for testing ---
if __name__ == "__main__":
    import sys

    import yaml

    # รับ papers จาก stdin หรือไฟล์
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            papers = json.load(f).get("papers", [])
    else:
        # Sample data สำหรับ test
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

    # โหลด config
    config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(level=logging.INFO)
    result = summarize_batch(papers, config.get("llm", {}))
    print(result.digest)
    print(f"\n📊 Stats: {json.dumps(result.stats, indent=2)}")
