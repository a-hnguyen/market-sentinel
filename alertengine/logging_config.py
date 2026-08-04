"""Central logging configuration for local runs and production services."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FIELD = re.compile(r"(?:^|\s)([a-z][a-z0-9_]*)=([^\s]+)")


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for CloudWatch Logs Insights."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Event messages use stable key=value fields. Promote them to native JSON
        # properties so Logs Insights can filter on `event`, `symbol`, etc.
        payload.update(dict(_FIELD.findall(record.getMessage())))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(component: str) -> None:
    """Configure console logging plus an optional rotating production file."""
    root = logging.getLogger()
    if getattr(root, "_market_sentinel_configured", False):
        return

    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = _JsonFormatter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    log_dir = os.environ.get("MARKET_SENTINEL_LOG_DIR", "").strip()
    if log_dir:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            directory / f"{component}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    root.setLevel(level)
    root._market_sentinel_configured = True  # type: ignore[attr-defined]
