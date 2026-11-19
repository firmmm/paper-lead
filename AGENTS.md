# Paper Lead

AI research digest agent: fetches papers (arXiv + HF Daily) → dedupes (SQLite) →
ranks by keyword interest → summarizes via LLM → publishes (file/Slack/Discord).
Full pipeline: `README.md`. Track A/B interface contract: `docs/SPEC.md`, `docs/TRACK_B.md`.

## Run it

```bash
python3 -m src.main --config config.yaml --dry-run   # test, prints digest
python3 -m src.main --config config.yaml              # real run, writes + delivers
```

Must run as `-m src.main` from repo root (`python src/main.py` fails: `ModuleNotFoundError: src`).

## Config

- `config.yaml` — real config, **gitignored**, edit this one (not `config.example.yaml`)
- `interests.yaml` — ranker keywords
- `.env` — `GATEWAY_API_KEY`, optional `SLACK_WEBHOOK`/`DISCORD_WEBHOOK`
- LLM model/fallbacks live under `config.yaml`'s `llm:` key — change model name there, no code edit needed (`src/summarizer.py` just forwards `llm_config["model"]`)

## Known gotchas

- `--dry-run` still calls `db.mark_seen()` for every summarized paper (see `src/main.py`,
  `publish_success = True` is set unconditionally on dry-run). It is **not** a no-op against
  `data/paper_lead.sqlite3` despite the name — a "test" run will make those papers invisible
  to the next real run.
- `requirements.txt` is missing `beautifulsoup4`, which `src/fetcher.py` imports for HF
  Daily Papers scraping. Install it manually if setting up fresh.
- `config.json.bak` and `src/digest/2026-06-18.md` are dead files (leftover from the
  JSON→YAML migration and a stray committed output) — not read by any code.

## Tests

`tests/` covers `db.py`, `fetcher.py`, `ranker.py` only — no tests for `summarizer.py`,
`publisher.py`, or `main.py` (the LLM/delivery/orchestration path).

```bash
pytest tests/
```

## Knowledge Base (2026-11-19)

- `src/parse_digests.py` — parses `digest/*.md` into `digest_days` + `digest_entries`
  (+ `entries_fts`, `papers_fts`). Idempotent (delete+insert per date). Run as
  `python3 -m src.parse_digests` (repo root). The OpenClaw daily cron runs it
  automatically after each digest.
- `src/api.py` — read-only FastAPI (uvicorn `src.api:app`, port 8000).
  Key: `.env` `KB_API_KEY` (X-API-Key header). Not a systemd service — start
  by hand or add a supervisor if it should survive reboots.
- Tables `digest_days`/`digest_entries`/FTS are KB-owned; the cron digest job
  is the only writer of `papers`. Parser writes KB tables right after.
- Old-format digests handled: emoji section headers, `##` headers, bullet
  entries (2026-10-31), italic `*Opinion:*`, analyst day-notes, double stats
  footers (summarizer flavor wins).
- `config.json.bak`, `src/digest/` (dead sample digest) removed 2026-11-19.
