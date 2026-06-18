"""Paper Lead — Publisher (Track B)

รับ DigestResult → บันทึกลงไฟล์ / ส่ง webhook
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

import requests
import yaml
from dotenv import load_dotenv

# โหลด .env
load_dotenv(Path(__file__).parent.parent / ".env")


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
    if webhook := delivery.get("slack", {}).get("webhook"):
        ok = _send_slack(webhook, digest_content, stats)
        results["slack"] = "sent" if ok else "failed"

    # 3. Send to Discord
    discord_webhook = delivery.get("discord", {}).get("webhook") or os.environ.get("DISCORD_WEBHOOK")
    if discord_webhook:
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
        return resp.status_code == 200
    except Exception:
        return False


def _send_discord(webhook_url: str, content: str, stats: dict) -> bool:
    """ส่ง digest ไป Discord webhook"""
    try:
        # Discord supports markdown
        payload = {
            "content": f"📰 **Paper Lead — Daily AI Research Digest**\n{content[:1900]}",  # Discord 2000 char limit
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception:
        return False


# --- CLI for testing ---
if __name__ == "__main__":
    import sys

    # รับ digest จากไฟล์หรือ stdin
    if len(sys.argv) > 1:
        digest_content = Path(sys.argv[1]).read_text()
    else:
        digest_content = "# 📰 Paper Lead — 2026-06-18\n\n🔥 **Must Read**\n- Test paper\n"

    # โหลด config
    config_path = Path(__file__).parent.parent / "config.example.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    stats = {"total_fetched": 87, "total_filtered": 12, "must_read": 2, "worth_reading": 7, "tools": 3}
    results = publish_digest(digest_content, stats, config)
    print(f"Published: {json.dumps(results, indent=2)}")
