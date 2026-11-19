# Paper Lead - AI Research Digest Agent

Agent that fetches the latest AI papers daily, summarizes them, and delivers a digest to the team - like having an R&D Lead who reads everything for you.

## Why

- AI moves fast - 100+ new papers per day, nobody can keep up
- Teams miss breakthroughs without a dedicated reader
- Paper Lead = wake up to a 5-minute summary, know where the field is at

## Architecture

```
arXiv + HF Daily Papers
        │
        ▼
   Fetcher ──► Ranker ──► Summarizer ──► Publisher ──► digest/YYYY-MM-DD.md
   (dedupe: SQLite)        (LLM, Thai)     (file + webhook)      │
                                                             ▼
                                              parse_digests ──► SQLite KB
   papers_pdf/ ◄── fetch_pdfs (arXiv PDFs)                    │
        │                                                      ▼
        └────────────────► FastAPI KB API (read-only, port 8000)
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
- `config.example.yaml` — template to copy when setting up (matches the real config schema)

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
| `GET /papers` | list/filter papers (`date`, `from`, `to`, `min_score`, `q` FTS, `with_abstract=1`) |
| `GET /papers/{arxiv_id}` | one paper + abstract + its digest entries |
| `GET /papers/{arxiv_id}/pdf` | the paper's PDF from the local archive (papers_pdf/) |
| `GET /digests` | list digest days |
| `GET /digests/{date}` | TL;DR + analyst note + entries by section |
| `GET /digests/{date}/raw` | raw markdown of that day's digest |
| `GET /search?q=` | FTS over Thai summaries + abstracts (`section` filter) |
| `GET /export/papers.jsonl` | raw dump of ALL papers as JSON Lines |
| `GET /export/papers.csv` | raw dump of ALL papers as CSV |

Auth: `X-API-Key` header, key in `.env` (`KB_API_KEY`; empty = auth off).
Interactive docs at `/docs` (Swagger). DB is opened read-only (`mode=ro`),
so the API can never corrupt the ingest pipeline.

Diagrams: `docs/data-flow.png` (system flow) · `docs/data-model.png` (KB schema).

## Microservices (Docker)

One shared image (`ppl:v0.1.0`), four services (`docker-compose.yml`).
All endpoints are behind the nginx proxy on port 80 — no ports to remember:

- **proxy** — nginx entry point on port 80: `http://<host>/` → api,
  `http://<host>/mcp` → mcp (recommended URLs)
- **api** — long-running KB API: `docker compose up -d api` (port 8000,
  healthcheck, `restart: unless-stopped` — survives reboots, docker is enabled)
- **mcp** — MCP server for AI clients (Claude/Cursor/agents): port 9000,
  `docker compose --profile mcp up -d mcp`
- **ingest** — one-shot daily worker: `docker compose run --rm ingest`
  (digest pipeline + KB refresh + PDF top-up in one run)

`data/` and `digest/` are host bind mounts, so all state stays on the host
and container files land as `saiuser`-owned (image runs as uid 1001).
The OpenClaw daily cron (09:00 ICT) runs the ingest service and reports to
Telegram; if docker is down it falls back to a host run.

```bash
docker compose build          # build image
docker compose up -d api      # serve KB API
docker compose run --rm ingest  # manual daily run
```

## MCP server (for AI clients)

`paper-lead-mcp` (port 9000) exposes the KB as tools for AI clients
(Claude, Cursor, agents…) over streamable HTTP — no doc-reading needed,
the tool specs are embedded in the protocol.

**Endpoint:** `http://<server-ip>/mcp` (via the nginx proxy — port 80;
`http://<server-ip>:9000/mcp` also works) · **Tools:** `search_kb` (Thai+EN
FTS), `get_digest`, `get_paper`, `list_digest_days`, `kb_stats` — all read-only.

**Cursor** — `.cursor/mcp.json` in your project:
```json
{
  "mcpServers": {
    "paper-lead": { "url": "http://172.16.30.208/mcp" }
  }
}
```

**Claude Code** — one-time add:
```bash
claude mcp add --transport http paper-lead http://172.16.30.208/mcp
```

**Claude Desktop** — Settings → Connectors → Add custom connector →
paste the URL above (older builds: use the `mcp-remote` npm wrapper in
`claude_desktop_config.json`).

**Python agent** (any code that speaks MCP):
```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://172.16.30.208/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("search_kb", {"query": "agent memory"})
            print(res.content[0].text)

asyncio.run(main())
```

Then just ask the AI naturally: *"paper ล่าสุดเรื่อง KV cache มีอะไรบ้าง"* —
it picks the tools by itself.

> ⚠️ **Auth:** the MCP endpoint currently has **no auth** — safe only on the
> trusted internal network (data is read-only). Add an auth provider before
> exposing it past the firewall. REST API auth is separate (`KB_API_KEY`).

## License

MIT
