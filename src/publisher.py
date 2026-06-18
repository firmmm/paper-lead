"""Paper Lead - Publisher (Track B)

Receives DigestResult and saves to file / sends to webhook.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env
load_dotenv(Path(__file__).parent.parent / ".env")

# Allowed webhook domains (SSRF protection)
ALLOWED_WEBHOOK_DOMAINS = {
    "discord.com",
    "discordapp.com",
    "hooks.slack.com",
    "hooks.githubusercontent.com",
}


def _validate_webhook_url(url: str) -> bool:
    """Validate webhook URL against allowed domains (SSRF protection)."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("https",) and parsed.hostname in ALLOWED_WEBHOOK_DOMAINS
    except Exception:
        return False


def publish_digest(
    digest_content: str,
    stats: dict,
    config: dict,
) -> dict:
    """
    Publish digest according to config.

    Args:
        digest_content: markdown string
        stats: {total_fetched, total_filtered, must_read, worth_reading, tools}
        config: delivery config from config.yaml

    Returns:
        dict with published locations
    """
    delivery = config.get("delivery", {})
    results = {}

    # 1. Save to file
    if delivery.get("file", False):
        output_dir = config.get("digest", {}).get("output_dir", "digest")
        path = _save_to_file(digest_content, stats, output_dir)
        results["file"] = str(path)

    # 2. Send to Slack
    slack_webhook = delivery.get("slack", {}).get("webhook") or os.environ.get("SLACK_WEBHOOK")
    if slack_webhook:
        if not _validate_webhook_url(slack_webhook):
            logger.error(f"Invalid Slack webhook URL (domain not allowed): {slack_webhook}")
            results["slack"] = "failed: invalid URL"
        else:
            ok = _send_slack(slack_webhook, digest_content, stats)
            results["slack"] = "sent" if ok else "failed"

    # 3. Send to Discord (split into multiple messages if needed)
    discord_webhook = delivery.get("discord", {}).get("webhook") or os.environ.get("DISCORD_WEBHOOK")
    if discord_webhook:
        if not _validate_webhook_url(discord_webhook):
            logger.error(f"Invalid Discord webhook URL (domain not allowed): {discord_webhook}")
            results["discord"] = "failed: invalid URL"
        else:
            ok = _send_discord(discord_webhook, digest_content, stats)
            results["discord"] = "sent" if ok else "failed"

    return results


def _save_to_file(content: str, stats: dict, output_dir: str) -> Path:
    """Save digest as markdown file"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    filepath = out / f"{today}.md"

    # Add stats footer
    footer = f"\n\n---\nStats: {stats.get('total_fetched', 0)} scanned, {stats.get('total_filtered', 0)} relevant, {stats.get('must_read', 0)} must read"

    filepath.write_text(content + footer, encoding="utf-8")
    return filepath


def _send_slack(webhook_url: str, content: str, stats: dict) -> bool:
    """Send digest to Slack webhook"""
    try:
        payload = {
            "text": "Paper Lead - Daily AI Research Digest",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": content[:3000],  # Slack limit
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Stats: {stats.get('total_fetched', 0)} scanned | {stats.get('must_read', 0)} must read",
                        }
                    ],
                },
            ],
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Slack webhook failed: status={resp.status_code}, body={resp.text[:200]}")
        return resp.status_code == 200
    except requests.RequestException as e:
        logger.error(f"Slack webhook error: {e}")
        return False


def _send_discord(webhook_url: str, content: str, stats: dict) -> bool:
    """Send digest to Discord webhook, splitting into multiple messages if needed."""
    prefix = "**Paper Lead - Daily AI Research Digest**\n"
    max_per_msg = 2000

    # Split content into chunks that fit Discord's limit
    chunks: list[str] = []
    remaining = content
    first = True

    while remaining:
        available = max_per_msg - (len(prefix) if first else 0)
        if len(remaining) <= available:
            chunks.append(remaining)
            break
        # Split at newline to avoid cutting mid-line
        split_at = remaining.rfind("\n", 0, available)
        if split_at == -1:
            split_at = available
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
        first = False

    all_ok = True
    for i, chunk in enumerate(chunks):
        if i == 0:
            payload = {"content": prefix + chunk}
        else:
            payload = {"content": f"**(continued {i+1}/{len(chunks)})**\n" + chunk}

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code != 204:
                logger.error(f"Discord webhook failed (msg {i+1}/{len(chunks)}): status={resp.status_code}, body={resp.text[:200]}")
                all_ok = False
        except requests.RequestException as e:
            logger.error(f"Discord webhook error (msg {i+1}/{len(chunks)}): {e}")
            all_ok = False

    return all_ok


# --- CLI for testing ---
if __name__ == "__main__":
    import sys

    import yaml

    # Read digest from file or use sample
    if len(sys.argv) > 1:
        digest_content = Path(sys.argv[1]).read_text()
    else:
        digest_content = "# Paper Lead - 2026-06-18\n\n## Must Read\n- Test paper\n"

    # Load config
    config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(level=logging.INFO)
    stats = {"total_fetched": 87, "total_filtered": 12, "must_read": 2, "worth_reading": 7, "tools": 3}
    results = publish_digest(digest_content, stats, config)
    print(f"Published: {json.dumps(results, indent=2)}")
