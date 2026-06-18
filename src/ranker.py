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


def _keyword_hit_score(title: str, abstract: str, keyword: str) -> float:
    kw = keyword.strip().lower()
    if not kw:
        return 0.0

    title_l = title.lower()
    abstract_l = abstract.lower()

    pattern = re.escape(kw)
    title_hit = re.search(rf"\b{pattern}\b", title_l) is not None
    abstract_hit = re.search(rf"\b{pattern}\b", abstract_l) is not None

    if title_hit:
        return 1.0
    if abstract_hit:
        return 0.6
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
                best = max(best, _keyword_hit_score(text_title, text_abstract, kw))
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
