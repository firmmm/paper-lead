# Deploy Guide

Paper Lead runs as three Docker services from one shared image (`ppl:v0.1.0`):

- **api** — long-running REST KB API (port 8000). `restart: unless-stopped`,
  comes back by itself after a host reboot (docker.service is enabled).
- **mcp** — MCP server for AI clients (port 9000, profile `mcp`). Same
  restart policy.
- **ingest** — one-shot daily worker (digest + KB refresh + PDF top-up),
  started by cron: `docker compose run --rm ingest`.

State lives on the **host** via bind mounts — nothing important lives
inside a container:

```
data/paper_lead.sqlite3   # KB: papers + digest_days + digest_entries + FTS
digest/*.md               # daily digests (source of truth for KB tables)
papers_pdf/               # arXiv PDF archive (~13 GB at 2,255 papers)
.env                      # GATEWAY_API_KEY, KB_API_KEY (commented = auth off), webhooks
config.yaml               # main config (gitignored)
interests.yaml            # ranker keywords
```

## Same machine, after reboot

Containers come back on their own. Verify:

```bash
docker ps                                    # paper-lead-api + paper-lead-mcp Up
curl -s http://localhost:8000/health         # {"status":"ok",...}
# MCP: connect any client to http://<ip>:9000/mcp
```

One manual thing: if the **PDF bulk fetch** (host process, not a container)
was still running when the machine went down, restart it — it's resumable
and skips existing files:

```bash
cd ~/paper-lead && nohup python3 -m src.fetch_pdfs > /tmp/fetch_pdfs_full.log 2>&1 &
ls papers_pdf/ | wc -l   # should keep climbing toward 2255
```

Only exception: if you ran `docker compose stop` yourself before the
reboot, the containers respect that — start them again with
`docker compose --profile mcp up -d`.

## Move to a new machine

```bash
# 0. New machine needs: docker + compose plugin (and python3 for the host
#    PDF fetcher). Then:

# 1. Copy EVERYTHING — code + state (rsync handles the gitignored parts):
rsync -av oldmachine:~/paper-lead/ ~/paper-lead/
#    (git clone works too, but then copy the state items listed above by hand)

cd ~/paper-lead

# 2. If your uid is not 1001, edit the USER line in Dockerfile to match —
#    else container-written files (digest/, papers_pdf/) will be owned by uid 1001.

# 3. Build once, start both long-running services:
docker compose build
docker compose --profile mcp up -d          # api (8000) + mcp (9000)
curl -s http://localhost:8000/health

# 4. Resume the PDF bulk fetch on the host (skips what rsync already brought):
nohup python3 -m src.fetch_pdfs > /tmp/fetch_pdfs_full.log 2>&1 &

# 5. Daily ingest — pick one:
#    a) plain crontab (09:00 Bangkok = 02:00 UTC):
crontab -e
0 2 * * * cd $HOME/paper-lead && docker compose run --rm ingest >> /tmp/paper-lead-cron.log 2>&1
#    b) OpenClaw cron job (if the new machine runs OpenClaw) — same command.
```

Update the URLs you hand out after moving (REST base, Swagger `/docs`,
MCP endpoint) — clients store the old IP otherwise.

## Auth state (important before sharing)

- **REST API**: `KB_API_KEY` in `.env` — currently **commented out (auth
  disabled, trial mode)**. Uncomment + `docker compose restart api` to
  re-enable `X-API-Key` checks.
- **MCP**: no auth yet — internal network only, read-only data. Add an auth
  provider before exposing past a firewall.

## Day-to-day operations

```bash
docker compose build                       # rebuild after code changes
docker compose --profile mcp up -d        # recreate api+mcp with new image
docker compose run --rm ingest             # manual digest run (any time)
docker compose run --rm ingest python -m src.main --config config.yaml --dry-run
                                          # test run — true no-op, DB untouched
docker logs paper-lead-api                 # REST logs
docker logs paper-lead-mcp                 # MCP logs
sqlite3 data/paper_lead.sqlite3 "SELECT COUNT(*) FROM papers;"
python3 -m src.fetch_pdfs --limit 30       # top up PDFs by hand
```

Bumping the version: edit `image: ppl:vX.Y.Z` in **all three** services in
`docker-compose.yml`, then `docker compose build && docker compose --profile mcp up -d`.

## Verification checklist

- `docker ps` shows `paper-lead-api` Up (healthy) and `paper-lead-mcp` Up
- `curl localhost:8000/health` → `{"status":"ok","papers":2255,...}`
- MCP client can initialize + list tools against `http://<ip>:9000/mcp`
- `pytest tests/` passes (run on the host)
- After a daily ingest: a new `digest/YYYY-MM-DD.md` appears,
  `digest_entries` count grows in `/health`, and `papers_pdf/` gains ~30 files
