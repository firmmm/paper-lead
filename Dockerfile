FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + static config
COPY src/ src/
COPY prompts/ prompts/
COPY config.example.yaml interests.yaml ./

# Match host saiuser uid:gid so bind-mount writes (data/, digest/)
# land as saiuser-owned files on the host.
# NOTE: TZ stays unset (UTC) — ingest dates must match host behavior.
USER 1001:1001

# api service default; ingest overrides via compose `command`
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
