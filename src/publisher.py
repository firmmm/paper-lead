"""Paper Lead — Publisher (Track B)

รับ DigestResult → บันทึกลงไฟล์ / ส่ง webhook
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

# โหลด .env
load_dotenv(Path(__file__).parent.parent / ".env")

# Allowed webhook domains
ALLOWED_WEBHOOK_DOMAINS = {
    "discord.com",
    "discordapp.com",
    "hooks.slack.com",
    "hooks.githubusercontent.com",
}


def _validate_webhook_url(url: str) -> bool:
    """ตรวจ webhook URL ว่าเป็น domain ที่อนุญาต (ป้องกัน SSRF)"""
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
    Publish digest ตาม config

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

    # 3. Send to Discord
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
    """บันทึก digest เป็น markdown file"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    filepath = out / f"{today}.md"

    # Add stats footer
    footer = f"\n\n---\n📊 Stats: {stats.get('total_fetched', 0)} scanned, {stats.get('total_filtered', 0)} relevant, {stats.get('must_read', 0)} must read"

    filepath.write_text(content + footer, encoding="utf-8")
    return filepath


def _send_slack(webhook_url: str, content: str, stats: dict) -> bool:
    """ส่ง digest ไป Slack webhook"""
    try:
        # Slack supports markdown in blocks
        payload = {
            "text": "📰 Paper Lead — Daily AI Research Digest",
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
                            "text": f"📊 {stats.get('total_fetched', 0)} scanned | {stats.get('must_read', 0)} must read",
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
    """ส่ง digest ไป Discord webhook"""
    try:
        prefix = "📰 **Paper Lead — Daily AI Research Digest**\n"
        # Discord 2000 char limit — คิด prefix length ด้วย
        max_content = 2000 - len(prefix)
        truncated = content[:max_content]
        if len(content) > max_content:
            truncated = truncated.rstrip() + "\n...(truncated)"

        payload = {"content": prefix + truncated}
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code != 204:
            logger.error(f"Discord webhook failed: status={resp.status_code}, body={resp.text[:200]}")
        return resp.status_code == 204
    except requests.RequestException as e:
        logger.error(f"Discord webhook error: {e}")
        return False


# --- CLI for testing ---
if __name__ == "__main__":
    import sys

    import yaml

    # รับ digest จากไฟล์หรือ stdin
    if len(sys.argv) > 1:
        digest_content = Path(sys.argv[1]).read_text()
    else:
        digest_content = "# 📰 Paper Lead — 2026-06-18\n\n🔥 **Must Read**\n- Test paper\n"

    # โหลด config
    config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    logging.basicConfig(level=logging.INFO)
    stats = {"total_fetched": 87, "total_filtered": 12, "must_read": 2, "worth_reading": 7, "tools": 3}
    results = publish_digest(digest_content, stats, config)
    print(f"Published: {json.dumps(results, indent=2)}")
