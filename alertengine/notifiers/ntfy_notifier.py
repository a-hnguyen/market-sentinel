"""Push alerts to phone + desktop via ntfy (https://ntfy.sh).

ntfy is a simple pub/sub push service: every device subscribed to a topic (iOS
app, Android app, Windows/desktop web app) receives a notification when we POST
to that topic. One POST fans out to all of them — so a single alert reaches the
phone (lock-screen push, good for AFK) and the desktop at once.

Config comes from the environment (load a .env first):
  NTFY_TOPIC   required — the topic name every device subscribes to
  NTFY_SERVER  optional — defaults to https://ntfy.sh (set for self-hosted)
  NTFY_TOKEN   optional — access token for a private/reserved topic

Uses stdlib urllib (no extra dependency) and posts off the event loop. A push
failure is logged, never raised, so it can't kill the watch loop.
"""

import asyncio
import logging
import os
import urllib.request

from ..interfaces import Notifier
from ..models import Alert


class NtfyNotifier(Notifier):
    def __init__(
        self,
        topic: str | None = None,
        server: str | None = None,
        token: str | None = None,
    ) -> None:
        self._topic = topic or os.environ.get("NTFY_TOPIC")
        if not self._topic:
            raise RuntimeError(
                "Missing ntfy topic: set NTFY_TOPIC (e.g. in a .env file)."
            )
        self._server = (
            server or os.environ.get("NTFY_SERVER") or "https://ntfy.sh"
        ).rstrip("/")
        self._token = token or os.environ.get("NTFY_TOKEN")
        self._log = logging.getLogger("alertengine.ntfy")

    @staticmethod
    def _row(label: str, value: str) -> str:
        # "label:" left-padded so the colons line up in a monospace client.
        return f"{label + ':':<9}{value}"

    def _body(self, alert: Alert) -> str:
        # Phone push fonts are proportional, so one field per line (with a colon
        # delimiter) reads far better than space-aligned columns on one line.
        c = alert.context or {}
        if {"close", "bb_lower", "rsi"} <= c.keys():
            lines = [
                self._row("close", f"{c['close']:.2f}"),
                self._row("BB low", f"{c['bb_lower']:.2f}"),
                self._row("RSI", f"{c['rsi']:.1f}"),
            ]
            if "pct_change" in c:
                lines.append(self._row("day", f"{c['pct_change']:+.1f}%"))
            if "volume_ratio" in c:
                lines.append(self._row("rel vol", f"x{c['volume_ratio']:.1f}"))
            return "\n".join(lines)
        return alert.message

    def _post(self, alert: Alert) -> None:
        req = urllib.request.Request(
            f"{self._server}/{self._topic}",
            data=self._body(alert).encode("utf-8"),
            method="POST",
        )
        # HTTP headers are latin-1 only, so keep the Title ASCII (no em-dash).
        req.add_header("Title", f"{alert.symbol} - layer-1 alert")
        req.add_header("Priority", "high")  # break through even when AFK
        req.add_header("Tags", "chart_with_downwards_trend")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:  # network/auth error must not kill the watch loop
            self._log.warning("ntfy push failed: %s", e)
            print(f"[ntfy] push failed: {e}")

    async def send(self, alert: Alert) -> None:
        # Phone buzzes only on the actionable BUY confirmation. "watch" alerts
        # (a setup merely arming) stay console-only to avoid AFK noise.
        if getattr(alert, "kind", "alert") == "watch":
            return
        # urllib is blocking; keep it off the event loop.
        await asyncio.to_thread(self._post, alert)
