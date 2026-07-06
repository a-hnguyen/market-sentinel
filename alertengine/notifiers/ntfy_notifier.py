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

    def _body(self, alert: Alert) -> str:
        parts = [alert.message]
        c = alert.context or {}
        if "pct_change" in c:
            parts.append(f"day {c['pct_change']:+.1f}%")
        if "volume_ratio" in c:
            parts.append(f"vol x{c['volume_ratio']:.1f}")
        return " | ".join(parts)

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
        # urllib is blocking; keep it off the event loop.
        await asyncio.to_thread(self._post, alert)
