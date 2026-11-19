# Paper Lead

AI research digest agent: fetches papers (arXiv + HF Daily) → dedupes (SQLite) →
ranks by keyword interest → summarizes via LLM → publishes (file/Slack/Discord).
Full pipeline + KB/API/microservices: `README.md`, deployment: `docs/DEPLOY.md`.

## Run it

```bash
# Host (legacy)
python3 -m src.main --config config.yaml --dry-run   # test, prints digest (true no-op since 2026-11-19)
python3 -m src.main --config config.yaml              # real run, writes + delivers

# Docker microservices (preferred)
docker compose up -d api           # KB API — port 8000, restart unless-stopped
docker compose run --rm ingest     # daily digest + KB refresh — one-shot container
```

Must run as `-m src.main` from repo root (`python src/main.py` fails: `ModuleNotFoundError: src`).
Containers use `python -m ...` with WORKDIR /app (see `Dockerfile`).

## Config

- `config.yaml` — real config, **gitignored**, edit this one (not `config.example.yaml`)
- `interests.yaml` — ranker keywords
- `.env` — `GATEWAY_API_KEY`, `KB_API_KEY` (KB API auth), optional `SLACK_WEBHOOK`/`DISCORD_WEBHOOK`
- LLM model/fallbacks live under `config.yaml`'s `llm:` key — change model name there, no code edit needed (`src/summarizer.py` just forwards `llm_config["model"]`)

## Known gotchas

- `--dry-run` **used to** mark papers as seen (old trap; fixed 2026-11-19 — it
  now skips publish + mark_seen entirely, safe to test any day).
- Docker image runs as uid 1001 (= host `saiuser`) so bind-mount writes in
  `data/` + `digest/` stay saiuser-owned on the host.
- Containers are UTC — digest filenames use UTC dates (same as old host
  behavior; OpenClaw cron fires 09:00 ICT = 02:00 UTC).
- KB API opens sqlite `mode=ro` per request; the only DB writer is the ingest
  run (docker `ingest` or fallback host run).

## Tests

`tests/` covers `db.py`, `fetcher.py`, `ranker.py` only — no tests for `summarizer.py`,
`publisher.py`, or `main.py` (the LLM/delivery/orchestration path).

```bash
pytest tests/
```

## Knowledge Base (2026-11-19)

- `src/parse_digests.py` — parses `digest/*.md` into `digest_days` + `digest_entries`
  (+ `entries_fts`, `papers_fts`). Idempotent (delete+insert per date). Run as
  `python3 -m src.parse_digests` (repo root). The OpenClaw daily cron (09:00 ICT)
  runs `docker compose run --rm ingest` = digest pipeline + KB refresh in one
  container run (host fallback if docker is down).
- `src/api.py` — read-only FastAPI. Runs as docker container `paper-lead-api`
  (port 8000, `restart: unless-stopped`, survives reboot; docker.service enabled).
  Key: `.env` `KB_API_KEY` (X-API-Key header).
- Tables `digest_days`/`digest_entries`/FTS are KB-owned; the cron digest job
  is the only writer of `papers`. Parser writes KB tables right after.
- Old-format digests handled: emoji section headers, `##` headers, bullet
  entries (2026-10-31), italic `*Opinion:*`, analyst day-notes, double stats
  footers (summarizer flavor wins).
- `config.json.bak`, `src/digest/` (dead sample digest) removed 2026-11-19.
