"""Persist and deliver the human-readable pre-screen audit summary."""

import json
import os
from pathlib import Path
from urllib import request

from .screener import PreScreenReport


def _symbols(symbols: list[str]) -> str:
    return ", ".join(symbols) if symbols else "(none)"


def summary_messages(report: PreScreenReport) -> list[str]:
    final = [result.symbol for result in report.results]
    return [
        "**Pre-screen complete (regular session only)**\n"
        f"Added: {_symbols(report.added)}\n"
        f"Removed: {_symbols(report.removed)}",
        f"**4-hour RSI matches ({len(report.slow_matches)}):** "
        f"{_symbols(report.slow_matches)}",
        f"**1-hour RSI matches ({len(report.fast_matches)}):** "
        f"{_symbols(report.fast_matches)}",
        f"**Final intersection ({len(final)}):** {_symbols(final)}",
    ]


def save_report(report: PreScreenReport, path: str) -> None:
    payload = {
        "slow_matches": report.slow_matches,
        "fast_matches": report.fast_matches,
        "final": [result.symbol for result in report.results],
        "added": report.added,
        "removed": report.removed,
    }
    target = Path(path)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def load_report(path: str) -> dict[str, list[str]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def send_discord_summary(report: PreScreenReport) -> None:
    """Post through the existing bot identity; no webhook secret is needed."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        raise RuntimeError("Discord bot token/channel are not configured")
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    for message in summary_messages(report):
        body = json.dumps({"content": message}).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "market-sentinel-prescreen/1.0",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=15):
            pass
