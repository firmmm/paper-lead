# Deploy Guide

Paper Lead runs as two Docker services from one image (`ppl:v0.1.0`):

- **api** — long-running KB API (port 8000). `restart: unless-stopped`, so it
  comes back by itself after a host reboot (docker.service must be enabled).
- **ingest** — one-shot daily worker (digest pipeline + KB refresh), started
  by cron: `docker compose run --rm ingest`.

State (SQLite DB, digest markdown, secrets) lives on the **host** via bind
mounts — nothing important lives inside a container.

```
data/paper_lead.sqlite3   # KB: papers + digest_days + digest_entries + FTS
digest/*.md               # daily digests (source of truth for KB tables)
.env                      # GATEWAY_API_KEY, KB_API_KEY, webhooks
config.yaml               # main config (gitignored)
interests.yaml            # ranker keywords
```

## Same machine, after reboot

Nothing to do. Verify:

```bash
docker ps                              # paper-lead-api should be Up (healthy)
curl -s http://localhost:8000/health   # {"status":"ok",...}
```

Only exception: if you ran `docker compose stop` yourself before the reboot,
start it again with `docker compose up -d api`.

## Move to a new machine

```bash
# 1. On the new machine: install docker + compose plugin, then:
rsync -av oldmachine:~/paper-lead/ ~/paper-lead/
#    (or: git clone + copy the 4 gitignored state items listed above)

cd ~/paper-lead
# 2. OPTIONAL: if your uid is not 1001, edit the USER line in Dockerfile
#    to match (else container-written files will be owned by uid 1001).
# 3. Build and start:
docker compose build
docker compose up -d api
curl -s http://localhost:8000/health

# 4. Daily ingest — pick one:
#    a) plain crontab (09:00 Bangkok = 02:00 UTC):
crontab -e
0 2 * * * cd $HOME/paper-lead && docker compose run --rm ingest >> /tmp/paper-lead-cron.log 2>&1
#    b) OpenClaw cron job (if the new machine runs OpenClaw) — same command.
```

## Day-to-day operations

```bash
docker compose build                  # rebuild after code changes
docker compose up -d api              # recreate api with the new image
docker compose run --rm ingest        # manual digest run (any time)
docker compose run --rm ingest python -m src.main --config config.yaml --dry-run
                                      # test run — true no-op, DB untouched
docker logs paper-lead-api            # api logs
sqlite3 data/paper_lead.sqlite3 "SELECT COUNT(*) FROM papers;"
```

Bumping the version: edit `image: ppl:vX.Y.Z` in **both** services in
`docker-compose.yml`, then `docker compose build && docker compose up -d api`.

## Verification checklist

- `docker ps` shows `paper-lead-api` Up (healthy)
- `curl localhost:8000/health` → `{"status":"ok","papers":2255,...}`
- `pytest tests/` passes (run on the host)
- After a daily ingest: a new `digest/YYYY-MM-DD.md` appears and
  `digest_entries` count grows in `/health`
