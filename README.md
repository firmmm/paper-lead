# Paper Lead - AI Research Digest Agent

Agent that fetches the latest AI papers daily, summarizes them, and delivers a digest to the team - like having an R&D Lead who reads everything for you.

## Why

- AI moves fast - 100+ new papers per day, nobody can keep up
- Teams miss breakthroughs without a dedicated reader
- Paper Lead = wake up to a 5-minute summary, know where the field is at

## Architecture

```
arXiv API --\
             +--> Fetcher --> Ranker --> Summarizer --> Publisher --> Digest
HF Daily ---/     (Track A)   (Track A)    (Track B)     (Track B)
```

## 2-Person Task Split

| Track A - Paper Fetcher & Ranker | Track B - Summarizer & Publisher |
|---|---|
| Fetch papers from arXiv API + HuggingFace Daily Papers | Summarize papers via LLM |
| Filter + score by relevance against interest profile | Categorize: Must Read / Worth Reading / Tools |
| Store metadata in SQLite (dedup) | Generate digest markdown |
| `src/fetcher.py`, `src/ranker.py` | `src/summarizer.py`, `src/publisher.py` |

## Interface (JSON schema agreed by both tracks)

```json
// Track A sends to Track B:
{
  "date": "2026-06-18",
  "papers": [
    {
      "id": "2306.xxxxx",
      "title": "Mamba-3: Linear Attention at Scale",
      "abstract": "...",
      "authors": ["..."],
      "url": "https://arxiv.org/abs/2306.xxxxx",
      "score": 0.92,
      "topics": ["architecture", "efficiency"]
    }
  ]
}

// Track B outputs digest markdown:
{
  "digest": "# Paper Lead - 2026-06-18\n...",
  "stats": {
    "total_fetched": 87,
    "total_filtered": 12,
    "must_read": 2,
    "worth_reading": 7,
    "tools": 3
  }
}
```

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env  # add your API keys
cp config.example.yaml config.yaml  # customize interests
python src/main.py --dry-run
```

## Config

See `config.example.yaml`

## Output

Digest saved as markdown in `digest/YYYY-MM-DD.md`

## License

MIT
