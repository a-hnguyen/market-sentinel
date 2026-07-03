"""Notifier that prints alerts to the console and appends them to a log file.

The log file is git-ignored (*.log). Swapping in SMS/email/Slack later is just
another Notifier implementation behind the same interface.
"""

import logging

from ..interfaces import Notifier
from ..models import Alert


class ConsoleNotifier(Notifier):
    def __init__(self, logfile: str = "alerts.log") -> None:
        self._log = logging.getLogger("alertengine.alerts")
        self._log.setLevel(logging.INFO)
        # Avoid duplicate handlers if constructed more than once.
        if not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "").endswith(logfile)
            for h in self._log.handlers
        ):
            handler = logging.FileHandler(logfile)
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self._log.addHandler(handler)

    async def send(self, alert: Alert) -> None:
        line = f"[ALERT {alert.timestamp:%H:%M}] {alert.symbol}: {alert.message}"
        print(line)
        self._log.info("%s %s %s", alert.symbol, alert.rule, alert.context)
