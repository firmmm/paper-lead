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
cp config.example.yaml config.yaml  # copy template
# edit interests.yaml to customize your research topics
python src/main.py --config config.yaml --dry-run
```

## Config

- `config.yaml` — main config (categories, LLM, delivery, thresholds)
- `interests.yaml` — keyword profiles used by the ranker (edit this to tune what papers match)
- `config.example.yaml` — template to copy when setting up

See `config.example.yaml` and `interests.yaml` for available options.

## Output

Digest saved as markdown in `digest/YYYY-MM-DD.md`

## Knowledge Base + API

The daily digests are parsed into SQLite (KB tables + FTS5) and served by a
read-only FastAPI service:

```bash
# refresh KB tables from digest/*.md (also run automatically by the daily cron)
python3 -m src.parse_digests

# serve the API on port 8000
python3 -m uvicorn src.api:app --host 0.0.0.0 --port 8000
```

| Endpoint | What |
|---|---|
| `GET /health` | liveness + DB counts (no auth) |
| `GET /stats` | global KB counters |
| `GET /papers` | list/filter papers (`date`, `from`, `to`, `min_score`, `q` FTS) |
| `GET /papers/{arxiv_id}` | one paper + abstract + its digest entries |
| `GET /digests` | list digest days |
| `GET /digests/{date}` | TL;DR + analyst note + entries by section |
| `GET /search?q=` | FTS over Thai summaries + abstracts (`section` filter) |

Auth: `X-API-Key` header, key in `.env` (`KB_API_KEY`; empty = auth off).
Interactive docs at `/docs` (Swagger). DB is opened read-only (`mode=ro`),
so the API can never corrupt the ingest pipeline.

Data flow diagram: `docs/data-flow.html` (SVG in page) / `docs/data-flow.png`.

## License

MIT
