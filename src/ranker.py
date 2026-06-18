from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Mapping, Sequence

from src.fetcher import Paper


@dataclass(frozen=True)
class RankedPaper:
    id: str
    title: str
    abstract: str
    authors: list[str]
    url: str
    score: float
    topics: list[str] = field(default_factory=list)
    published_date: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_interests(interests) -> list[tuple[str, list[str]]]:
    if interests is None:
        return []

    if isinstance(interests, Mapping):
        groups: list[tuple[str, list[str]]] = []
        for topic, terms in interests.items():
            if isinstance(terms, str):
                groups.append((str(topic), [terms]))
            else:
                groups.append((str(topic), [str(t) for t in terms if str(t).strip()]))
        return [(topic, terms) for topic, terms in groups if terms]

    if isinstance(interests, str):
        return [(interests, [interests])]

    groups = []
    for term in interests:
        term = str(term).strip()
        if term:
            groups.append((term, [term]))
    return groups


# Aliases for multi-word keywords that regex word-boundary matching may miss
_KEYWORD_ALIASES: dict[str, list[str]] = {
    "tool use": ["tool use", "tool-use", "tool calling", "function calling"],
    "mixture of experts": ["mixture of experts", "moe", "mixtral"],
    "vision language": ["vision language", "vlm", "vision-language", "multimodal llm"],
    "large language model": ["large language model", "llm", "large language models"],
}


def _expand_keyword(keyword: str) -> list[str]:
    """Expand a keyword with its aliases if available."""
    kw = keyword.strip().lower()
    return _KEYWORD_ALIASES.get(kw, [keyword])


def _keyword_hit_score(title: str, abstract: str, keyword: str) -> float:
    """Score a keyword match against title and abstract.
    Uses simple substring matching to avoid word-boundary issues with multi-word keywords.
    """
    kw = keyword.strip().lower()
    if not kw:
        return 0.0

    title_l = title.lower()
    abstract_l = abstract.lower()

    if kw in title_l:
        return 1.0
    if kw in abstract_l:
        return 0.6
    return 0.0


def rank_papers(
    papers: Sequence[Paper],
    interests,
    min_score: float = 0.0,
) -> list[RankedPaper]:
    groups = _normalize_interests(interests)
    if not groups:
        return []

    ranked: list[RankedPaper] = []

    for paper in papers:
        text_title = paper.title or ""
        text_abstract = paper.abstract or ""

        topic_hits: list[str] = []
        group_scores: list[float] = []

        for topic, keywords in groups:
            best = 0.0
            for kw in keywords:
                # Expand keyword with aliases
                for expanded_kw in _expand_keyword(kw):
                    best = max(best, _keyword_hit_score(text_title, text_abstract, expanded_kw))
            group_scores.append(best)
            if best > 0:
                topic_hits.append(topic)

        score = sum(group_scores) / len(group_scores)

        if score < min_score:
            continue

        ranked.append(
            RankedPaper(
                id=paper.id,
                title=paper.title,
                abstract=paper.abstract,
                authors=list(paper.authors),
                url=paper.url,
                score=round(float(min(score, 1.0)), 4),
                topics=sorted(set(topic_hits)),
                published_date=paper.published_date,
            )
        )

    ranked.sort(key=lambda p: (p.score, p.published_date), reverse=True)
    return ranked
